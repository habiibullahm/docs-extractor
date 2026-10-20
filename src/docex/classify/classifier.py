"""Document type classification: keyword scoring on OCR text, or an LLM/VLM call."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from docex.prompts import load_prompt
from docex.providers.llm import Content, LLMProvider, Usage
from docex.schemas.llm_schema import CLASSIFY_SCHEMA


@dataclass
class Classification:
    doc_type: str
    confidence: float
    method: str
    usage: Optional[Usage] = None


KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "faktur_pajak": [(r"faktur\s*pajak", 4), (r"dasar\s+pengenaan\s+pajak", 3), (r"ppnbm", 2),
                     (r"pengusaha\s+kena\s+pajak|penjual\s*\(pkp\)", 2), (r"\d{3}\.\d{3}-\d{2}\.\d{8}", 2)],
    "invoice": [(r"\binvoice\b", 3), (r"jatuh\s+tempo|\bdue\b", 2), (r"tagih", 2), (r"faktur\s+penjualan", 2),
                (r"no\.?\s*rek|transfer|pembayaran\s+ke", 1), (r"kepada\s+yth|bill\s+to|customer", 1)],
    "receipt": [(r"kembali|kembalian|change", 2), (r"\btunai\b|\bcash\b|qris|ovo|gopay|debit", 2),
                (r"kasir|cashier|server", 2), (r"terima\s*kasih|thank", 1), (r"\bpb1\b|service", 1),
                (r"\d+\s*x\s*[\d.,]+", 1), (r"order\s*#|no\.?\s*trx|struk", 1)],
}


def classify_text(text: str) -> Classification:
    t = text.lower()
    scores = {k: sum(w for pat, w in pats if re.search(pat, t)) for k, pats in KEYWORDS.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, s1), (_, s2) = ranked[0], ranked[1]
    if s1 == 0:
        return Classification("other", 0.3, "keyword")
    margin = (s1 - s2) / max(s1, 1)
    conf = min(0.99, 0.5 + 0.5 * margin + 0.03 * s1)
    return Classification(best, round(conf, 3), "keyword")


def classify_llm(provider: LLMProvider, content: Content) -> Classification:
    prompt = load_prompt("classify_v1")
    resp = provider.complete_json(prompt.text, content, CLASSIFY_SCHEMA)
    dt = resp.data.get("doc_type", "other")
    if dt not in ("faktur_pajak", "invoice", "receipt", "other"):
        dt = "other"
    return Classification(dt, float(resp.data.get("confidence", 0.5)), f"llm:{provider.model}", resp.usage)
