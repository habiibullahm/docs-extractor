"""Apply field-level normalizers to a raw extraction dict and build the schema model."""
from __future__ import annotations

from typing import Any, Callable, Optional

from pydantic import BaseModel

from docex.normalize.core import (
    digits_only,
    normalize_currency,
    normalize_text,
    parse_date,
    parse_number,
    parse_time,
)
from docex.schemas.documents import Extracted, LineItem, model_for

_BY_NAME: dict[str, Callable[[Any], Any]] = {
    "date": parse_date,
    "due_date": parse_date,
    "time": parse_time,
    "npwp": digits_only,
    "nik": digits_only,
    "account_number": digits_only,
    "tax_invoice_number": digits_only,
    "currency": normalize_currency,
}

DEFAULT_CONFIDENCE_FOR_BARE_VALUES = 0.5


def _leaf(raw: Any) -> tuple[Any, float]:
    if isinstance(raw, dict) and "value" in raw:
        return raw.get("value"), raw.get("confidence", DEFAULT_CONFIDENCE_FOR_BARE_VALUES)
    return raw, DEFAULT_CONFIDENCE_FOR_BARE_VALUES


def _normalizer(name: str, ann: Any) -> Callable[[Any], Any]:
    if name in _BY_NAME:
        return _BY_NAME[name]
    args = getattr(ann, "__pydantic_generic_metadata__", {}).get("args", ())
    if args and args[0] is float:
        return parse_number
    return normalize_text


def _build(schema: type[BaseModel], raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, field in schema.model_fields.items():
        if name not in raw:
            continue
        ann = field.annotation
        origin = getattr(ann, "__pydantic_generic_metadata__", {}).get("origin")
        v = raw[name]
        if origin is Extracted:
            value, conf = _leaf(v)
            norm: Optional[Any] = _normalizer(name, ann)(value) if value is not None else None
            out[name] = {"value": norm, "confidence": conf if norm is not None else 0.0}
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            out[name] = _build(ann, v if isinstance(v, dict) else {})
        elif name == "line_items":
            out[name] = [_build(LineItem, it) for it in (v or []) if isinstance(it, dict)]
        elif name in ("is_adjustment", "document_inconsistent"):
            out[name] = bool(v.get("value") if isinstance(v, dict) else v)
        elif name == "doc_type":
            continue
    return out


def normalize_raw(doc_type: str, raw: dict[str, Any]) -> dict[str, Any]:
    return _build(model_for(doc_type), raw or {})


def normalize_document(doc_type: str, raw: dict[str, Any]) -> BaseModel:
    model = model_for(doc_type)
    return model.model_validate(normalize_raw(doc_type, raw))
