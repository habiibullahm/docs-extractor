"""Approaches B (OCR text -> LLM) and C (image -> vision LLM), plus the repair pass."""
from __future__ import annotations

import json
from typing import Any

from docex.prompts import Prompt, load_prompt
from docex.providers.llm import Content, ImagePart, LLMProvider, LLMResponse
from docex.schemas.llm_schema import extraction_schema


def _system(prompt: Prompt, doc_type: str, schema: dict[str, Any]) -> str:
    return prompt.render(doc_type=doc_type, json_schema=json.dumps(schema, separators=(",", ":")))


def content_from_ocr(ocr_text: str) -> Content:
    return ["OCR text of the document (line order follows the page, wide gaps separate columns):\n\n" + ocr_text]


def content_from_images(images: list[bytes]) -> Content:
    parts: Content = [ImagePart(b, "image/jpeg") for b in images]
    parts.append("Extract the data from this document image.")
    return parts


def extract_llm(provider: LLMProvider, doc_type: str, content: Content, prompt_name: str) -> tuple[LLMResponse, str]:
    prompt = load_prompt(prompt_name)
    schema = extraction_schema(doc_type)
    resp = provider.complete_json(_system(prompt, doc_type, schema), content, schema)
    return resp, prompt.version


def repair_llm(provider: LLMProvider, doc_type: str, content: Content, previous: dict[str, Any],
               errors: list[str], extract_prompt: str, repair_prompt: str) -> tuple[LLMResponse, str]:
    base = load_prompt(extract_prompt)
    rp = load_prompt(repair_prompt)
    schema = extraction_schema(doc_type)
    note = rp.render(validation_errors="\n".join(f"- {e}" for e in errors),
                     previous_json=json.dumps(previous, ensure_ascii=False))
    resp = provider.complete_json(_system(base, doc_type, schema), list(content) + [note], schema)
    return resp, rp.version
