"""Bearer token authentication and scope enforcement."""
from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import HTTPException, Request

from .config import Settings


@dataclass(frozen=True)
class AuthContext:
    scope: str  # "read" | "admin"


def _token_matches(token: str, candidates: set[str]) -> bool:
    for candidate in candidates:
        if hmac.compare_digest(token.encode("utf-8"), candidate.encode("utf-8")):
            return True
    return False


def verify_bearer(request: Request, settings: Settings, required_scope: str) -> AuthContext:
    """Validate Authorization Bearer token and required scope ('read' or 'admin')."""
    if settings.allow_unauth_dev and not settings.read_tokens and not settings.admin_tokens:
        return AuthContext(scope="admin")

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Missing bearer token"})

    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Empty bearer token"})

    if _token_matches(token, settings.admin_tokens):
        return AuthContext(scope="admin")

    if _token_matches(token, settings.read_tokens):
        if required_scope == "admin":
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "Admin scope required"})
        return AuthContext(scope="read")

    raise HTTPException(status_code=401, detail={"code": "unauthorized", "message": "Invalid bearer token"})
