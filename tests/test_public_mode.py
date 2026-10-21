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
