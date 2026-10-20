"""Build JSON schemas for structured LLM output from the Pydantic models.

Structured-output APIs accept a restricted JSON-schema subset, so instead of
``model_json_schema()`` we emit a flat, $ref-free schema in which every property
is required and nullable values use ``["T", "null"]``.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from docex.schemas.documents import Extracted, LineItem, model_for

_PRIMS = {str: "string", float: "number", int: "integer", bool: "boolean"}


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


def _model_schema(model: type[BaseModel]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for name, f in model.model_fields.items():
        if name == "doc_type":
            continue
        ann = f.annotation
        meta = getattr(ann, "__pydantic_generic_metadata__", {})
        if meta.get("origin") is Extracted:
            leaf = _obj({"value": {"type": [_PRIMS[meta["args"][0]], "null"]}, "confidence": {"type": "number"}})
            if f.description:
                leaf["description"] = f.description
            props[name] = leaf
        elif isinstance(ann, type) and issubclass(ann, BaseModel):
            props[name] = _model_schema(ann)
        elif name == "line_items":
            props[name] = {"type": "array", "items": _model_schema(LineItem)}
        elif ann is bool:
            props[name] = {"type": "boolean"}
            if f.description:
                props[name]["description"] = f.description
    return _obj(props)


def extraction_schema(doc_type: str) -> dict[str, Any]:
    return _model_schema(model_for(doc_type))


CLASSIFY_SCHEMA: dict[str, Any] = _obj({
    "doc_type": {"type": "string", "enum": ["faktur_pajak", "invoice", "receipt", "other"]},
    "confidence": {"type": "number"},
})
