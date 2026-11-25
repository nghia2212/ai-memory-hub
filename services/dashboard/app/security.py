"""Session auth, CSRF tokens, and Origin checks for Dashboard."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from urllib.parse import urlparse

from fastapi import HTTPException, Request

from .config import DashboardSettings

SESSION_COOKIE_NAME = "ai_hub_session"
SESSION_TTL_SECONDS = 8 * 3600


def create_session_token(secret: str) -> tuple[str, str]:
    sid = secrets.token_urlsafe(24)
    exp = str(int(time.time()) + SESSION_TTL_SECONDS)
    payload = f"{sid}.{exp}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}", compute_csrf_token(secret, sid)


def compute_csrf_token(secret: str, sid: str) -> str:
    return hmac.new(secret.encode("utf-8"), f"{sid}:csrf".encode("utf-8"), hashlib.sha256).hexdigest()


def verify_session_token(token: str | None, secret: str) -> str | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    sid, exp_str, sig = parts
    try:
        exp = int(exp_str)
    except ValueError:
        return None
    if exp < int(time.time()):
        return None
    expected = hmac.new(secret.encode("utf-8"), f"{sid}.{exp_str}".encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    return sid


def verify_origin(request: Request, settings: DashboardSettings) -> None:
    origin = request.headers.get("Origin")
    referer = request.headers.get("Referer")
    candidate = origin
    if not candidate and referer:
        parsed = urlparse(referer)
        if parsed.scheme and parsed.netloc:
            candidate = f"{parsed.scheme}://{parsed.netloc}"

    if not candidate:
        raise HTTPException(status_code=403, detail={"error": {"code": "origin_required", "message": "Missing Origin header"}})

    host_header = request.headers.get("Host")
    allowed = set(settings.allowed_origins)
    if host_header:
        allowed.add(f"http://{host_header}")
        allowed.add(f"https://{host_header}")

    if candidate not in allowed:
        raise HTTPException(status_code=403, detail={"error": {"code": "origin_forbidden", "message": "Origin not allowed"}})


def require_authenticated_csrf(request: Request, settings: DashboardSettings) -> str:
    verify_origin(request, settings)
    cookie_val = request.cookies.get(SESSION_COOKIE_NAME)
    sid = verify_session_token(cookie_val, settings.session_secret)
    if not sid:
        raise HTTPException(status_code=401, detail={"error": {"code": "unauthenticated", "message": "Session required"}})
    csrf_header = request.headers.get("X-CSRF-Token", "")
    expected_csrf = compute_csrf_token(settings.session_secret, sid)
    if not csrf_header or not hmac.compare_digest(csrf_header, expected_csrf):
        raise HTTPException(status_code=403, detail={"error": {"code": "csrf_invalid", "message": "Invalid CSRF token"}})
    return sid
