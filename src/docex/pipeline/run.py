"""End-to-end pipeline: ingest -> preprocess -> classify -> extract -> normalize -> validate -> score -> route."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Optional

from docex.classify.classifier import Classification, classify_llm, classify_text
from docex.config import pipeline_config
from docex.extract.baseline import extract_baseline
from docex.extract.llm_extract import content_from_images, content_from_ocr, extract_llm, repair_llm
from docex.normalize.document import normalize_document
from docex.observability import Trace, get_logger
from docex.preprocess.image import PreparedDocument, preprocess, rotate_quadrant, to_jpeg_bytes
from docex.providers.llm import LLMProvider, ProviderError, Usage, get_llm
from docex.providers.ocr import OcrResult, get_ocr
from docex.schemas.documents import model_for, plain
from docex.scoring.score import score_document
from docex.validate.rules import Issue, Validator

log = get_logger("docex.pipeline")
LOW_OCR_CONF = 0.6


@dataclass
class PipelineResult:
    doc_type: str
    classification: dict[str, Any]
    extraction: Optional[dict[str, Any]]           # with confidences
    values: Optional[dict[str, Any]]               # plain values
    issues: list[dict[str, Any]]
    score: float
    route: str
    route_reason: str
    repaired: bool
    approach: str
    versions: dict[str, str]
    usage: dict[str, Any]
    latency_ms: float
    trace: list[dict[str, Any]]
    error: Optional[str] = None
    first_pass_issues: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class Pipeline:
    def __init__(self, approach: Optional[str] = None, cfg: Optional[dict[str, Any]] = None,
                 llm: Optional[LLMProvider] = None, vlm: Optional[LLMProvider] = None,
                 validator: Optional[Validator] = None) -> None:
        self.cfg = cfg or pipeline_config()
        self.approach = (approach or self.cfg["approach"]).upper()
        self._llm, self._vlm = llm, vlm
        self.validator = validator or Validator()

    # lazily construct providers so approach A never needs API credentials
    @property
    def llm(self) -> LLMProvider:
        if self._llm is None:
            self._llm = get_llm(self.cfg["llm"], self.cfg)
        return self._llm

    @property
    def vlm(self) -> LLMProvider:
        if self._vlm is None:
            self._vlm = get_llm(self.cfg["vlm"], self.cfg)
        return self._vlm

    def _ocr(self, doc: PreparedDocument, trace: Trace) -> OcrResult:
        ocr = get_ocr(self.cfg["ocr"])
        with trace.span("ocr", engine=ocr.name) as s:
            res = ocr.ocr(doc.image)
            # orientation fallback: OSD is unreliable on degraded photos, so if the
            # OCR confidence is poor, try the other quadrants and keep the best read
            if res.mean_confidence < LOW_OCR_CONF and doc.source_kind == "image":
                for deg in (180, 90, 270):
                    alt = ocr.ocr(rotate_quadrant(doc.image, deg))
                    if alt.mean_confidence > res.mean_confidence + 0.1:
                        res = alt
                        doc.pages = [rotate_quadrant(p, deg) for p in doc.pages]
                        s.attrs["orientation_fallback"] = deg
                        break
            s.attrs.update(lines=len(res.lines), mean_conf=round(res.mean_confidence, 3))
        return res

    def run(self, data: bytes, doc_type_hint: Optional[str] = None, threshold: Optional[float] = None) -> PipelineResult:
        trace = Trace()
        t0 = time.perf_counter()
        usage: Optional[Usage] = None
        versions = {"approach": self.approach, "pipeline_config": "configs/pipeline.yaml"}
        ocr_res: Optional[OcrResult] = None
        repaired = False
        first_issues: list[Issue] = []
        try:
            with trace.span("preprocess") as s:
                doc = preprocess(data, self.cfg["preprocess"])
                s.attrs.update(doc.steps)
            if self.approach in ("A", "B"):
                ocr_res = self._ocr(doc, trace)
                versions["ocr"] = getattr(get_ocr(self.cfg["ocr"]), "version", self.cfg["ocr"]["provider"])

            with trace.span("classify") as s:
                if doc_type_hint:
                    cls = Classification(doc_type_hint, 1.0, "hint")
                elif ocr_res is not None:
                    cls = classify_text(ocr_res.text)
                else:
                    cls = classify_llm(self.vlm, [*content_from_images([to_jpeg_bytes(doc.image, 1024)])[:1],
                                                  "Classify this document."])
                    usage = cls.usage
                s.attrs.update(doc_type=cls.doc_type, confidence=cls.confidence, method=cls.method)
            if cls.doc_type == "other":
                return self._result(cls, None, [], 0.0, "review", "unsupported document type", False, versions,
                                    usage, t0, trace)

            # ---- extract
            content = None
            with trace.span("extract", approach=self.approach) as s:
                if self.approach == "A":
                    raw = extract_baseline(cls.doc_type, ocr_res)
                    versions["extractor"] = "regex_v1"
                else:
                    provider = self.llm if self.approach == "B" else self.vlm
                    content = (content_from_ocr(ocr_res.text) if self.approach == "B"
                               else content_from_images([to_jpeg_bytes(p, 1600) for p in doc.pages]))
                    resp, pv = extract_llm(provider, cls.doc_type, content, self.cfg["prompts"]["extract"])
                    raw = resp.data
                    usage = resp.usage if usage is None else usage + resp.usage
                    versions.update(model=provider.model, provider=provider.name, prompt=pv)
                    s.attrs.update(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)

            with trace.span("normalize"):
                model = normalize_document(cls.doc_type, raw)
            with trace.span("validate") as s:
                issues = self.validator.validate(cls.doc_type, plain(model))
                s.attrs.update(errors=[i.rule for i in issues if i.severity == "error"])

            # ---- repair pass (LLM approaches only, once)
            errors = [i for i in issues if i.severity == "error" and i.rule != "required_fields"]
            if errors and self.approach in ("B", "C") and self.cfg["repair"]["enabled"]:
                first_issues = issues
                provider = self.llm if self.approach == "B" else self.vlm
                with trace.span("repair") as s:
                    resp, rv = repair_llm(provider, cls.doc_type, content, model.model_dump(exclude={"doc_type"}),
                                          [i.message for i in errors], self.cfg["prompts"]["extract"],
                                          self.cfg["prompts"]["repair"])
                    usage = usage + resp.usage if usage else resp.usage
                    versions["repair_prompt"] = rv
                    model = normalize_document(cls.doc_type, resp.data)
                    issues = self.validator.validate(cls.doc_type, plain(model))
                    repaired = True
                    s.attrs.update(errors_after=[i.rule for i in issues if i.severity == "error"])

            with trace.span("score"):
                extracted = model.model_dump()
                sr = score_document(cls.doc_type, extracted, issues, cls.confidence, threshold, self.cfg["scoring"],
                                    approach=self.approach)
            res = self._result(cls, model, issues, sr.score, sr.route, sr.reason, repaired, versions, usage, t0,
                               trace, first_issues)
        except ProviderError as e:
            res = self._error(str(e), versions, usage, t0, trace)
        log.info("document_processed", trace_id=trace.trace_id, approach=self.approach, doc_type=res.doc_type,
                 route=res.route, score=res.score, repaired=res.repaired, versions=versions,
                 tokens_in=res.usage.get("input_tokens"), tokens_out=res.usage.get("output_tokens"),
                 cost_usd=res.usage.get("cost_usd"), latency_ms=res.latency_ms,
                 validation=[i["rule"] for i in res.issues], error=res.error)
        return res

    def _usage_dict(self, usage: Optional[Usage], latency_s: float) -> dict[str, Any]:
        cpu_cost = latency_s / 3600 * self.cfg["costs"]["cpu_usd_per_hour"]
        api_cost = usage.cost_usd if usage else 0.0
        return {
            "model": usage.model if usage else None,
            "input_tokens": usage.input_tokens if usage else 0,
            "output_tokens": usage.output_tokens if usage else 0,
            "llm_calls": usage.calls if usage else 0,
            "api_cost_usd": round(api_cost, 6),
            "compute_cost_usd": round(cpu_cost, 6),
            "cost_usd": round(api_cost + cpu_cost, 6),
            "cost_idr": round((api_cost + cpu_cost) * self.cfg["costs"]["usd_to_idr"], 2),
        }

    def _result(self, cls: Classification, model, issues: list[Issue], score: float, route: str, reason: str,
                repaired: bool, versions: dict[str, str], usage: Optional[Usage], t0: float, trace: Trace,
                first_issues: Optional[list[Issue]] = None) -> PipelineResult:
        latency = time.perf_counter() - t0
        extracted = model.model_dump() if model is not None else None
        return PipelineResult(
            doc_type=cls.doc_type,
            classification={"doc_type": cls.doc_type, "confidence": cls.confidence, "method": cls.method},
            extraction=extracted, values=plain(extracted) if extracted else None,
            issues=[i.to_dict() for i in issues], score=score, route=route, route_reason=reason,
            repaired=repaired, approach=self.approach, versions=versions,
            usage=self._usage_dict(usage, latency), latency_ms=round(latency * 1000, 1), trace=trace.to_list(),
            first_pass_issues=[i.to_dict() for i in (first_issues or [])],
        )

    def _error(self, msg: str, versions: dict[str, str], usage: Optional[Usage], t0: float,
               trace: Trace) -> PipelineResult:
        latency = time.perf_counter() - t0
        return PipelineResult("other", {}, None, None, [], 0.0, "review", f"pipeline error: {msg}", False,
                              self.approach, versions, self._usage_dict(usage, latency), round(latency * 1000, 1),
                              trace.to_list(), error=msg)


def empty_model(doc_type: str):
    return model_for(doc_type)()
