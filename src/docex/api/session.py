"""Anonymous visitor sessions and the client address, for the public demo.

A visitor is a random cookie, not an account: it only scopes which short-lived demo uploads they can see.
Behind a front proxy (e.g. Vercel) every request arrives from the proxy's address, so DOCEX_TRUST_PROXY=true
makes the client address and https detection follow X-Forwarded-For / X-Forwarded-Proto.
"""
from __future__ import annotations

import ipaddress
import os
import re
import uuid
from typing import Optional

from fastapi import Request

from docex.config import pipeline_config

SID_COOKIE = "docex_sid"
_SID = re.compile(r"[0-9a-f]{32}")


def valid_sid(value: Optional[str]) -> bool:
    return bool(value and _SID.fullmatch(value))


def trust_proxy() -> bool:
    return os.environ.get("DOCEX_TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}


def client_ip(request: Request) -> str:
    if trust_proxy():
        first = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        try:
            return str(ipaddress.ip_address(first))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    if trust_proxy():
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if proto:
            return proto == "https"
    return request.url.scheme == "https"


def current_sid(request: Request) -> str:
    return request.state.sid


async def session_middleware(request: Request, call_next):
    sid = request.cookies.get(SID_COOKIE)
    fresh = not valid_sid(sid)
    if fresh:
        sid = uuid.uuid4().hex
    request.state.sid = sid
    response = await call_next(request)
    if fresh or request.method == "POST":  # a POST means the visitor is active: slide the expiry
        max_age = int(pipeline_config()["limits"]["retention_minutes"]) * 60
        response.set_cookie(SID_COOKIE, sid, max_age=max_age, httponly=True, samesite="lax", path="/",
                            secure=is_https(request))
    return response
