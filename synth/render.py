"""Render synthetic documents: Jinja2 HTML templates -> PDF (Chromium) -> PNG."""
from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATE_DIR = Path(__file__).parent / "templates"

TEMPLATES = {
    "faktur_pajak": ["faktur_efaktur", "faktur_coretax"],
    "invoice": ["invoice_modern", "invoice_classic", "invoice_compact"],
    "receipt": ["receipt_thermal", "receipt_cafe"],
}

BULAN = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus",
         "September", "Oktober", "November", "Desember"]
BULAN_SINGKAT = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def fmt_money(x: Any, style: str) -> str:
    if x is None:
        return "-"
    neg = x < 0
    x = abs(float(x))
    if style == "en_dec":
        s = f"{x:,.2f}"
    elif style == "id_dec":
        s = f"{x:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")
    else:  # "id"
        s = f"{x:,.0f}".replace(",", ".")
    return f"-{s}" if neg else s


def fmt_date(iso: str, style: str) -> str:
    y, m, d = (int(p) for p in iso.split("-"))
    return {
        "long": f"{d} {BULAN[m - 1]} {y}",
        "short_month": f"{d:02d} {BULAN_SINGKAT[m - 1]} {y}",
        "slash": f"{d:02d}/{m:02d}/{y}",
        "slash2": f"{d:02d}/{m:02d}/{y % 100:02d}",
        "dash": f"{d:02d}-{m:02d}-{y}",
        "iso": iso,
    }[style]


def fmt_npwp(digits: str | None) -> str:
    if not digits:
        return "-"
    if len(digits) == 15:
        d = digits
        return f"{d[0:2]}.{d[2:5]}.{d[5:8]}.{d[8]}-{d[9:12]}.{d[12:15]}"
    return digits


def fmt_qty(q: Any) -> str:
    if q is None:
        return ""
    return str(int(q)) if float(q).is_integer() else str(q).replace(".", ",")


_SATUAN = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]


def terbilang(n: int) -> str:
    n = int(n)
    if n < 12:
        return _SATUAN[n]
    if n < 20:
        return terbilang(n - 10) + " belas"
    if n < 100:
        return (terbilang(n // 10) + " puluh " + terbilang(n % 10)).strip()
    if n < 200:
        return ("seratus " + terbilang(n - 100)).strip()
    if n < 1000:
        return (terbilang(n // 100) + " ratus " + terbilang(n % 100)).strip()
    if n < 2000:
        return ("seribu " + terbilang(n - 1000)).strip()
    for div, word in ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (10**3, "ribu")):
        if n >= div:
            return (terbilang(n // div) + f" {word} " + terbilang(n % div)).strip()
    return ""


def make_env(money_style: str, date_style: str) -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(["html"]))
    env.filters["money"] = lambda x: fmt_money(x, money_style)
    env.filters["tanggal"] = lambda d: fmt_date(d, date_style)
    env.filters["npwp"] = fmt_npwp
    env.filters["qty"] = fmt_qty
    return env


def choose_style(doc_type: str, template: str, currency: str | None, rng: random.Random) -> dict[str, str]:
    if currency == "USD":
        money = "en_dec"
    elif doc_type == "receipt":
        money = rng.choice(["id", "id", "en_dec"]) if template == "receipt_cafe" else "id"
    else:
        money = rng.choice(["id", "id_dec"])
    date_style = rng.choice(["long", "short_month", "slash", "slash2", "dash", "iso"] if doc_type != "faktur_pajak"
                            else ["long", "long", "slash"])
    if money == "en_dec" and currency != "USD":
        money = "id"  # avoid ambiguous en-style IDR amounts
    return {"money": money, "date": date_style}


def render_html(template: str, gt: dict[str, Any], extra: dict[str, Any], style: dict[str, str]) -> str:
    env = make_env(style["money"], style["date"])
    g = dict(gt)
    g["currency_symbol"] = "Rp" if gt.get("currency") == "IDR" else "$"
    r = dict(extra)
    r.setdefault("color", "#2b5797")
    if gt.get("total") is not None:
        r["terbilang"] = (terbilang(int(gt["total"])) + " rupiah").capitalize() if gt.get("currency") == "IDR" else "-"
    return env.get_template(f"{template}.html").render(g=g, r=r)


class PdfRenderer:
    """Thin wrapper around a headless Chromium (Playwright) instance."""

    def __init__(self) -> None:
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        exe = os.environ.get("CHROMIUM_PATH")
        if not exe:
            for cand in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux/chrome"):
                exe = str(cand)
                break
        kwargs = {"executable_path": exe} if exe else {}
        self._browser = self._pw.chromium.launch(**kwargs)
        self._page = self._browser.new_page()

    def to_pdf(self, html: str, out: Path, receipt: bool = False) -> None:
        self._page.set_content(html, wait_until="load")
        if receipt:
            height = self._page.evaluate("document.documentElement.scrollHeight")
            self._page.pdf(path=str(out), width="340px", height=f"{height + 30}px", print_background=True)
        else:
            self._page.pdf(path=str(out), prefer_css_page_size=True, print_background=True)

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


def pdf_to_png(pdf: Path, out: Path, dpi: int = 150) -> None:
    with fitz.open(pdf) as doc:
        pix = doc[0].get_pixmap(dpi=dpi)
        pix.save(str(out))
