"""Structured logging with PII masking, and lightweight per-stage tracing."""
from __future__ import annotations

import logging
import re
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

import structlog

# 15/16-digit NPWP or NIK, with or without separators ("12.345.678.9-012.345").
_ID_RX = re.compile(r"(?<!\d)(\d[\d.\-\s]{13,22}\d)(?!\d)")


def mask_ids(text: str) -> str:
    def repl(m: re.Match[str]) -> str:
        digits = re.sub(r"\D", "", m.group(1))
        if len(digits) in (15, 16):
            return digits[:2] + "*" * (len(digits) - 4) + digits[-2:]
        return m.group(0)

    return _ID_RX.sub(repl, text)


def _mask_value(v: Any) -> Any:
    if isinstance(v, str):
        return mask_ids(v)
    if isinstance(v, dict):
        return {k: _mask_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return type(v)(_mask_value(x) for x in v)
    return v


def _mask_processor(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return {k: _mask_value(v) for k, v in event_dict.items()}


_configured = False


def configure_logging(level: str = "INFO") -> None:
    global _configured
    if _configured:
        return
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_processor,
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    _configured = True


def get_logger(name: str = "docex") -> Any:
    configure_logging()
    return structlog.get_logger(name)


@dataclass
class Span:
    name: str
    start: float
    duration_ms: float = 0.0
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Trace:
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    spans: list[Span] = field(default_factory=list)

    @contextmanager
    def span(self, name: str, **attrs: Any) -> Iterator[Span]:
        s = Span(name, time.perf_counter(), attrs=dict(attrs))
        try:
            yield s
        except Exception as e:
            s.attrs["error"] = repr(e)
            raise
        finally:
            s.duration_ms = round((time.perf_counter() - s.start) * 1000, 1)
            self.spans.append(s)

    def to_list(self) -> list[dict[str, Any]]:
        return [{"name": s.name, "duration_ms": s.duration_ms, **_mask_value(s.attrs)} for s in self.spans]

    @property
    def total_ms(self) -> float:
        return round(sum(s.duration_ms for s in self.spans), 1)
