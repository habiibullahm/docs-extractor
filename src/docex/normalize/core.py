"""Normalization of Indonesian number, date and ID formats."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Optional

MONTHS = {
    "januari": 1, "jan": 1,
    "februari": 2, "feb": 2, "pebruari": 2, "peb": 2,
    "maret": 3, "mar": 3,
    "april": 4, "apr": 4,
    "mei": 5, "may": 5,
    "juni": 6, "jun": 6,
    "juli": 7, "jul": 7,
    "agustus": 8, "agu": 8, "ags": 8, "agt": 8, "aug": 8,
    "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "oct": 10,
    "november": 11, "nov": 11, "nop": 11,
    "desember": 12, "des": 12, "dec": 12,
    "january": 1, "february": 2, "march": 3, "june": 6, "july": 7,
    "august": 8, "october": 10, "december": 12,
}

_NUM_CLEAN = re.compile(r"[^\d,.\-()]")


def parse_number(raw: Any) -> Optional[float]:
    """Parse Indonesian-formatted amounts.

    "Rp 1.250.000,50" -> 1250000.5 ; "1,250,000.50" -> 1250000.5 ; "(5.000)" -> -5000
    "12.500" -> 12500 (dot followed by exactly 3 digits is a thousands separator).
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    s = re.sub(r"[,.]-$", "", s)  # "Rp 1.000.000,-" means no cents
    negative = s.startswith("-") or (s.startswith("(") and s.endswith(")")) or bool(re.search(r"\d-$", s))
    s = _NUM_CLEAN.sub("", s).strip("()-")
    if not s or not re.search(r"\d", s):
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        # the right-most separator is the decimal separator
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) in (1, 2):
            s = s.replace(",", ".")  # Indonesian decimal comma
        else:
            s = s.replace(",", "")  # thousands comma
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = s.replace(".", "")  # thousands dots
        # else: single dot with 1-2 decimals -> decimal point
    try:
        value = float(s)
    except ValueError:
        return None
    return -value if negative else value


def _year(y: int) -> int:
    if y < 100:
        return 2000 + y if y < 70 else 1900 + y
    return y


def parse_date(raw: Any) -> Optional[str]:
    """Return ISO YYYY-MM-DD. Supports "12 Januari 2025", "12/01/25", "2025-01-12",
    "12-Jan-2025", "Jan 12, 2025". Day-first is assumed for numeric dates."""
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s:
        return None
    s = re.sub(r"^(senin|selasa|rabu|kamis|jumat|jum'at|sabtu|minggu|ahad)[,\s]+", "", s)
    candidates: list[tuple[int, int, int]] = []
    m = re.search(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        candidates.append((int(m[1]), int(m[2]), int(m[3])))
    m = m or None
    if not candidates:
        m = re.search(r"(\d{1,2})[-/.\s]+([a-z]{3,9})\.?[-/.,\s]+(\d{2,4})", s)
        if m and m[2] in MONTHS:
            candidates.append((_year(int(m[3])), MONTHS[m[2]], int(m[1])))
    if not candidates:
        m = re.search(r"([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})", s)
        if m and m[1] in MONTHS:
            candidates.append((int(m[3]), MONTHS[m[1]], int(m[2])))
    if not candidates:
        m = re.search(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
        if m:
            candidates.append((_year(int(m[3])), int(m[2]), int(m[1])))
    for y, mo, d in candidates:
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            continue
    return None


def parse_time(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    m = re.search(r"(\d{1,2})[:.](\d{2})(?:[:.](\d{2}))?", str(raw))
    if not m:
        return None
    h, mi = int(m[1]), int(m[2])
    if h > 23 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}" + (f":{int(m[3]):02d}" if m[3] else "")


def digits_only(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    d = re.sub(r"\D", "", str(raw))
    return d or None


def normalize_text(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    s = re.sub(r"\s+", " ", str(raw)).strip()
    return s or None


def normalize_currency(raw: Any) -> Optional[str]:
    s = normalize_text(raw)
    if s is None:
        return None
    u = s.upper()
    if u in {"RP", "RP.", "RUPIAH", "IDR"}:
        return "IDR"
    if u in {"$", "US$", "USD"}:
        return "USD"
    return u[:3]
