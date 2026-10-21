"""Public showcase mode: anonymous sessions, per-visitor isolation, retention and abuse caps."""
import importlib
import io
import re

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def make_client(tmp_path, monkeypatch):
    """make_client(env) reloads the app with extra env set first and returns a TestClient (one visitor)."""
    clients = []

    def make(env=None, **client_kwargs):
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
        monkeypatch.setenv("DOCEX_STORAGE_DIR", str(tmp_path / "uploads"))
        monkeypatch.setenv("DOCEX_APPROACH", "A")
        monkeypatch.setenv("QUEUE_BACKEND", "inline")
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        import docex.api.app as app_mod
        import docex.db.models as models

        models._Session = None
        importlib.reload(app_mod)
        c = TestClient(app_mod.app, **client_kwargs)
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), "white").save(buf, format="PNG")
    return buf.getvalue()


def upload(c, headers=None, **data):
    return c.post("/documents", files={"file": ("x.png", png(), "image/png")}, data=data or {"doc_type": "receipt"},
                  headers=headers)


# ------------------------------------------------------------------ session cookie

def test_new_visitor_gets_cookie(make_client):
    c = make_client()
    cookie = c.get("/health").headers["set-cookie"]
    assert re.search(r"docex_sid=[0-9a-f]{32};", cookie)
    low = cookie.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low and "secure" not in low
    assert "max-age=7200" in low


def test_valid_cookie_is_kept_and_bad_ones_replaced(make_client):
    c = make_client()
    c.get("/health")
    assert "set-cookie" not in c.get("/health").headers  # the jar sends the cookie back, nothing new is issued
    for bad in ["x" * 1000, "ABCDEF0123456789ABCDEF0123456789", "z" * 32, "ab" * 15]:
        other = TestClient(c.app)
        r = other.get("/health", headers={"Cookie": f"docex_sid={bad}"})
        assert re.search(r"docex_sid=[0-9a-f]{32};", r.headers["set-cookie"]), bad


def test_secure_only_on_https(make_client):
    c = make_client(base_url="https://testserver")
    assert "secure" in c.get("/health").headers["set-cookie"].lower()


def test_forwarded_headers_ignored_unless_trusted(make_client):
    c = make_client({"DOCEX_TRUST_PROXY": "false"})
    assert "secure" not in c.get("/health", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"].lower()
    t = make_client({"DOCEX_TRUST_PROXY": "true"})
    assert "secure" in t.get("/health", headers={"X-Forwarded-Proto": "https"}).headers["set-cookie"].lower()


# ------------------------------------------------------------------ client address for the rate limit

def test_rate_limit_per_forwarded_ip_only_when_trusted(make_client):
    c = make_client({"DOCEX_TRUST_PROXY": "true", "DOCEX_LIMITS__RATE_LIMIT": "2/minute"})
    a = {"X-Forwarded-For": "1.1.1.1, 10.0.0.1"}
    assert [upload(c, a).status_code for _ in range(3)] == [202, 202, 429]
    assert upload(c, {"X-Forwarded-For": "2.2.2.2"}).status_code == 202

    u = make_client({"DOCEX_TRUST_PROXY": "false", "DOCEX_LIMITS__RATE_LIMIT": "2/minute"})
    assert [upload(u, a).status_code for _ in range(2)] == [202, 202]
    assert upload(u, {"X-Forwarded-For": "2.2.2.2"}).status_code == 429  # header ignored: same bucket


def test_junk_forwarded_for_falls_back_to_socket_address(make_client):
    c = make_client({"DOCEX_TRUST_PROXY": "true", "DOCEX_LIMITS__RATE_LIMIT": "2/minute"})
    assert upload(c).status_code == 202
    assert upload(c, {"X-Forwarded-For": "unknown"}).status_code == 202
    assert upload(c, {"X-Forwarded-For": ""}).status_code == 429


# ------------------------------------------------------------------ owner_id column

def _old_documents_table(path):
    import sqlite3

    con = sqlite3.connect(path)
    con.execute("""CREATE TABLE documents (id VARCHAR(32) PRIMARY KEY, filename VARCHAR(255) NOT NULL,
        content_type VARCHAR(100) NOT NULL, sha256 VARCHAR(64) NOT NULL, size_bytes INTEGER NOT NULL,
        storage_path VARCHAR(500) NOT NULL, status VARCHAR(20) NOT NULL, approach VARCHAR(4) NOT NULL,
        doc_type_hint VARCHAR(20), error TEXT, created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""")
    con.execute("INSERT INTO documents VALUES ('old', 'a.png', 'image/png', 'x', 1, '/tmp/a.png', 'done', 'A', "
                "NULL, NULL, '2026-10-20 10:00:00', '2026-10-20 10:00:00')")
    con.commit()
    con.close()


def test_owner_id_is_stored(tmp_path, monkeypatch):
    import docex.db.models as models

    models._Session = None
    models.init_db(f"sqlite:///{tmp_path / 'o.db'}")
    with models.session() as s:
        s.add(models.Document(id="d1", filename="a.png", content_type="image/png", sha256="x", size_bytes=1,
                              storage_path="/tmp/a.png", approach="A", owner_id="a" * 32))
    with models.session() as s:
        assert models.get_document(s, "d1").owner_id == "a" * 32
    models._Session = None


def test_migration_adds_owner_id_to_old_database(tmp_path):
    import sqlalchemy as sa

    import docex.db.models as models

    path = tmp_path / "old.db"
    _old_documents_table(path)
    url = f"sqlite:///{path}"
    models._Session = None
    models.init_db(url)
    models.init_db(url)  # running it again is a no-op
    insp = sa.inspect(sa.create_engine(url))
    assert "owner_id" in [c["name"] for c in insp.get_columns("documents")]
    assert any("owner_id" in ix["column_names"] for ix in insp.get_indexes("documents"))
    with models.session() as s:
        old = models.get_document(s, "old")
        assert old.filename == "a.png" and old.owner_id is None
    models._Session = None


# ------------------------------------------------------------------ per-visitor isolation

def wait(c, doc_id):
    import time

    for _ in range(120):
        d = c.get(f"/documents/{doc_id}").json()
        if d["status"] in ("done", "failed"):
            return d
        time.sleep(0.25)
    raise AssertionError("timeout")


def second_visitor(c):
    return TestClient(c.app)


def test_other_visitor_gets_404_everywhere(make_client):
    a = make_client()
    b = second_visitor(a)
    doc_id = upload(a).json()["job_id"]
    wait(a, doc_id)
    assert b.get(f"/documents/{doc_id}").status_code == 404
    assert b.get(f"/documents/{doc_id}/preview").status_code == 404
    assert b.post(f"/review/{doc_id}", json={"action": "approve"}).status_code == 404
    assert b.get("/documents").json() == [] and b.get("/review/queue").json() == []
    assert [d["id"] for d in a.get("/documents").json()] == [doc_id]
    assert [q["id"] for q in a.get("/review/queue").json()] == [doc_id]
    assert a.get(f"/documents/{doc_id}/preview").status_code == 200


def test_metrics_and_export_are_per_visitor(make_client):
    a = make_client()
    b = second_visitor(a)
    doc_id = upload(a).json()["job_id"]
    wait(a, doc_id)
    a.post(f"/review/{doc_id}", json={"action": "correct", "values": {"store_name": "Toko A", "total": "12.500"}})
    ma, mb = a.get("/metrics").json(), b.get("/metrics").json()
    assert ma["documents"] == 1 and ma["reviews"] == {"correct": 1}
    assert mb["documents"] == 0 and mb["reviews"] == {} and mb["by_status"] == {}
    assert "offline_eval" in mb
    assert a.get("/export/approved/count").json()["documents"] == 1
    assert b.get("/export/approved/count").json()["documents"] == 0
    assert doc_id not in b.get("/export/approved?format=csv").content.decode("utf-8-sig")


def test_samples_are_public_but_runs_are_owned(make_client):
    a = make_client()
    b = second_visitor(a)
    samples = b.get("/samples").json()
    if not samples:
        pytest.skip("no demo samples in this checkout")
    job = a.post(f"/samples/{samples[0]['name']}").json()["job_id"]
    wait(a, job)
    assert b.get(f"/documents/{job}").status_code == 404
    assert [d["id"] for d in a.get("/documents").json()] == [job]


def test_cookieless_client_cannot_read_but_can_keep_cookie(make_client):
    app = make_client().app
    r = TestClient(app).post("/documents", files={"file": ("x.png", png(), "image/png")})
    sid = re.search(r"docex_sid=([0-9a-f]{32})", r.headers["set-cookie"]).group(1)
    doc_id = r.json()["job_id"]
    kept = TestClient(app).get(f"/documents/{doc_id}", headers={"Cookie": f"docex_sid={sid}"})
    assert kept.status_code == 200
    assert TestClient(app).get(f"/documents/{doc_id}").status_code == 404


def test_null_owner_rows_are_invisible(make_client):
    import docex.db.models as models

    a = make_client()
    with models.session() as s:
        s.add(models.Document(id="legacy", filename="old.png", content_type="image/png", sha256="x", size_bytes=1,
                              storage_path="/tmp/none.png", approach="A", status="done"))
    assert a.get("/documents/legacy").status_code == 404
    assert a.get("/documents").json() == []
    assert a.get("/metrics").json()["documents"] == 0


# ------------------------------------------------------------------ retention

def add_doc(tmp_path, doc_id, age_min, status="done", owner="a" * 32, file=True, with_children=False, approach="A"):
    from datetime import datetime, timedelta, timezone

    import docex.db.models as models

    created = datetime.now(timezone.utc) - timedelta(minutes=age_min)
    path = tmp_path / "uploads" / f"{doc_id}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    if file:
        path.write_bytes(png())
        path.with_name(f"{doc_id}.read.jpg").write_bytes(b"jpg")
    with models.session() as s:
        s.add(models.Document(id=doc_id, filename="x.png", content_type="image/png", sha256="x", size_bytes=10,
                              storage_path=str(path), approach=approach, status=status, owner_id=owner,
                              created_at=created, updated_at=created))
        if with_children:
            s.flush()
            ex = models.Extraction(document_id=doc_id, doc_type="receipt", route="review", score=0.5, approach="A",
                                   versions={}, result={})
            s.add(ex)
            s.flush()
            s.add(models.Review(document_id=doc_id, extraction_id=ex.id, action="approve"))
    return path


def doc_ids():
    import docex.db.models as models
    from sqlalchemy import select

    with models.session() as s:
        return set(s.scalars(select(models.Document.id)).all())


def test_purge_removes_files_and_rows_past_retention(make_client, tmp_path):
    from docex.db.retention import purge_expired

    make_client()
    old = add_doc(tmp_path, "old", age_min=130, with_children=True)
    young = add_doc(tmp_path, "young", age_min=10)
    assert purge_expired() == 1
    assert doc_ids() == {"young"}
    assert not old.exists() and not old.with_name("old.read.jpg").exists() and young.exists()


def test_purge_keeps_young_running_job_and_removes_stuck_one(make_client, tmp_path):
    from docex.db.retention import purge_expired

    make_client()
    add_doc(tmp_path, "running", age_min=5, status="processing")
    add_doc(tmp_path, "stuck", age_min=45, status="processing")
    add_doc(tmp_path, "queued", age_min=5, status="queued")
    assert purge_expired(retention_minutes=1) == 1
    assert doc_ids() == {"running", "queued"}


def test_purge_survives_missing_file_and_removes_null_owner_rows(make_client, tmp_path):
    from docex.db.retention import purge_expired

    make_client()
    add_doc(tmp_path, "nofile", age_min=200, file=False)
    add_doc(tmp_path, "legacy", age_min=200, owner=None)
    assert purge_expired() == 2 and doc_ids() == set()


def test_deleted_document_is_404(make_client):
    from datetime import datetime, timedelta, timezone

    import docex.db.models as models
    from docex.db.retention import purge_expired

    c = make_client()
    doc_id = upload(c).json()["job_id"]
    wait(c, doc_id)
    with models.session() as s:
        s.get(models.Document, doc_id).created_at = datetime.now(timezone.utc) - timedelta(hours=3)
    assert purge_expired() == 1
    assert c.get(f"/documents/{doc_id}").status_code == 404
    assert c.get(f"/documents/{doc_id}/preview").status_code == 404


def test_health_and_cookie_follow_configured_retention(make_client):
    c = make_client({"DOCEX_LIMITS__RETENTION_MINUTES": "90"})
    r = c.get("/health")
    assert r.json()["retention_minutes"] == 90
    assert "max-age=5400" in r.headers["set-cookie"].lower()


# ------------------------------------------------------------------ abuse caps

def as_visitor(app, sid):
    return TestClient(app, headers={"Cookie": f"docex_sid={sid}"})


def test_session_cap_counts_only_this_visitor_within_retention(make_client, tmp_path):
    a = make_client({"DOCEX_LIMITS__SESSION_MAX_DOCS": "2"})
    assert [upload(a).status_code for _ in range(3)] == [202, 202, 429]
    assert upload(a).json()["detail"] == "session_limit"
    assert upload(second_visitor(a)).status_code == 202
    add_doc(tmp_path, "c1", age_min=200, owner="c" * 32)
    add_doc(tmp_path, "c2", age_min=200, owner="c" * 32)
    assert upload(as_visitor(a.app, "c" * 32)).status_code == 202  # older than retention: not counted


def test_storage_cap(make_client):
    size = len(png())
    c = make_client({"DOCEX_LIMITS__STORAGE_MAX_MB": str(2.5 * size / 1048576)})
    assert [upload(c).status_code for _ in range(3)] == [202, 202, 503]
    assert upload(c).json()["detail"] == "storage_full"


LLM_ENV = {"DOCEX_ENABLE_LLM": "true", "GEMINI_API_KEY": "test", "DOCEX_LIMITS__LLM_DAILY_DOCS": "1"}


def test_llm_daily_cap_blocks_b_c_but_not_a(make_client, tmp_path):
    c = make_client(LLM_ENV)
    h = c.get("/health").json()
    assert h["available_approaches"] == ["A", "B", "C"] and h["llm_daily"] == {"limit": 1, "used": 0}
    add_doc(tmp_path, "c-today", age_min=0, approach="C")
    h = c.get("/health").json()
    assert h["available_approaches"] == ["A"] and set(h["paused_approaches"]) == {"B", "C"}
    assert h["llm_daily"] == {"limit": 1, "used": 1}
    assert all(0 < v <= 86400 for v in h["paused_approaches"].values())
    r = upload(c, approach="C")
    assert r.status_code == 429 and r.json()["detail"] == "llm_daily_cap"
    assert upload(c, approach="B").json()["detail"] == "llm_daily_cap"
    assert upload(c).status_code == 202  # approach A is never capped by the daily limit


def test_llm_daily_cap_ignores_yesterday(make_client, tmp_path):
    from datetime import datetime, timezone

    c = make_client(LLM_ENV)
    now = datetime.now(timezone.utc)
    add_doc(tmp_path, "c-yesterday", age_min=now.hour * 60 + now.minute + 1, approach="C")  # 23:59 UTC yesterday
    h = c.get("/health").json()
    assert h["available_approaches"] == ["A", "B", "C"] and h["llm_daily"]["used"] == 0
