"""FastAPI service.

POST /documents          upload -> job id
GET  /documents/{id}     status + result
GET  /documents/{id}/preview   first page as PNG (for the review UI)
GET  /review/queue       documents routed to human review
POST /review/{id}        approve / correct / reject (corrections become new eval data)
GET  /metrics            live operational metrics + latest offline eval summary
GET  /                   review UI (static app in web/, see design.md)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import cv2
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import func, select

from docex.api.session import client_ip, current_sid, session_middleware
from docex.config import DATA_DIR, ROOT, pipeline_config, validation_config
from docex.db.models import Document, Extraction, LlmUsage, Review, get_document, init_db, review_queue, session
from docex.db.retention import purge_loop
from docex.observability import configure_logging, get_logger
from docex.preprocess.image import load_pages
from docex.providers.llm import llm_available, llm_paused_seconds
from docex.validate.rules import get_path
from docex.worker.tasks import enqueue, read_page_path

CFG = pipeline_config()
LIMITS = CFG["limits"]
STORAGE = Path(os.environ.get("DOCEX_STORAGE_DIR", DATA_DIR / "uploads"))
MAGIC = {b"%PDF-": "application/pdf", b"\x89PNG": "image/png", b"\xff\xd8\xff": "image/jpeg",
         b"RIFF": "image/webp", b"II*\x00": "image/tiff", b"MM\x00*": "image/tiff"}

configure_logging(os.environ.get("LOG_LEVEL", "INFO"))
log = get_logger("docex.api")
limiter = Limiter(key_func=client_ip, default_limits=[])
app = FastAPI(title="docex — Indonesian financial document extraction", version="0.1.0")
app.state.limiter = limiter
app.middleware("http")(session_middleware)
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.on_event("startup")
def _startup() -> None:
    STORAGE.mkdir(parents=True, exist_ok=True)
    init_db()


@app.on_event("startup")
async def _start_cleaner() -> None:
    app.state.purge_task = asyncio.create_task(purge_loop())


@app.on_event("shutdown")
async def _stop_cleaner() -> None:
    app.state.purge_task.cancel()


def default_approach() -> str:
    """The local baseline A unless DOCEX_APPROACH says otherwise: B and C use a rate-limited free-tier key."""
    env = os.environ.get("DOCEX_APPROACH")
    return env.upper() if env else "A"


def _require_available(approach: str) -> None:
    section = {"B": CFG["llm"], "C": CFG["vlm"]}.get(approach)
    if section is None or llm_available(section):
        return
    if llm_paused_seconds(section):
        raise HTTPException(400, f"approach {approach} is paused for now (free quota used up or location rejected); "
                                 f"try again in about {llm_paused_seconds(section)} s or use approach A")
    raise HTTPException(400, f"approach {approach} is not available on this deployment (no model credentials)")


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
        # boxes are positions on the page OCR read, so only offer them when that page is what /preview serves
        out["sources"] = r.get("sources") if read_page_path(doc.storage_path).exists() else None
    if doc.reviews:
        rv = doc.reviews[-1]
        out["review"] = {"action": rv.action, "reviewer": rv.reviewer, "changed_fields": rv.changed_fields,
                         "corrected_values": rv.corrected_values, "created_at": rv.created_at.isoformat()}
    return out


@app.get("/health")
def health() -> dict[str, Any]:
    from docex.scoring.score import default_threshold

    sections = {"A": None, "B": CFG["llm"], "C": CFG["vlm"]}
    available = [a for a, s in sections.items() if s is None or llm_available(s)]
    paused = {a: llm_paused_seconds(s) for a, s in sections.items() if s is not None and llm_paused_seconds(s)}
    daily = {"limit": int(limits()["llm_daily_docs"]), "used": llm_docs_today()}
    if daily["used"] >= daily["limit"]:  # B and C share the free Gemini quota: stop offering them for today
        until_midnight = int((_utc_midnight() + timedelta(days=1) - datetime.now(timezone.utc)).total_seconds())
        for a in [a for a in available if a != "A"]:
            available.remove(a)
            paused[a] = max(paused.get(a, 0), until_midnight)
    return {"ok": True, "default_approach": default_approach(),
            "available_approaches": available, "paused_approaches": paused, "llm_daily": daily,
            "session_max_docs": int(limits()["session_max_docs"]),
            "thresholds": {a: default_threshold(a, CFG["scoring"]) for a in sections},
            "critical_fields": validation_config()["critical_fields"],
            "models": {"B": CFG["llm"]["model"], "C": CFG["vlm"]["model"]},
            "max_upload_mb": LIMITS["max_upload_mb"],
            "retention_minutes": int(pipeline_config()["limits"]["retention_minutes"])}


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
    _require_available(approach)
    return _create(data, file.filename or "upload", mime, approach, doc_type, current_sid(request))


def limits() -> dict[str, Any]:
    """Read per request so DOCEX_LIMITS__* overrides apply without a reload."""
    return pipeline_config()["limits"]


def _utc_midnight() -> datetime:
    return datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def llm_docs_today() -> int:
    with session() as s:
        row = s.get(LlmUsage, _utc_day())
        return row.count if row else 0


def _check_caps(sid: str, approach: str, size: int) -> None:
    """Refuse an upload that would break a demo limit: per-visitor count, total stored bytes, daily B/C use."""
    lim = limits()
    window = datetime.now(timezone.utc) - timedelta(minutes=int(lim["retention_minutes"]))
    with session() as s:
        mine = s.scalar(select(func.count(Document.id)).where(
            Document.owner_id == sid, Document.created_at >= window)) or 0
        stored = s.scalar(select(func.coalesce(func.sum(Document.size_bytes), 0))) or 0
    if mine >= int(lim["session_max_docs"]):
        raise HTTPException(429, "session_limit")
    if stored + size > float(lim["storage_max_mb"]) * 1024 * 1024:
        raise HTTPException(503, "storage_full")
    if approach in ("B", "C") and llm_docs_today() >= int(lim["llm_daily_docs"]):
        raise HTTPException(429, "llm_daily_cap")


_create_lock = threading.Lock()  # cap check and insert must be one step, or concurrent requests overshoot a cap


def _create(data: bytes, filename: str, mime: str, approach: str, doc_type: Optional[str],
            owner_id: str) -> dict[str, Any]:
    digest = hashlib.sha256(data).hexdigest()
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
           "image/tiff": ".tif"}[mime]
    with _create_lock:
        _check_caps(owner_id, approach, len(data))
        with session() as s:
            doc = Document(filename=filename[:255], content_type=mime, sha256=digest, size_bytes=len(data),
                           storage_path="", approach=approach, doc_type_hint=doc_type, owner_id=owner_id)
            s.add(doc)
            s.flush()
            path = STORAGE / f"{doc.id}{ext}"
            path.write_bytes(data)
            doc.storage_path = str(path)
            doc_id = doc.id
            if approach in ("B", "C"):
                usage = s.get(LlmUsage, _utc_day())
                if usage is None:
                    s.add(LlmUsage(day=_utc_day(), count=1))
                else:
                    usage.count += 1
    enqueue(doc_id)
    log.info("document_received", document_id=doc_id, mime=mime, size=len(data), approach=approach)
    return {"job_id": doc_id, "status": "queued", "approach": approach}


def _owned(s, doc_id: str, sid: str) -> Document:
    """The caller's own document; someone else's looks exactly like a missing one."""
    doc = get_document(s, doc_id)
    if not doc or doc.owner_id != sid:
        raise HTTPException(404, "not found")
    return doc


@app.get("/documents/{doc_id}")
def get_doc(request: Request, doc_id: str) -> dict[str, Any]:
    with session() as s:
        return _doc_out(_owned(s, doc_id, current_sid(request)))


@app.get("/documents/{doc_id}/preview")
def preview(request: Request, doc_id: str) -> Response:
    with session() as s:
        path = _owned(s, doc_id, current_sid(request)).storage_path
    read = read_page_path(path)
    if read.exists():  # the page OCR read: source boxes are positions on this image
        return Response(read.read_bytes(), media_type="image/jpeg")
    pages, _ = load_pages(Path(path).read_bytes(), dpi=110)
    ok, buf = cv2.imencode(".png", pages[0])
    return Response(buf.tobytes(), media_type="image/png")


@app.get("/documents")
def list_docs(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    with session() as s:
        docs = s.scalars(select(Document).where(Document.owner_id == current_sid(request))
                         .order_by(Document.created_at.desc()).limit(min(limit, 200))).all()
        return [{"id": d.id, "filename": d.filename, "status": d.status,
                 "route": d.latest.route if d.latest else None, "score": d.latest.score if d.latest else None,
                 "doc_type": d.latest.doc_type if d.latest else None, "reviewed": bool(d.reviews)} for d in docs]


WEAK_CONFIDENCE = 0.8  # same line the review UI uses to flag a field


def weak_fields(doc_type: str, extraction: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """Critical fields and line items the reader was unsure about (or did not find), weakest first.

    Mirrors what the document score looks at (scoring/score.py), so the list explains the score."""
    out = []
    for path in validation_config()["critical_fields"].get(doc_type, []):
        node = get_path(extraction or {}, path)
        conf = node.get("confidence") if isinstance(node, dict) and node.get("value") is not None else None
        if conf is None or conf < WEAK_CONFIDENCE:
            out.append({"path": path, "confidence": conf})
    item_confs = [(it.get("total") or {}).get("confidence") for it in (extraction or {}).get("line_items") or []
                  if isinstance(it, dict)]
    item_confs = [c for c in item_confs if c is not None]
    if item_confs and min(item_confs) < WEAK_CONFIDENCE:
        out.append({"path": "line_items", "confidence": min(item_confs)})
    return sorted(out, key=lambda f: (f["confidence"] is not None, f["confidence"] or 0.0))


@app.get("/review/queue")
def get_queue(request: Request, limit: int = 50) -> list[dict[str, Any]]:
    with session() as s:
        return [{"id": d.id, "filename": d.filename, "doc_type": d.latest.doc_type, "score": d.latest.score,
                 "approach": d.approach,
                 "route_reason": d.latest.result.get("route_reason"),
                 "weak_fields": weak_fields(d.latest.doc_type, d.latest.result.get("extraction")),
                 "created_at": d.created_at.isoformat()}
                for d in review_queue(s, limit, owner_id=current_sid(request))]


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


def corrections_by_field(changed_lists: list[Any], top: int = 10) -> dict[str, Any]:
    """How many corrected documents touched each field. Line items count as one field."""
    counts: dict[str, int] = {}
    for changed in changed_lists:
        for path in {p.split(".")[0] if p.startswith("line_items.") else p for p in changed or []}:
            counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return {"documents": len(changed_lists), "fields": [{"path": p, "count": c} for p, c in ranked]}


@app.post("/review/{doc_id}")
def submit_review(request: Request, doc_id: str, body: ReviewIn) -> dict[str, Any]:
    with session() as s:
        doc = get_document(s, doc_id)
        if not doc or doc.owner_id != current_sid(request) or not doc.latest:
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
            before = {k: v for k, v in (ex.result.get("values") or {}).items() if k != "doc_type"}
            changed = _diff(before, values)
        s.add(Review(document_id=doc_id, extraction_id=ex.id, action=body.action, reviewer=body.reviewer[:100],
                     corrected_values=values, changed_fields=changed, notes=body.notes))
    log.info("review_submitted", document_id=doc_id, action=body.action, changed_fields=changed)
    return {"ok": True, "changed_fields": changed}


@app.get("/metrics")
def metrics(request: Request) -> dict[str, Any]:
    sid = current_sid(request)
    with session() as s:
        n_docs = s.scalar(select(func.count(Document.id)).where(Document.owner_id == sid)) or 0
        by_status = dict(s.execute(select(Document.status, func.count()).where(Document.owner_id == sid)
                                   .group_by(Document.status)).all())
        latest_ids = (select(func.max(Extraction.id)).join(Document, Document.id == Extraction.document_id)
                      .where(Document.owner_id == sid).group_by(Extraction.document_id).scalar_subquery())
        rows = s.execute(select(Extraction.route, Extraction.doc_type, Extraction.cost_usd, Extraction.latency_ms,
                                Extraction.approach).where(Extraction.id.in_(latest_ids))).all()
        reviews = dict(s.execute(select(Review.action, func.count()).join(Document, Document.id == Review.document_id)
                                 .where(Document.owner_id == sid).group_by(Review.action)).all())
        changed_lists = s.scalars(select(Review.changed_fields).join(Document, Document.id == Review.document_id)
                                  .where(Document.owner_id == sid, Review.action == "correct")).all()
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
        "corrections_by_field": corrections_by_field(changed_lists),
        "default_approach": default_approach(),
        "offline_eval": offline,
    }


# ------------------------------------------------------------------ export

DocTypeParam = Optional[Literal["faktur_pajak", "invoice", "receipt"]]


@app.get("/export/approved/count")
def export_count(request: Request, doc_type: DocTypeParam = None) -> dict[str, Any]:
    from docex.api.export import approved

    with session() as s:
        rows = approved(s, doc_type, owner_id=current_sid(request))
    by: dict[str, int] = {}
    for r in rows:
        by[r["meta"]["decision"]] = by.get(r["meta"]["decision"], 0) + 1
    return {"documents": len(rows), "line_items": sum(len(r["items"]) for r in rows), "by_decision": by}


@app.get("/export/approved")
def export_approved(request: Request, format: Literal["xlsx", "csv"] = "xlsx", part: Literal["documents", "items"] = "documents",
                    doc_type: DocTypeParam = None) -> Response:
    """Approved documents (auto-approved, approved or corrected by a reviewer).

    xlsx: one workbook with a "Dokumen" sheet and a "Rincian barang" sheet.
    csv: one table per request; `part=items` returns the line items.
    """
    from datetime import date

    from docex.api.export import ITEM_COLS, approved, tables, to_csv, to_xlsx

    with session() as s:
        cols, doc_rows, item_rows = tables(approved(s, doc_type, owner_id=current_sid(request)))
    stem = f"docex-disetujui-{date.today().isoformat()}{f'-{doc_type}' if doc_type else ''}"
    if format == "xlsx":
        body, media, name = (to_xlsx(cols, doc_rows, item_rows),
                             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", f"{stem}.xlsx")
    elif part == "items":
        body, media, name = to_csv(ITEM_COLS, item_rows), "text/csv; charset=utf-8", f"{stem}-rincian.csv"
    else:
        body, media, name = to_csv(cols, doc_rows), "text/csv; charset=utf-8", f"{stem}.csv"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ demo helpers

SAMPLES_DIR = Path(os.environ.get("DOCEX_SAMPLES_DIR", ROOT / "demo_samples"))


@app.get("/samples")
def samples() -> list[dict[str, Any]]:
    if not (SAMPLES_DIR / "index.json").exists():
        return []
    return json.loads((SAMPLES_DIR / "index.json").read_text())


@app.get("/samples/{name}/image")
def sample_image(name: str) -> Response:
    index = {x["name"]: x for x in samples()}
    if name not in index:
        raise HTTPException(404, "unknown sample")
    pages, _ = load_pages((SAMPLES_DIR / index[name]["file"]).read_bytes(), dpi=60)
    img = pages[0]
    scale = 360 / img.shape[1]
    if scale < 1:
        img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


@app.post("/samples/{name}", status_code=202)
@limiter.limit(LIMITS["rate_limit"])
def run_sample(request: Request, name: str, approach: Optional[Literal["A", "B", "C"]] = None) -> dict[str, Any]:
    index = {x["name"]: x for x in samples()}
    if name not in index:
        raise HTTPException(404, "unknown sample")
    path = SAMPLES_DIR / index[name]["file"]
    data = path.read_bytes()
    approach = approach or default_approach()
    _require_available(approach)
    return _create(data, path.name, sniff(data) or "image/jpeg", approach, None, current_sid(request))


# ------------------------------------------------------------------ review UI

WEB_DIR = Path(os.environ.get("DOCEX_WEB_DIR", ROOT / "web"))

if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=WEB_DIR), name="web")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")
