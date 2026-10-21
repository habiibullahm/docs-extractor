"""Find where each extracted value sits on the page OCR read, so the review UI can draw a box around it.

Matching runs on the final normalized values and the OCR lines, so it does not depend on which extractor
produced the values. It is deliberately strict: a field gets a box only when exactly one OCR line explains the
value (after using the field's printed label to break ties). A missing box is better than a wrong one.

Boxes are stored as fractions of the page (x, y, w, h in 0..1), so they stay valid whatever size the page is
shown at.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any, Optional

from docex.extract.baseline import DATE_RE, money_tokens
from docex.normalize.core import parse_date, parse_number
from docex.providers.ocr import OcrLine

MONEY_FIELDS = {"total", "subtotal", "discount", "tax", "service_charge", "dpp", "ppn", "ppnbm"}
DATE_FIELDS = {"date", "due_date"}
ID_FIELDS = {"tax_invoice_number", "seller.npwp", "buyer.npwp", "vendor.npwp", "customer.npwp", "seller.nik",
             "buyer.nik", "vendor.nik", "customer.nik", "bank_account.account_number"}
TEXT_FIELDS = {"seller.name", "buyer.name", "vendor.name", "customer.name", "store_name", "invoice_number",
               "bank_account.bank_name", "bank_account.account_name", "payment_method"}

# Printed labels that separate lines carrying the same amount. (must match, must not match)
LABELS: dict[str, tuple[str, Optional[str]]] = {
    "total": (r"total|jumlah|grand|tagihan|harga\s+jual", r"sub\s*-?\s*total|ppn|pajak|dpp"),
    "subtotal": (r"sub\s*-?\s*total|jumlah\s+harga|harga\s+jual", None),
    "discount": (r"diskon|discount|potongan", None),
    "tax": (r"ppn|pajak|tax|pb1", None),
    "ppn": (r"ppn|pajak\s+pertambahan", r"ppnbm|barang\s+mewah"),
    "dpp": (r"dpp|dasar\s+pengenaan|dasar\s+pajak", None),
    "ppnbm": (r"ppnbm|barang\s+mewah", None),
    "service_charge": (r"servis|service|layanan", None),
}


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s)


def _amounts(text: str) -> list[float]:
    out = []
    for tok in money_tokens(text):
        n = parse_number(tok)
        if n is not None:
            out.append(abs(n))
    return out


def _pick(lines: list[OcrLine], hits: list[int], path: str) -> Optional[int]:
    """One line index, or None when the match is not certain."""
    if len(hits) == 1:
        return hits[0]
    label = LABELS.get(path)
    if not hits or not label:
        return None
    want, avoid = (re.compile(label[0], re.I), re.compile(label[1], re.I) if label[1] else None)
    named = [i for i in hits if want.search(lines[i].text) and not (avoid and avoid.search(lines[i].text))]
    return named[0] if len(named) == 1 else None


def _match_money(lines: list[OcrLine], value: float, path: str) -> Optional[int]:
    if not value:
        return None
    hits = [i for i, ln in enumerate(lines) if any(abs(a - abs(value)) < 0.5 for a in _amounts(ln.text))]
    return _pick(lines, hits, path)


def _match_date(lines: list[OcrLine], value: str, path: str) -> Optional[int]:
    hits = []
    for i, ln in enumerate(lines):
        if any(parse_date(m.group(0)) == value for m in DATE_RE.finditer(ln.text)):
            hits.append(i)
    return hits[0] if len(hits) == 1 else None


def _match_id(lines: list[OcrLine], value: str, path: str) -> Optional[int]:
    want = _digits(str(value))
    if len(want) < 10:
        return None
    hits = [i for i, ln in enumerate(lines) if want in _digits(ln.text)]
    return hits[0] if len(hits) == 1 else None


def _match_text(lines: list[OcrLine], value: str, path: str) -> Optional[int]:
    want = _alnum(str(value))
    if len(want) < 4:
        return None
    hits = [i for i, ln in enumerate(lines) if want in _alnum(ln.text)]
    if not hits:  # tolerate one or two misread characters, but only on a close match
        for i, ln in enumerate(lines):
            got = _alnum(ln.text)
            if len(got) >= len(want) and SequenceMatcher(None, want, got).find_longest_match().size >= 0.9 * len(want):
                hits.append(i)
    return hits[0] if len(hits) == 1 else None


def _match_item(lines: list[OcrLine], item: dict[str, Any]) -> Optional[int]:
    desc, total = _alnum(str(item.get("description") or "")), item.get("total")
    if len(desc) < 3 or total is None:
        return None
    hits = [i for i, ln in enumerate(lines)
            if desc in _alnum(ln.text) and any(abs(a - abs(total)) < 0.5 for a in _amounts(ln.text))]
    return hits[0] if len(hits) == 1 else None


def _tight_box(line: OcrLine, path: str, value: Any) -> tuple[int, int, int, int]:
    """The words of the line that make up the value, or the whole line when they cannot be told apart."""
    words = line.words
    if not words:
        return line.bbox
    kind = ("money" if path in MONEY_FIELDS else "date" if path in DATE_FIELDS else "id" if path in ID_FIELDS
            else "text")
    n = len(words)
    best: Optional[tuple[int, int]] = None
    for a in range(n):
        for b in range(a, min(n, a + 6)):
            chunk = " ".join(w[0] for w in words[a:b + 1])
            if kind == "money":
                ok = b == a and any(abs(x - abs(float(value))) < 0.5 for x in _amounts(chunk))
            elif kind == "date":
                ok = any(parse_date(m.group(0)) == value for m in DATE_RE.finditer(chunk))
            elif kind == "id":
                ok = _digits(chunk) == _digits(str(value))
            else:
                ok = _alnum(chunk) == _alnum(str(value))
            if ok and (best is None or (b - a) < (best[1] - best[0])):
                best = (a, b)
    if best is None:
        return line.bbox
    ws = words[best[0]:best[1] + 1]
    x0, y0 = min(w[1] for w in ws), min(w[2] for w in ws)
    x1, y1 = max(w[1] + w[3] for w in ws), max(w[2] + w[4] for w in ws)
    return (x0, y0, x1 - x0, y1 - y0)


def _get(values: dict[str, Any], path: str) -> Any:
    cur: Any = values
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def locate_sources(values: dict[str, Any], lines: list[OcrLine], size: tuple[int, int]) -> dict[str, Any]:
    """``size`` is (width, height) of the image the OCR lines refer to. Returns {path: {"box", "text"}}."""
    w, h = size
    if not lines or not w or not h:
        return {}
    found: dict[str, tuple[int, Any]] = {}
    for path in sorted(MONEY_FIELDS | DATE_FIELDS | ID_FIELDS | TEXT_FIELDS):
        v = _get(values, path)
        if v in (None, ""):
            continue
        if path in MONEY_FIELDS:
            i = _match_money(lines, float(v), path)
        elif path in DATE_FIELDS:
            i = _match_date(lines, str(v), path)
        elif path in ID_FIELDS:
            i = _match_id(lines, str(v), path)
        else:
            i = _match_text(lines, str(v), path)
        if i is not None:
            found[path] = (i, v)
    for n, item in enumerate(values.get("line_items") or []):
        if isinstance(item, dict) and (i := _match_item(lines, item)) is not None:
            found[f"line_items.{n}"] = (i, None)
    out = {}
    for path, (i, v) in found.items():
        x, y, bw, bh = lines[i].bbox if v is None else _tight_box(lines[i], path, v)
        x0, y0 = max(0.0, x / w), max(0.0, y / h)
        out[path] = {"box": [round(x0, 4), round(y0, 4), round(min(bw / w, 1 - x0), 4), round(min(bh / h, 1 - y0), 4)],
                     "text": lines[i].text.strip()[:120]}
    return out
