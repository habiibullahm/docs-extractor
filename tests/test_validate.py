from datetime import date

from docex.scoring.score import score_document
from docex.schemas.documents import from_plain
from docex.validate.rules import Validator

V = Validator(today=date(2026, 10, 20))


def faktur(**kw):
    base = {"tax_invoice_number": "0100002312345678", "date": "2025-03-01",
            "seller": {"name": "PT A", "npwp": "0123456789012345"}, "buyer": {"name": "PT B", "npwp": "123456789012345"},
            "line_items": [{"description": "Jasa", "total": 1_200_000.0}],
            "total": 1_200_000.0, "discount": 0.0, "dpp": 1_100_000.0, "ppn": 132_000.0, "ppnbm": 0.0}
    base.update(kw)
    return base


def rules(issues):
    return sorted(i.rule for i in issues)


def test_valid_faktur_2025_dpp_nilai_lain():
    assert V.validate("faktur_pajak", faktur()) == []


def test_wrong_ppn_rate_detected():
    assert "ppn_rate" in rules(V.validate("faktur_pajak", faktur(ppn=140_000.0)))


def test_old_rate_for_2024():
    ok = faktur(date="2024-06-01", dpp=1_200_000.0, ppn=132_000.0)
    assert V.validate("faktur_pajak", ok) == []


def test_line_sum_and_npwp_and_future_date():
    bad = faktur(total=1_300_000.0, date="2027-01-01")
    bad["seller"]["npwp"] = "12345"
    r = rules(V.validate("faktur_pajak", bad))
    assert "line_items_sum" in r and "npwp_format" in r and "date_plausible" in r


def test_invoice_arithmetic_and_due_date():
    inv = {"invoice_number": "INV-1", "date": "2025-01-10", "due_date": "2025-01-01", "vendor": {"name": "V"},
           "customer": {"name": "C"}, "line_items": [{"total": 100.0}, {"total": 50.0}], "subtotal": 150.0,
           "discount": 0.0, "tax": 16.5, "total": 170.0}
    r = rules(V.validate("invoice", inv))
    assert "totals_arithmetic" in r and "due_after_issue" in r


def test_receipt_with_adjustment_rows():
    rec = {"store_name": "Toko", "date": "2025-01-10", "line_items": [{"total": 100_000.0}, {"total": -10_000.0}],
           "subtotal": 90_000.0, "tax": 9_000.0, "total": 99_000.0}
    assert V.validate("receipt", rec) == []


def test_score_and_route():
    doc = from_plain("faktur_pajak", faktur(), confidence=0.95).model_dump()
    s = score_document("faktur_pajak", doc, [], 0.99, threshold=0.8)
    assert s.route == "auto_approved" and s.score == 0.95
    issues = V.validate("faktur_pajak", faktur(ppn=1.0))
    s = score_document("faktur_pajak", doc, issues, 0.99, threshold=0.8)
    assert s.route == "review" and s.n_errors >= 1
    doc["document_inconsistent"] = True
    assert score_document("faktur_pajak", doc, [], 0.99, threshold=0.0).route == "review"
