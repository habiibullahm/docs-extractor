"""Approach A: OCR + regex / layout heuristics (no LLM).

Field confidence = OCR line confidence x a heuristic factor that reflects how the
value was found (explicit label on the same line vs. positional guess).
"""
from __future__ import annotations

import re
from typing import Any, Optional

from docex.normalize.core import parse_date, parse_number
from docex.providers.ocr import OcrLine, OcrResult

MONEY = r"\(?-?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d{1,2})?\)?-?|\(?-?\d+(?:[.,]\d{1,2})?\)?"
MONEY_RE = re.compile(rf"(?<![\w/])(?:{MONEY})(?![\w/])")
DATE_PATTERNS = [
    r"\d{1,2}\s+(?:januari|februari|maret|april|mei|juni|juli|agustus|september|oktober|november|desember)\s+\d{4}",
    r"\d{1,2}[\s-](?:jan|feb|mar|apr|mei|jun|jul|agu|ags|agt|sep|okt|nov|des)[a-z]*[\s-]\d{2,4}",
    r"\d{4}-\d{2}-\d{2}",
    r"\d{1,2}/\d{1,2}/\d{2,4}",
    r"\d{1,2}-\d{1,2}-\d{2,4}",
]
DATE_RE = re.compile("|".join(f"(?:{p})" for p in DATE_PATTERNS), re.I)
TIME_RE = re.compile(r"\b([01]?\d|2[0-3])[:.]([0-5]\d)(?:[:.]([0-5]\d))?\b")
SEG_SPLIT = re.compile(r"\s{3,}|\s*\|\s*")

LABEL_FACTOR = 0.92
GUESS_FACTOR = 0.6


def F(value: Any, conf: float) -> dict[str, Any]:
    return {"value": value, "confidence": round(max(0.0, min(1.0, conf)), 3) if value is not None else 0.0}


NPWP_FMT = re.compile(r"\d{2}\.\d{3}\.\d{3}\.\d-\d{3}\.\d{3}")


def clean_id(raw: str, lengths: tuple[int, ...] = (15, 16)) -> str:
    """Recover an NPWP/NIK from noisy OCR: prefer the printed 15-digit layout, and
    drop a single stray leading digit (often a misread ':' or '|')."""
    m = NPWP_FMT.search(raw)
    if m:
        return m.group(0)
    digits = re.sub(r"\D", "", raw)
    if len(digits) == max(lengths) + 1:
        digits = digits[1:]
    return digits


def segments(text: str) -> list[str]:
    return [s.strip(" :|") for s in SEG_SPLIT.split(text) if s.strip(" :|")]


def money_tokens(text: str) -> list[str]:
    return [m.group(0) for m in MONEY_RE.finditer(text)]


def last_amount(text: str) -> Optional[float]:
    toks = money_tokens(text)
    return parse_number(toks[-1]) if toks else None


def find_line(lines: list[OcrLine], pattern: str, need_amount: bool = False, exclude: Optional[str] = None,
              start: int = 0, last: bool = False) -> Optional[tuple[int, OcrLine]]:
    rx = re.compile(pattern, re.I)
    ex = re.compile(exclude, re.I) if exclude else None
    hits = []
    for i in range(start, len(lines)):
        t = lines[i].text
        if rx.search(t) and not (ex and ex.search(t)):
            if need_amount and last_amount(t[rx.search(t).end():]) is None:
                continue
            hits.append((i, lines[i]))
            if not last:
                break
    return hits[-1] if hits else None


def amount_field(lines: list[OcrLine], pattern: str, exclude: Optional[str] = None, last: bool = False,
                 start: int = 0) -> dict[str, Any]:
    hit = find_line(lines, pattern, need_amount=True, exclude=exclude, last=last, start=start)
    if not hit:
        return F(None, 0)
    i, line = hit
    m = re.search(pattern, line.text, re.I)
    return F(last_amount(line.text[m.end():]), line.confidence * LABEL_FACTOR)


def find_date(lines: list[OcrLine], label: Optional[str] = None, exclude: Optional[str] = None,
              last: bool = False) -> dict[str, Any]:
    if label:
        rx = re.compile(label, re.I)
        ex = re.compile(exclude, re.I) if exclude else None
        for line in lines:
            m = rx.search(line.text)
            if m and not (ex and ex.search(line.text[: m.start() + 1])):
                tail = line.text[m.end():]
                d = DATE_RE.search(tail)
                if d and parse_date(d.group(0)):
                    return F(d.group(0), line.confidence * LABEL_FACTOR)
    cands = [(line, d) for line in lines for d in DATE_RE.finditer(line.text) if parse_date(d.group(0))]
    if not cands:
        return F(None, 0)
    line, d = cands[-1] if last else cands[0]
    return F(d.group(0), line.confidence * (GUESS_FACTOR + 0.2))


def parse_item_row(text: str, conf: float, qty_first: bool = False) -> Optional[dict[str, Any]]:
    t = re.sub(r"^\|?\s*\d{1,2}[.)]?\s+(?=[A-Za-z])", "", text.strip())  # row number
    toks = t.split()
    nums: list[str] = []
    while toks and (MONEY_RE.fullmatch(toks[-1].strip("|")) or toks[-1] in {"-", "|"}):
        tok = toks.pop().strip("|")
        if tok:
            nums.insert(0, tok)
    desc = " ".join(toks).strip(" |:")
    qty = None
    if qty_first:
        m = re.match(r"^(\d{1,3})\s+(.+)$", desc)
        if m:
            qty, desc = m.group(1), m.group(2)
    if not desc or not re.search(r"[A-Za-z]{2,}", desc) or not nums:
        return None
    values = [None if n == "-" else parse_number(n) for n in nums]
    total = values[-1]
    if total is None:
        return None
    item = {"description": F(desc, conf * 0.85), "total": F(total, conf * 0.85),
            "quantity": F(parse_number(qty), conf * 0.8) if qty else F(None, 0),
            "unit_price": F(None, 0), "discount": F(None, 0), "is_adjustment": total < 0}
    if len(values) >= 3:
        item["quantity"] = F(values[0], conf * 0.8)
        item["unit_price"] = F(values[1], conf * 0.8)
        if len(values) >= 4:
            item["discount"] = F(values[2] or 0.0, conf * 0.7) if values[2] else F(None, 0)
    elif len(values) == 2:
        item["unit_price"] = F(values[0], conf * 0.7)
    return item


def _items_between(lines: list[OcrLine], start: int, end: int, qty_first: bool = False) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending: Optional[OcrLine] = None
    for line in lines[start:end]:
        t = line.text.strip()
        # two-line receipt rows: "Roti Tawar" / "3 x 15.900   47.700"
        m = re.match(rf"^(\d+(?:[.,]\d+)?)\s*[xX@]\s*({MONEY})\s+({MONEY})$", t)
        if m and pending is not None:
            c = min(pending.confidence, line.confidence) * 0.85
            items.append({"description": F(pending.text.strip(), c), "quantity": F(parse_number(m[1]), c),
                          "unit_price": F(parse_number(m[2]), c), "discount": F(None, 0),
                          "total": F(parse_number(m[3]), c), "is_adjustment": False})
            pending = None
            continue
        row = parse_item_row(t, line.confidence, qty_first=qty_first)
        if row:
            items.append(row)
            pending = None
        elif re.search(r"[A-Za-z]{3,}", t) and not money_tokens(t):
            pending = line
    return items


def _parties(lines: list[OcrLine], buyer_marker: str, stop: int) -> tuple[dict[str, Any], dict[str, Any]]:
    label_rx = re.compile(r"^(nama|npwp|alamat|nik)\b\.?\s*:?\s*(.*)$", re.I)
    buyer_line = None
    for i, line in enumerate(lines[:stop]):
        if re.search(buyer_marker, line.text, re.I):
            buyer_line = i
            break
    seller: dict[str, Any] = {}
    buyer: dict[str, Any] = {}
    for i, line in enumerate(lines[:stop]):
        segs = segments(line.text)
        found: list[tuple[str, str]] = []
        k = 0
        while k < len(segs):
            m = label_rx.match(segs[k])
            if m:
                label, val = m[1].lower(), m[2].strip(" :")
                if not val and k + 1 < len(segs) and not label_rx.match(segs[k + 1]):
                    val = segs[k + 1]
                    k += 1
                if val:
                    found.append((label, val))
            k += 1
        for j, (label, val) in enumerate(found):
            dup = sum(1 for l2, _ in found if l2 == label) > 1
            if dup:
                target = seller if j == next(n for n, (l2, _) in enumerate(found) if l2 == label) else buyer
            elif label == "nik":
                target = buyer
            else:
                target = buyer if buyer_line is not None and i > buyer_line else seller
                if buyer_line is not None and i == buyer_line:
                    target = buyer
            key = "address" if label == "alamat" else "name" if label == "nama" else label
            if key in ("npwp", "nik"):
                val = clean_id(val)
            if key not in target:
                target[key] = F(val, line.confidence * LABEL_FACTOR)
    return seller, buyer


# ---------------------------------------------------------------- per type

def extract_faktur(ocr: OcrResult) -> dict[str, Any]:
    lines = ocr.lines
    text = ocr.text
    out: dict[str, Any] = {}
    m = re.search(r"\d{3}\.\d{3}-\d{2}\.\d{8}", text) or re.search(r"(?<!\d)\d{17}(?!\d)", text)
    if m:
        line = next(l for l in lines if m.group(0) in l.text)
        out["tax_invoice_number"] = F(m.group(0), line.confidence * LABEL_FACTOR)
    out["date"] = find_date(lines, r"tanggal", last=True)
    header = find_line(lines, r"nama\s+barang|uraian|barang\s*/\s*jasa|harga\s+satuan")
    if header:
        header_i = header[0]
    else:  # header unreadable: the table starts at the first row that parses as an item after the parties
        last_party = max((i for i, l in enumerate(lines) if re.search(r"\b(npwp|alamat|nik)\b", l.text, re.I)),
                         default=0)
        header_i = next((i - 1 for i in range(last_party + 1, len(lines))
                         if parse_item_row(lines[i].text, 1.0) and len(money_tokens(lines[i].text)) >= 3), len(lines))
    seller, buyer = _parties(lines, r"pembeli", header_i)
    out["seller"], out["buyer"] = seller, buyer
    totals = find_line(lines, r"harga\s+jual\s*/\s*penggantian", need_amount=True, start=header_i + 1)
    end_i = totals[0] if totals else len(lines)
    out["line_items"] = _items_between(lines, header_i + 1, end_i)
    out["total"] = amount_field(lines, r"harga\s+jual\s*/\s*penggantian(?:\s*/\s*uang\s+muka\s*/\s*termin)?",
                                start=header_i + 1)
    out["discount"] = amount_field(lines, r"potongan\s+harga")
    out["dpp"] = amount_field(lines, r"dasar\s+pengenaan\s+pajak(?:\s*\(dpp\))?")
    out["ppn"] = amount_field(lines, r"(?:total|jumlah)\s+ppn(?!\s*bm)", exclude=r"ppnbm")
    out["ppnbm"] = amount_field(lines, r"ppnbm(?:\s*\([^)]*\))?")
    return out


BANK_RX = r"\bbank\s+(?!no\b|rek\b)[a-z]+(?:\s+(?!no\b|rek\b|a\.?n\b)[a-z]+){0,2}"
META_LABEL = re.compile(r"\b(tanggal|tgl|jatuh\s+tempo|due|nomor|mata\s+uang|invoice|no\.)\b", re.I)


def extract_invoice(ocr: OcrResult) -> dict[str, Any]:
    lines = ocr.lines
    text = ocr.text
    out: dict[str, Any] = {}
    num = None
    for line in lines:
        m = re.search(r"(?:invoice\s*#|invoice\s*no\.?|\bno\.|\bnomor\b|#)\s*:?\s*([A-Z0-9][A-Z0-9/\-.]{3,})",
                      line.text, re.I)
        if m and re.search(r"\d", m[1]):
            num = F(m[1].strip(".:"), line.confidence * LABEL_FACTOR)
            break
    out["invoice_number"] = num or F(None, 0)
    out["date"] = find_date(lines, r"\b(?:tanggal|tgl|date)\b", exclude=r"jatuh|due")
    out["due_date"] = find_date(lines, r"jatuh\s+tempo|\bdue\b") if re.search(r"jatuh\s+tempo|\bdue\b", text, re.I) \
        else F(None, 0)

    vendor: dict[str, Any] = {}
    customer: dict[str, Any] = {}
    dari = find_line(lines, r"^\s*dari\b")
    if dari and dari[0] + 1 < len(lines):
        nxt = lines[dari[0] + 1]
        segs = segments(nxt.text)
        if segs:
            vendor["name"] = F(segs[0], nxt.confidence * LABEL_FACTOR)
        if dari[0] + 2 < len(lines):
            a = lines[dari[0] + 2]
            sa = segments(a.text)
            if sa:
                vendor["address"] = F(sa[0], a.confidence * 0.8)
    else:
        for i, line in enumerate(lines[:4]):
            segs = segments(line.text)
            if segs and not re.search(r"invoice|faktur", segs[0], re.I) and re.search(r"[A-Za-z]{3,}", segs[0]):
                vendor["name"] = F(segs[0], line.confidence * GUESS_FACTOR)
                if i + 1 < len(lines):
                    sa = segments(lines[i + 1].text)
                    if sa and not re.search(r"npwp|invoice", sa[0], re.I):
                        vendor["address"] = F(sa[0], lines[i + 1].confidence * GUESS_FACTOR)
                break
    npwp = find_line(lines, r"npwp")
    if npwp:
        m = re.search(r"npwp\s*:?\s*([\d.\-\s]{15,24})", npwp[1].text, re.I)
        if m:
            vendor["npwp"] = F(clean_id(m[1]), npwp[1].confidence * LABEL_FACTOR)

    cust_rx = r"kepada\s+yth\.?|ditagihkan\s+kepada|customer\s*:?|bill\s+to"
    for i, line in enumerate(lines):
        segs = segments(line.text)
        for k, s in enumerate(segs):
            m = re.search(cust_rx, s, re.I)
            if not m:
                continue
            inline = s[m.end():].strip(" :")
            if inline:
                customer["name"] = F(inline, line.confidence * LABEL_FACTOR)
            elif i + 1 < len(lines):
                ns = [x for x in segments(lines[i + 1].text) if not META_LABEL.search(x)]
                if ns:
                    col = min(k, len(ns) - 1) if len(segs) > 1 else 0
                    customer["name"] = F(ns[col], lines[i + 1].confidence * LABEL_FACTOR)
                    if i + 2 < len(lines):
                        na = [x for x in segments(lines[i + 2].text) if not META_LABEL.search(x)]
                        if na:
                            customer["address"] = F(na[min(col, len(na) - 1)], lines[i + 2].confidence * 0.7)
            break
        if "name" in customer:
            break
    alamat = find_line(lines, r"alamat\s+tagihan")
    if alamat:
        customer["address"] = F(re.split(r"alamat\s+tagihan\s*:?", alamat[1].text, flags=re.I)[-1].strip(),
                                alamat[1].confidence * LABEL_FACTOR)
    out["vendor"], out["customer"] = vendor, customer

    header = find_line(lines, r"deskripsi|uraian|\bitem\b|description")
    sub = find_line(lines, r"sub\s*-?\s*total", need_amount=True)
    if header:
        out["line_items"] = _items_between(lines, header[0] + 1, sub[0] if sub else len(lines))
    out["subtotal"] = amount_field(lines, r"sub\s*-?\s*total")
    out["discount"] = amount_field(lines, r"diskon|potongan|\bdisc\b")
    out["tax"] = amount_field(lines, r"\bppn\b(?:\s*\d+\s*%)?|\bpajak\b|\btax\b", exclude=r"faktur\s+pajak")
    out["total"] = amount_field(lines, r"grand\s+total(?:\s+[A-Z]{3})?|total\s+tagihan|\btotal\b",
                                exclude=r"sub\s*-?\s*total", last=True)
    cur = "USD" if re.search(r"\bUSD\b|\$", text) else "IDR" if re.search(r"\bIDR\b|\bRp\b", text, re.I) else None
    out["currency"] = F(cur, 0.85 if cur else 0)
    bank: dict[str, Any] = {}
    b = find_line(lines, BANK_RX)
    if b:
        m = re.search(BANK_RX, b[1].text, re.I)
        bank["bank_name"] = F(m.group(0).strip(), b[1].confidence * 0.8)
        window = "\n".join(l.text for l in lines[b[0]: b[0] + 3])
        acc = re.search(r"(?:no\.?\s*rek\.?|rek\.?|no\.)\s*:?\s*(\d[\d\s-]{7,20}\d)", window, re.I) or \
            re.search(r"(?<!\d)(\d{10,16})(?!\d)", window)
        if acc:
            bank["account_number"] = F(acc[1], b[1].confidence * 0.8)
        an = re.search(r"a\s*[./]\s*n\.?\s*([^\n]+?)(?:$|\s{3,})", window, re.I | re.M) or \
            re.search(r"\(([^)\n]+)\)", window)
        if an:
            bank["account_name"] = F(an[1].strip(" ."), b[1].confidence * 0.7)
    out["bank_account"] = bank
    return out


PAY_RX = r"\b(tunai|cash|qris|ovo|gopay|shopeepay|dana|debit\s+[a-z]+|kartu\s+kredit|credit\s+card|debit)\b"


def extract_receipt(ocr: OcrResult) -> dict[str, Any]:
    lines = [l for l in ocr.lines if l.text.strip(" -=*_")]
    out: dict[str, Any] = {}
    if lines:
        out["store_name"] = F(segments(lines[0].text)[0] if segments(lines[0].text) else lines[0].text,
                              lines[0].confidence * GUESS_FACTOR * 1.3)
    if len(lines) > 1 and not DATE_RE.search(lines[1].text):
        out["store_address"] = F(lines[1].text.strip(), lines[1].confidence * GUESS_FACTOR)
    out["date"] = find_date(lines)
    t = None
    for line in lines:
        m = TIME_RE.search(DATE_RE.sub(" ", line.text))
        if m:
            t = F(m.group(0), line.confidence * 0.8)
            break
    out["time"] = t or F(None, 0)
    start = 0
    for i, line in enumerate(lines[:12]):
        if DATE_RE.search(line.text) or re.search(r"kasir|cashier|server|no\s*[:.]|order", line.text, re.I):
            start = i + 1
    sub = find_line(lines, r"sub\s*-?\s*total", need_amount=True)
    tot = find_line(lines, r"\btotal\b", need_amount=True, exclude=r"sub\s*-?\s*total")
    end = sub[0] if sub else (tot[0] if tot else len(lines))
    items = _items_between(lines, start, end, qty_first=True)
    out["line_items"] = [it for it in items if not re.search(r"kasir|server|order|no\s*:", it["description"]["value"] or "", re.I)]
    out["subtotal"] = amount_field(lines, r"sub\s*-?\s*total")
    out["service_charge"] = amount_field(lines, r"service(?:\s+charge)?|\bsc\b")
    out["tax"] = amount_field(lines, r"\bpb1\b(?:\s*\d+\s*%)?|pajak(?:\s+resto)?|\btax\b|\bppn\b")
    out["discount"] = amount_field(lines, r"^\s*(?:diskon|discount)\b(?!\s+member)")
    out["total"] = amount_field(lines, r"\btotal\b", exclude=r"sub\s*-?\s*total|total\s+item|total\s+qty")
    pay = None
    for line in lines[end:]:
        m = re.search(PAY_RX, line.text, re.I)
        if m:
            pay = F(m.group(0).upper(), line.confidence * 0.8)
            break
    out["payment_method"] = pay or F(None, 0)
    return out


EXTRACTORS = {"faktur_pajak": extract_faktur, "invoice": extract_invoice, "receipt": extract_receipt}


def extract_baseline(doc_type: str, ocr: OcrResult) -> dict[str, Any]:
    return EXTRACTORS[doc_type](ocr)
