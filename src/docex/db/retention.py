"""Delete demo uploads and their results once they are older than limits.retention_minutes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import or_, select

from docex.config import pipeline_config
from docex.db.models import Document, session
from docex.observability import get_logger
from docex.worker.tasks import read_page_path

log = get_logger("docex.retention")


def purge_expired(now: Optional[datetime] = None, retention_minutes: Optional[int] = None,
                  young_job_minutes: int = 30) -> int:
    """Remove files, cached page images, reviews, extractions and documents past retention; return how many.

    A queued or processing document younger than young_job_minutes is left alone so the worker keeps its file;
    an older unfinished one is stuck and goes with the rest.
    """
    now = now or datetime.now(timezone.utc)
    if retention_minutes is None:
        retention_minutes = int(pipeline_config()["limits"]["retention_minutes"])
    cutoff = now - timedelta(minutes=retention_minutes)
    young_job_cutoff = now - timedelta(minutes=young_job_minutes)
    deleted = 0
    with session() as s:
        docs = s.scalars(select(Document).where(
            Document.created_at < cutoff,
            or_(Document.status.notin_(["queued", "processing"]), Document.created_at < young_job_cutoff))).all()
        for d in docs:
            if d.storage_path:
                for p in (Path(d.storage_path), read_page_path(d.storage_path)):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError as e:
                        log.error("purge_file_failed", path=str(p), error=str(e))
            for child in [*d.reviews, *d.extractions]:
                s.delete(child)
            s.flush()
            s.delete(d)
            deleted += 1
    if deleted:
        log.info("documents_purged", count=deleted)
    return deleted


async def purge_loop(interval_s: int = 900) -> None:
    while True:
        try:
            await asyncio.to_thread(purge_expired)
        except Exception as e:  # never let one bad run stop the cleaner
            log.error("purge_failed", error=str(e))
        await asyncio.sleep(interval_s)
