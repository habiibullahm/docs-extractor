"""FastAPI service.

POST /documents          upload -> job id
GET  /documents/{id}     status + result
GET  /documents/{id}/preview   first page as PNG (for the review UI)
GET  /review/queue       documents routed to human review
POST /review/{id}        approve / correct / reject (corrections become new eval data)
GET  /metrics            live operational metrics + latest offline eval summary
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal, Optional

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, select

from docex.config import DATA_DIR, ROOT, pipeline_config
from docex.db.models import Document, Extraction, Review, get_document, init_db, review_queue, session
from docex.observability import configure_logging, get_logger
from docex.preprocess.image import load_pages
from docex.providers.llm import llm_available
from docex.worker.tasks import enqueue

CFG = pipeline_config()
LIMITS = CFG["limits"]
STORAGE = Path(os.environ.get("DOCEX_STORAGE_DIR", DATA_DIR / "uploads"))
MAGIC = {b"%PDF-": "application/pdf", b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg",
         b"RIFF": "image/webp", b"II*\x00": "image/tiff", b"MM\x00*": "image/tiff"}

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
log = get_logger("docex.api")
limiter = Limiter(key_func=get_remote_address, default_limits=[])
app = FastAPI(title="docex — Indonesian financial document extraction", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def _startup() -> None:
    STORAGE.mkdir(parents=True, exist_ok=True)
    init_db()


def default_approach() -> str:
    """C when a VLM is configured and has credentials, otherwise the local baseline A."""
    env = os.environ.get("DOCEX_APPROACH")
    if env:
        return env.upper()
    return "C" if llm_available(CFG["vlm"]) else "A"


def sniff(data: bytes) -> Optional[str]:
    for magic, mime in MAGIC.items():
        if data.startswith(magic):
            return mime
    return None


def _doc_out(doc: Document) -> dict[str, Any]:
    ex = doc.latest
    out: dict[str, Any] = {"id": doc.id, "status": doc.status, "filename": doc.filename, "approach": doc.approach,
                           "created_at": doc.created_at.isoformat(), "error": doc.error}
    if ex:
        r = ex.result
        out.update(doc_type=ex.doc_type, route=ex.route, score=ex.score, route_reason=r.get("route_reason"),
                   extraction=r.get("extraction"), values=r.get("values"), issues=r.get("issues"),
                   repaired=r.get("repaired"), versions=ex.versions, usage=r.get("usage"),
                   latency_ms=ex.latency_ms, trace=r.get("trace"))
    if doc.reviews:
        rv = doc.reviews[-1]
        out["review"] = {"action": rv.action, "reviewer": rv.reviewer, "changed_fields": rv.changed_fields,
                         "corrected_values": rv.corrected_values, "created_at": rv.created_at.isoformat()}
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    return {"ok": True, "default_approach": default_approach()}


@app.post("/documents", status_code=202)
@limiter.limit(LIMITS["rate_limit"])
async def upload(request: Request, file: UploadFile = File(...),
                 approach: Optional[Literal["A", "B", "C"]] = Form(None),
                 doc_type: Optional[Literal["faktur_pajak", "invoice", "receipt"]] = Form(None)) -> dict[str, Any]:
    max_bytes = int(LIMITS["max_upload_mb"] * 1024 * 1024)
    data = await file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise HTTPException(413, f"file larger than {LIMITS['max_upload_mb']} MB")
    mime = sniff(data)
    if mime is None or mime not in LIMITS["allowed_types"]:
        raise HTTPException(415, f"unsupported file type; allowed: {LIMITS['allowed_types']}")
    approach = approach or default_approach()
    if approach in ("B", "C") and not llm_available(CFG["llm" if approach == "B" else "vlm"]):
        raise HTTPException(400, f"approach {approach} is not available on this deployment (no model credentials)")
    return _create(data, file.filename or "upload", mime, approach, doc_type)


def _create(data: bytes, filename: str, mime: str, approach: str, doc_type: Optional[str]) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
           "image/tiff": ".tif"}[mime]
    with session() as s:
        doc = Document(filename=filename[:255], content_type=mime, sha256=digest, size_bytes=len(data),
                       storage_path="", approach=approach, doc_type_hint=doc_type)
        s.add(doc)
        s.flush()
        path = STORAGE / f"{doc.id}{ext}"
        path.write_bytes(data)
        doc.storage_path = str(path)
        doc_id = doc.id
    enqueue(doc_id)
    log.info("document_received", document_id=doc_id, mime=mime, size=len(data), approach=approach)
    return {"job_id": doc_id, "status": "queued", "approach": approach}


@app.get("/documents/{doc_id}")
def get_doc(doc_id: str) -> dict[str, Any]:
    with session() as s:
        doc = get_document(s, doc_id)
        if not doc:
            raise HTTPException(404, "not found")
        return _doc_out(doc)


@app.get("/documents/{doc_id}/preview")
def preview(doc_id: str) -> Response:
    with session() as s:
        doc = get_document(s, doc_id)
        if not doc:
            raise HTTPException(404, "not found")
        path = doc.storage_path
    pages, _ = load_pages(Path(path).read_bytes(), dpi=110)
    ok, buf = cv2.imencode(".png", pages[0])
    return Response(buf.tobytes(), media_type="image/png")


@app.get("/documents")
def list_docs(limit: int = 50) -> list[dict[str, Any]]:
    with session() as s:
        docs = s.scalars(select(Document).order_by(Document.created_at.desc()).limit(min(limit, 200))).all()
        return [{"id": d.id, "filename": d.filename, "status": d.status,
                 "route": d.latest.route if d.latest else None, "score": d.latest.score if d.latest else None,
                 "doc_type": d.latest.doc_type if d.latest else None, "reviewed": bool(d.reviews)} for d in docs]


@app.get("/review/queue")
def get_queue(limit: int = 50) -> list[dict[str, Any]]:
    with session() as s:
        return [{"id": d.id, "filename": d.filename, "doc_type": d.latest.doc_type, "score": d.latest.score,
                 "route_reason": d.latest.result.get("route_reason"), "created_at": d.created_at.isoformat()}
                for d in review_queue(s, limit)]


class ReviewIn(BaseModel):
    action: Literal["approve", "correct", "reject"]
    values: Optional[dict[str, Any]] = None
    reviewer: str = "anonymous"
    notes: Optional[str] = None


def _diff(a: Any, b: Any, prefix: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            out += _diff(a.get(k), b.get(k), f"{prefix}{k}.")
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [x for i, (p, q) in enumerate(zip(a, b)) for x in _diff(p, q, f"{prefix}{i}.")]
    return [] if a == b else [prefix.rstrip(".")]


@app.post("/review/{doc_id}")
def submit_review(doc_id: str, body: ReviewIn) -> dict[str, Any]:
    with session() as s:
        doc = get_document(s, doc_id)
        if not doc or not doc.latest:
            raise HTTPException(404, "document not found or not processed yet")
        ex = doc.latest
        changed: list[str] = []
        values = None
        if body.action == "correct":
            if not body.values:
                raise HTTPException(422, "values are required for action=correct")
            from docex.normalize.document import normalize_document
            from docex.schemas.documents import plain

            # re-normalize human input so stored corrections have the same format as ground truth
            try:
                values = plain(normalize_document(ex.doc_type, body.values))
            except ValueError as e:
                raise HTTPException(422, f"invalid corrected values: {e}") from e
            values.pop("doc_type", None)
            changed = _diff(ex.result.get("values") or {}, values)
        s.add(Review(document_id=doc_id, extraction_id=ex.id, action=body.action, reviewer=body.reviewer[:100],
                     corrected_values=values, changed_fields=changed, notes=body.notes))
    log.info("review_submitted", document_id=doc_id, action=body.action, changed_fields=changed)
    return {"ok": True, "changed_fields": changed}


@app.get("/metrics")
def metrics() -> dict[str, Any]:
    with session() as s:
        n_docs = s.scalar(select(func.count(Document.id))) or 0
        by_status = dict(s.execute(select(Document.status, func.count()).group_by(Document.status)).all())
        latest_ids = select(func.max(Extraction.id)).group_by(Extraction.document_id).scalar_subquery()
        rows = s.execute(select(Extraction.route, Extraction.doc_type, Extraction.cost_usd, Extraction.latency_ms,
                                Extraction.approach).where(Extraction.id.in_(latest_ids))).all()
        reviews = dict(s.execute(select(Review.action, func.count()).group_by(Review.action)).all())
    lat = sorted(r.latency_ms for r in rows)
    by_route: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for r in rows:
        by_route[r.route] = by_route.get(r.route, 0) + 1
        by_type[r.doc_type] = by_type.get(r.doc_type, 0) + 1
    offline = {}
    summary = ROOT / "eval" / "reports" / "summary.json"
    if summary.exists():
        offline = json.loads(summary.read_text())
    return {
        "documents": n_docs, "by_status": by_status, "by_route": by_route, "by_doc_type": by_type,
        "auto_approve_rate": by_route.get("auto_approved", 0) / len(rows) if rows else None,
        "latency_ms_p50": lat[len(lat) // 2] if lat else None,
        "latency_ms_p95": lat[int(len(lat) * 0.95) - 1] if lat else None,
        "cost_usd_total": round(sum(r.cost_usd for r in rows), 4),
        "reviews": reviews,
        "default_approach": default_approach(),
        "offline_eval": offline,
    }


# ------------------------------------------------------------------ demo helpers

SAMPLES_DIR = Path(os.environ.get("DOCEX_SAMPLES_DIR", ROOT / "demo_samples"))


@app.get("/samples")
def samples() -> list[dict[str, Any]]:
    if not (SAMPLES_DIR / "index.json").exists():
        return []
    return json.loads((SAMPLES_DIR / "index.json").read_text())


@app.post("/samples/{name}", status_code=202)
@limiter.limit(LIMITS["rate_limit"])
def run_sample(request: Request, name: str, approach: Optional[Literal["A", "B", "C"]] = None) -> dict[str, Any]:
    index = {x["name"]: x for x in samples()}
    if name not in index:
        raise HTTPException(404, "unknown sample")
    path = SAMPLES_DIR / index[name]["file"]
    data = path.read_bytes()
    approach = approach or default_approach()
    if approach in ("B", "C") and not llm_available(CFG["llm" if approach == "B" else "vlm"]):
        raise HTTPException(400, f"approach {approach} is not available on this deployment")
    return _create(data, path.name, sniff(data) or "image/jpeg", approach, None)
