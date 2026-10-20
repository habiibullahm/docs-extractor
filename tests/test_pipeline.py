"""Pipeline tests with a mocked LLM (no network) and the real local OCR path."""
import json
import io
from PIL import Image, ImageDraw

from docex.ingest.cord import cord_to_receipt
from docex.observability import mask_ids
from docex.pipeline.run import Pipeline
from docex.providers.llm import ImagePart, MockProvider


# CORD-v2 style gt_parse record (values invented, structure copied)
CORD_SAMPLE = {
    "menu": [
        {"nm": "Nasi Campur Bali", "cnt": "1 x", "price": "75,000"},
        {"nm": "Es Teh", "cnt": "2", "unitprice": "5,000", "price": "10,000"},
        {"nm": "Diskon", "price": "-30,454"},
    ],
    "sub_total": {"subtotal_price": "54,546", "tax_price": "5,454"},
    "total": {"total_price": "60,000", "cashprice": "100,000", "changeprice": "40,000"},
}


def _wrap(v):
    if isinstance(v, dict):
        return {k: _wrap(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return {"value": v, "confidence": 0.95}


GT = {"invoice_number": "INV-2025-001", "date": "2025-01-12", "due_date": "2025-02-11",
      "vendor": {"name": "PT Contoh", "npwp": None, "nik": None, "address": "Jl. Mawar 1"},
      "customer": {"name": "CV Pelanggan", "npwp": None, "nik": None, "address": "Jl. Melati 2"},
      "subtotal": 100000.0, "discount": 0.0, "tax": 11000.0, "total": 111000.0, "currency": "IDR",
      "bank_account": {"bank_name": None, "account_number": None, "account_name": None}}


def responder(total):
    def _r(system, content, schema):
        out = _wrap({**GT, "total": total})
        out["line_items"] = [{**_wrap({"description": "Jasa", "quantity": 1.0, "unit_price": 100000.0,
                                        "discount": None, "total": 100000.0}), "is_adjustment": False}]
        out["document_inconsistent"] = False
        return out
    return _r


def _png() -> bytes:
    im = Image.new("RGB", (1200, 800), "white")
    d = ImageDraw.Draw(im)
    d.text((50, 50), "INVOICE No. INV-2025-001  Jatuh Tempo 11/02/2025", fill="black")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def test_approach_c_with_mock_vlm_auto_approves():
    vlm = MockProvider(responder(111000.0))
    res = Pipeline("C", vlm=vlm).run(_png(), doc_type_hint="invoice", threshold=0.8)
    assert res.route == "auto_approved", res.issues
    assert res.values["total"] == 111000.0
    assert any(isinstance(p, ImagePart) for p in vlm.calls[0]["content"])
    assert res.versions["prompt"].startswith("extract_v1@")
    assert {s["name"] for s in res.trace} >= {"preprocess", "extract", "validate", "score"}


def test_repair_pass_runs_once_and_keeps_printed_values():
    vlm = MockProvider(responder(999.0))  # inconsistent total, model keeps it on repair
    res = Pipeline("C", vlm=vlm).run(_png(), doc_type_hint="invoice", threshold=0.8)
    assert res.repaired is True
    assert len(vlm.calls) == 2
    assert "failed these validation checks" in vlm.calls[1]["content"][-1]
    assert res.route == "review"


def test_approach_b_uses_ocr_text():
    llm = MockProvider(responder(111000.0))
    res = Pipeline("B", llm=llm).run(_png(), doc_type_hint="invoice")
    text = llm.calls[0]["content"][0]
    assert isinstance(text, str) and "OCR text" in text
    assert res.values["invoice_number"] == "INV-2025-001"


def test_approach_a_runs_without_credentials():
    res = Pipeline("A").run(_png())
    assert res.error is None
    assert res.usage["api_cost_usd"] == 0.0


def test_cord_conversion():
    r = cord_to_receipt(CORD_SAMPLE)
    assert r["total"] == 60000.0 and r["subtotal"] == 54546.0
    assert r["line_items"][0]["description"] == "Nasi Campur Bali" and r["line_items"][0]["total"] == 75000.0
    assert r["store_name"] is None  # CORD does not label store names


def test_mask_ids():
    assert mask_ids("NPWP 01.234.567.8-901.234 ok") == "NPWP 01***********34 ok"
    assert mask_ids("NIK 3171234567890001") == "NIK 31************01"
    assert mask_ids("total 1250000") == "total 1250000"


def test_openai_provider_strict_schema_and_parsing(monkeypatch):
    import httpx

    from docex.providers.llm import ImagePart, OpenAIProvider
    from docex.schemas.llm_schema import extraction_schema

    def strict_ok(node):
        # OpenAI strict mode: every object closed and every property required
        if isinstance(node, dict):
            if node.get("type") == "object" or "object" in (node.get("type") or []):
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for v in node.values():
                strict_ok(v)
        elif isinstance(node, list):
            for v in node:
                strict_ok(v)

    for t in ("faktur_pajak", "invoice", "receipt"):
        strict_ok(extraction_schema(t))

    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": '{"x": 1}', "refusal": None}}],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})

    monkeypatch.setenv("OPENAI_API_KEY", "test")
    p = OpenAIProvider("gpt-4.1-mini", max_tokens=500)
    p._http = httpx.Client(base_url="https://api.openai.com/v1", transport=httpx.MockTransport(handler))
    r = p.complete_json("sys", ["hello", ImagePart(b"\xff\xd8")], {"type": "object"})
    assert r.data == {"x": 1}
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert seen["body"]["messages"][1]["content"][1]["type"] == "image_url"
    assert r.usage.cost_usd > 0
