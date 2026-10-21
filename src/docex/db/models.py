"""Persistence: documents, extraction results (with model/prompt versions) and human reviews.

SQLite for dev (default), Postgres in docker-compose via DATABASE_URL.
"""
from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from docex.config import DATA_DIR


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(100))
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_path: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued|processing|done|failed
    approach: Mapped[str] = mapped_column(String(4))
    doc_type_hint: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(32), index=True, nullable=True)  # anonymous visitor
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    extractions: Mapped[list["Extraction"]] = relationship(back_populates="document", order_by="Extraction.id")
    reviews: Mapped[list["Review"]] = relationship(back_populates="document", order_by="Review.id")

    @property
    def latest(self) -> Optional["Extraction"]:
        return self.extractions[-1] if self.extractions else None


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    doc_type: Mapped[str] = mapped_column(String(20))
    route: Mapped[str] = mapped_column(String(20), index=True)
    score: Mapped[float] = mapped_column(Float)
    approach: Mapped[str] = mapped_column(String(4))
    model: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    prompt_version: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    versions: Mapped[dict[str, Any]] = mapped_column(JSON)
    result: Mapped[dict[str, Any]] = mapped_column(JSON)      # full PipelineResult
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    latency_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[Document] = relationship(back_populates="extractions")


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"), index=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("extractions.id"))
    action: Mapped[str] = mapped_column(String(20))  # approve | correct | reject
    reviewer: Mapped[str] = mapped_column(String(100), default="anonymous")
    corrected_values: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    changed_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    document: Mapped[Document] = relationship(back_populates="reviews")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", f"sqlite:///{DATA_DIR / 'docex.db'}")


_engine = None
_Session: Optional[sessionmaker] = None


def init_db(url: Optional[str] = None) -> None:
    global _engine, _Session
    url = url or database_url()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    _engine = create_engine(url, **kwargs)
    Base.metadata.create_all(_engine)
    _ensure_owner_column(_engine)
    _Session = sessionmaker(_engine, expire_on_commit=False)


def _ensure_owner_column(engine) -> None:
    """create_all does not alter existing tables: add documents.owner_id to a database made before it existed."""
    if "owner_id" in {c["name"] for c in inspect(engine).get_columns("documents")}:
        return
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE documents ADD COLUMN owner_id VARCHAR(32)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_documents_owner_id ON documents (owner_id)"))


@contextmanager
def session() -> Iterator[Session]:
    if _Session is None:
        init_db()
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_document(s: Session, doc_id: str) -> Optional[Document]:
    return s.get(Document, doc_id)


def review_queue(s: Session, limit: int = 50) -> list[Document]:
    docs = s.scalars(select(Document).where(Document.status == "done").order_by(Document.created_at.desc())).all()
    out = []
    for d in docs:
        if d.latest and d.latest.route == "review" and not d.reviews:
            out.append(d)
        if len(out) >= limit:
            break
    return out
