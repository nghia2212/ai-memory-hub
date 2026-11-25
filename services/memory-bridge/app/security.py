"""Authentication, RBAC scopes, path validation, secret redaction, and FTS5 literal tokenization."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .config import Settings
from .errors import BridgeError

SCOPE_RANK = {
    "read": {"read"},
    "write": {"read", "write"},
    "admin": {"read", "write", "admin"},
    "worker": {"worker"},
}

_FTS_OPERATORS = {"AND", "OR", "NOT", "NEAR"}
_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{6,})['\"]?"),
    re.compile(r"\bsk-[A-Za-z0-9]{16,}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}\b"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.=]{12,}", re.IGNORECASE),
]


@dataclass(frozen=True)
class AuthPrincipal:
    role: str
    scopes: frozenset[str]


def authenticate_token(token: str | None, settings: Settings) -> AuthPrincipal:
    if not token:
        raise BridgeError(401, "unauthenticated", "Missing bearer token")

    for role, configured in (
        ("admin", settings.admin_tokens),
        ("write", settings.write_tokens),
        ("read", settings.read_tokens),
        ("worker", settings.worker_tokens),
    ):
        for candidate in configured:
            if hmac.compare_digest(token, candidate):
                return AuthPrincipal(role=role, scopes=frozenset(SCOPE_RANK[role]))

    raise BridgeError(401, "unauthenticated", "Invalid bearer token")


def extract_bearer_token(auth_header: str | None) -> str | None:
    if not auth_header:
        return None
    parts = auth_header.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def require_scope(principal: AuthPrincipal, required_scope: str) -> None:
    if required_scope not in principal.scopes:
        raise BridgeError(403, "forbidden", f"Scope '{required_scope}' is required")


def validate_safe_worktree_root(raw_path: str) -> str:
    """Validate and normalize a worktree root path without leaking host filesystem layout."""
    cleaned = raw_path.strip()
    if not cleaned or "\x00" in cleaned:
        raise BridgeError(422, "invalid_path", "Worktree root path is invalid")

    if ".." in cleaned.replace("\\", "/").split("/"):
        raise BridgeError(422, "unsafe_path", "Path traversal segments are not allowed")

    is_windows_abs = bool(re.match(r"^[A-Za-z]:[\\/]", cleaned))
    is_posix_abs = cleaned.startswith("/") and not cleaned.startswith("//")
    if not (is_windows_abs or is_posix_abs):
        raise BridgeError(422, "unsafe_path", "Worktree root must be an explicit absolute path")

    if is_windows_abs:
        normalized = str(PureWindowsPath(cleaned))
    else:
        normalized = str(PurePosixPath(cleaned))

    # If the path exists on the current host, verify it is not a symlink escape
    if os.path.exists(normalized):
        real = os.path.realpath(normalized)
        if os.path.normcase(real) != os.path.normcase(os.path.abspath(normalized)):
            raise BridgeError(422, "unsafe_path", "Symlink escape detected in worktree root")

    return normalized


def tokenize_fts_query(query: str) -> str:
    """Convert arbitrary user text (including Vietnamese Unicode) into safe FTS5 literal tokens."""
    # Extract word sequences (Unicode letters, digits, underscore)
    raw_tokens = re.findall(r"[^\s\W]+", query, flags=re.UNICODE)
    safe_terms: list[str] = []
    for tok in raw_tokens:
        if tok.upper() in _FTS_OPERATORS:
            continue
        escaped = tok.replace('"', '""')
        safe_terms.append(f'"{escaped}"')
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_terms: list[str] = []
    for term in safe_terms:
        if term not in seen:
            seen.add(term)
            unique_terms.append(term)
    return " AND ".join(unique_terms)


def compute_payload_hash(payload: Any) -> str:
    serialized = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def redact_secrets(text: str) -> str:
    redacted = text
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_payload(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, list):
        return [redact_payload(item) for item in obj]
    if isinstance(obj, dict):
        return {
            k: ("[REDACTED]" if str(k).lower() in {"token", "secret", "password", "api_key", "authorization"} else redact_payload(v))
            for k, v in obj.items()
        }
    return obj
