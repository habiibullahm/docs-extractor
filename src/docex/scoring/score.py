"""Combine model confidence and validation results into a document score, then route."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from docex.config import pipeline_config, validation_config
from docex.validate.rules import Issue, get_path


@dataclass
class ScoreResult:
    score: float
    min_confidence: float
    weakest_field: Optional[str]
    n_errors: int
    n_warnings: int
    route: str  # "auto_approved" | "review"
    reason: str


def field_confidence(extracted: dict[str, Any], path: str) -> float:
    node = get_path(extracted, path)
    if isinstance(node, dict) and "value" in node:
        return float(node.get("confidence", 0.0)) if node.get("value") is not None else 0.0
    return 0.0


def default_threshold(approach: str, cfg: Optional[dict[str, Any]] = None) -> float:
    cfg = cfg or pipeline_config()["scoring"]
    return float(cfg["thresholds"].get(approach.upper(), 0.9))


def score_document(doc_type: str, extracted: dict[str, Any], issues: list[Issue], classification_conf: float,
                   threshold: Optional[float] = None, cfg: Optional[dict[str, Any]] = None,
                   approach: str = "C") -> ScoreResult:
    cfg = cfg or pipeline_config()["scoring"]
    threshold = default_threshold(approach, cfg) if threshold is None else threshold
    critical = validation_config()["critical_fields"].get(doc_type, [])
    confs = {p: field_confidence(extracted, p) for p in critical}
    items = extracted.get("line_items") or []
    item_confs = [float(it.get("total", {}).get("confidence", 0)) for it in items if isinstance(it, dict)]
    if item_confs:
        confs["line_items"] = min(item_confs)
    weakest = min(confs, key=confs.get) if confs else None
    min_conf = confs[weakest] if weakest else 0.0
    n_err = sum(1 for i in issues if i.severity == "error")
    n_warn = sum(1 for i in issues if i.severity == "warning")
    score = min(min_conf, classification_conf)
    if n_err:
        score *= cfg["error_penalty"]
    score *= cfg["warning_penalty"] ** n_warn
    score = round(score, 4)
    if extracted.get("document_inconsistent"):
        route, reason = "review", "model flagged the document as internally inconsistent"
    elif score >= threshold:
        route, reason = "auto_approved", f"score {score:.2f} >= threshold {threshold:.2f}"
    elif n_err:
        route, reason = "review", f"{n_err} validation error(s)"
    else:
        route, reason = "review", f"low confidence on '{weakest}' ({min_conf:.2f})"
    return ScoreResult(score, min_conf, weakest, n_err, n_warn, route, reason)


def route_for(score: float, threshold: float, inconsistent: bool = False) -> str:
    return "auto_approved" if score >= threshold and not inconsistent else "review"
