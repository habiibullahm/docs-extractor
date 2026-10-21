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


def _expected(issues, rule):
    return next(i for i in issues if i.rule == rule).expected


def test_ppn_issue_carries_the_calculation():
    issues = V.validate("faktur_pajak", faktur(ppn=140_000.0))
    e = _expected(issues, "ppn_rate")
    assert e["field"] == "ppn" and e["expected"] == 132_000.0 and e["actual"] == 140_000.0
    assert e["difference"] == 8_000.0 and e["tolerance"] == 1.0
    assert e["terms"] == [{"value": 1_100_000.0, "field": "dpp"}, {"value": 0.12, "op": "x", "label": "rate"}]


def test_message_text_is_unchanged_by_expected():
    i = next(i for i in V.validate("faktur_pajak", faktur(ppn=140_000.0)) if i.rule == "ppn_rate")
    assert i.message == "PPN 140,000 != DPP 1,100,000 x rate [0.12] for the document date"


def test_invoice_total_issue_lists_each_term():
    inv = {"invoice_number": "INV-1", "date": "2025-01-10", "vendor": {"name": "V"}, "customer": {"name": "C"},
           "line_items": [{"total": 150.0}], "subtotal": 150.0, "discount": 10.0, "tax": 16.5, "total": 170.0}
    e = _expected(V.validate("invoice", inv), "totals_arithmetic")
    assert e["field"] == "total" and e["expected"] == 156.5 and e["difference"] == 13.5
    assert [(t.get("op"), t["field"]) for t in e["terms"]] == [(None, "subtotal"), ("-", "discount"), ("+", "tax")]


def test_issue_without_a_calculation_has_no_expected_key():
    bad = faktur()
    bad["seller"]["npwp"] = "12345"
    issue = next(i for i in V.validate("faktur_pajak", bad) if i.rule == "npwp_format")
    assert issue.expected is None and "expected" not in issue.to_dict()


def test_line_items_sum_issue_expected():
    e = _expected(V.validate("faktur_pajak", faktur(total=1_300_000.0)), "line_items_sum")
    assert e["field"] == "total" and e["expected"] == 1_200_000.0 and e["actual"] == 1_300_000.0
