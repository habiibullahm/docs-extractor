import io
import time

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("DOCEX_STORAGE_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("DOCEX_APPROACH", "A")
    monkeypatch.setenv("QUEUE_BACKEND", "inline")
    import importlib

    import docex.api.app as app_mod
    import docex.db.models as models

    models._Session = None
    importlib.reload(app_mod)
    with TestClient(app_mod.app) as c:
        yield c


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


def _wait(client, doc_id):
    for _ in range(120):
        d = client.get(f"/documents/{doc_id}").json()
        if d["status"] in ("done", "failed"):
            return d
        time.sleep(0.25)
    raise AssertionError("timeout")


def test_upload_process_review_metrics(client):
    r = client.post("/documents", files={"file": ("x.png", _png(), "image/png")}, data={"doc_type": "receipt"})
    assert r.status_code == 202
    doc_id = r.json()["job_id"]
    d = _wait(client, doc_id)
    assert d["status"] == "done" and d["route"] == "review"
    assert client.get(f"/documents/{doc_id}/preview").headers["content-type"] == "image/png"
    assert any(q["id"] == doc_id for q in client.get("/review/queue").json())
    r = client.post(f"/review/{doc_id}", json={"action": "correct", "values": {"store_name": "Toko A", "total": "Rp 12.500"}})
    assert r.status_code == 200 and "store_name" in r.json()["changed_fields"]
    assert not any(q["id"] == doc_id for q in client.get("/review/queue").json())
    d = client.get(f"/documents/{doc_id}").json()
    assert d["review"]["corrected_values"]["total"] == 12500.0
    m = client.get("/metrics").json()
    assert m["documents"] == 1 and m["reviews"] == {"correct": 1}


def test_rejects_bad_type_and_size(client):
    r = client.post("/documents", files={"file": ("x.txt", b"hello world", "text/plain")})
    assert r.status_code == 415
    big = b"%PDF-" + b"0" * (11 * 1024 * 1024)
    assert client.post("/documents", files={"file": ("x.pdf", big, "application/pdf")}).status_code == 413


def test_llm_approach_unavailable_without_key(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = client.post("/documents", files={"file": ("x.png", _png(), "image/png")}, data={"approach": "C"})
    assert r.status_code == 400


def test_ui_and_health_for_ui(client):
    r = client.get("/")
    assert r.status_code == 200 and "meja cek dokumen" in r.text
    assert client.get("/web/app.js").status_code == 200
    h = client.get("/health").json()
    assert h["available_approaches"][0] == "A"
    assert "seller.npwp" in h["critical_fields"]["faktur_pajak"]
    samples = client.get("/samples").json()
    if samples:
        img = client.get(f"/samples/{samples[0]['name']}/image")
        assert img.headers["content-type"] == "image/jpeg"
