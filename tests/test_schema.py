import json

import pytest
from pydantic import ValidationError

from docex.normalize import normalize_document
from docex.schemas import FakturPajak, Invoice, Receipt, plain
from docex.schemas.documents import from_plain
from docex.schemas.llm_schema import extraction_schema


def test_defaults_are_null_not_invented():
    doc = FakturPajak()
    assert doc.total.value is None and doc.total.confidence == 0.0
    assert plain(doc)["seller"]["npwp"] is None


def test_confidence_bounds_and_clamping():
    inv = Invoice.model_validate({"total": {"value": 10.0, "confidence": 1.7}})
    assert inv.total.confidence == 1.0
    with pytest.raises(ValidationError):
        Invoice.model_validate({"unknown_field": 1})


def test_round_trip_ground_truth(tmp_path):
    gt = {"doc_type": "receipt", "store_name": "Kopi Kenangan", "date": "2025-01-12", "total": 25000.0,
          "line_items": [{"description": "Es Kopi", "quantity": 1.0, "unit_price": 25000.0, "discount": None,
                          "total": 25000.0, "is_adjustment": False}]}
    doc = from_plain("receipt", gt)
    assert isinstance(doc, Receipt)
    p = plain(doc)
    assert p["store_name"] == "Kopi Kenangan" and p["line_items"][0]["total"] == 25000.0


@pytest.mark.parametrize("doc_type", ["faktur_pajak", "invoice", "receipt"])
def test_llm_schema_is_strict(doc_type):
    s = extraction_schema(doc_type)

    def walk(node):
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for v in node["properties"].values():
                walk(v)
        if node.get("type") == "array":
            walk(node["items"])
        assert "$ref" not in node

    walk(s)
    json.dumps(s)


def test_normalize_raw_llm_output():
    raw = {"invoice_number": {"value": "INV/2025/I/001", "confidence": 0.95},
           "date": {"value": "12 Januari 2025", "confidence": 0.9},
           "total": {"value": "Rp 1.250.000,50", "confidence": 0.9},
           "vendor": {"npwp": {"value": "01.234.567.8-901.234", "confidence": 0.8}},
           "currency": "Rp"}
    doc = normalize_document("invoice", raw)
    assert doc.date.value == "2025-01-12"
    assert doc.total.value == 1250000.50
    assert doc.vendor.npwp.value == "012345678901234"
    assert doc.currency.value == "IDR" and doc.currency.confidence == 0.5  # bare value -> default confidence
    assert doc.due_date.value is None
