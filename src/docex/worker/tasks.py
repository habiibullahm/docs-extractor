"""Background processing. Two backends:

- ``inline`` (default, single container / HF Spaces): a small in-process thread pool.
- ``rq``: Redis + RQ workers (docker-compose). Start with ``rq worker docex --url $REDIS_URL``.
"""
from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from docex.db.models import Extraction, get_document, session
from docex.observability import get_logger

log = get_logger("docex.worker")
_pipelines: dict[str, object] = {}
_lock = threading.Lock()


def _pipeline(approach: str):
    from docex.pipeline.run import Pipeline

    with _lock:
        if approach not in _pipelines:
            _pipelines[approach] = Pipeline(approach)
        return _pipelines[approach]


def read_page_path(storage_path: str) -> Path:
    p = Path(storage_path)
    return p.with_name(f"{p.stem}.read.jpg")


def save_read_page(storage_path: str, image) -> None:
    """Keep the page OCR read (deskewed, maybe rotated) so the review UI shows what the boxes refer to."""
    import cv2

    h, w = image.shape[:2]
    if w > 1600:
        image = cv2.resize(image, (1600, round(h * 1600 / w)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if ok:
        read_page_path(storage_path).write_bytes(buf.tobytes())


def process_document(doc_id: str) -> None:
    with session() as s:
        doc = get_document(s, doc_id)
        if doc is None:
            return
        doc.status = "processing"
        path, approach, hint = doc.storage_path, doc.approach, doc.doc_type_hint
    try:
        res = _pipeline(approach).run(Path(path).read_bytes(), doc_type_hint=hint,
                                      on_page=lambda img: save_read_page(path, img))
    except Exception as e:  # never leave a job stuck in "processing"
        log.exception("processing_failed", document_id=doc_id)
        with session() as s:
            doc = get_document(s, doc_id)
            doc.status, doc.error = "failed", repr(e)[:500]
        return
    with session() as s:
        doc = get_document(s, doc_id)
        s.add(Extraction(document_id=doc_id, doc_type=res.doc_type, route=res.route, score=res.score,
                         approach=res.approach, model=res.versions.get("model"),
                         prompt_version=res.versions.get("prompt"), versions=res.versions, result=res.to_dict(),
                         cost_usd=res.usage.get("cost_usd", 0.0), latency_ms=res.latency_ms))
        doc.status = "failed" if res.error else "done"
        doc.error = res.error
    log.info("job_done", document_id=doc_id, route=res.route, score=res.score)


_executor: Optional[ThreadPoolExecutor] = None


def enqueue(doc_id: str) -> str:
    backend = os.environ.get("QUEUE_BACKEND", "inline")
    if backend == "rq":
        from redis import Redis
        from rq import Queue

        q = Queue("docex", connection=Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0")))
        job = q.enqueue(process_document, doc_id, job_timeout=600)
        return job.id
    global _executor
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=int(os.environ.get("INLINE_WORKERS", "2")))
    _executor.submit(process_document, doc_id)
    return doc_id
