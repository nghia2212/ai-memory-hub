"""Redaction and payload-bounding utilities for Hermes worker."""
from __future__ import annotations

import re
from typing import Any

# Fields that must NEVER be forwarded to an external LLM provider.
FORBIDDEN_FIELDS = frozenset(
    {
        "transcript",
        "transcript_path",
        "raw_code",
        "source_code",
        "source_files",
        "file_contents",
        "diff",
        "diff_full",
        "env",
        "secrets",
    }
)

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z0-9 ]*PRIVATE KEY-----"
        ),
        "[REDACTED_PRIVATE_KEY]",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED_TOKEN]"),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{12,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[REDACTED_GH_PAT]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{20,}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z\-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*\b"), "[REDACTED_JWT]"),
    (
        re.compile(r"(?i)\b([A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD|PASSWD)\s*[=:]\s*)([^\s,;\"']+)"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)\b((?:postgres|postgresql|mysql|mongodb|redis)://)[^:\s]+:[^@\s]+@"),
        r"\1[REDACTED_CREDENTIALS]@",
    ),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"(?i)[A-Z]:\\Users\\[^\\/\s\"']+"), "<USER_HOME>"),
    (re.compile(r"/home/[^/\s\"']+"), "<USER_HOME>"),
)


def redact_text(text: str, max_chars: int = 2000) -> tuple[str, bool]:
    """Redact secrets, PII, and local user paths; bound length.

    Returns (redacted_text, was_truncated).
    """
    cleaned = text
    for pattern, replacement in _SECRET_PATTERNS:
        cleaned = pattern.sub(replacement, cleaned)

    truncated = False
    if len(cleaned) > max_chars:
        cleaned = cleaned[: max_chars - 3] + "..."
        truncated = True
    return cleaned, truncated


def sanitize_event_payload(payload: Any, max_chars: int = 2000) -> dict[str, Any]:
    """Strip forbidden raw-code/transcript fields and redact remaining values."""
    if not isinstance(payload, dict):
        text, _ = redact_text(str(payload), max_chars=max_chars)
        return {"summary": text}

    sanitized: dict[str, Any] = {}
    remaining = max_chars
    for key, value in payload.items():
        key_str = str(key)
        if key_str.lower() in FORBIDDEN_FIELDS:
            continue
        if remaining <= 0:
            break
        if isinstance(value, str):
            redacted, _ = redact_text(value, max_chars=min(remaining, 500))
            sanitized[key_str] = redacted
            remaining -= len(redacted)
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key_str] = value
        elif isinstance(value, list):
            items: list[Any] = []
            for item in value[:10]:
                item_str, _ = redact_text(str(item), max_chars=min(remaining, 120))
                items.append(item_str)
                remaining -= len(item_str)
                if remaining <= 0:
                    break
            sanitized[key_str] = items
        elif isinstance(value, dict):
            nested = sanitize_event_payload(value, max_chars=min(remaining, 500))
            sanitized[key_str] = nested
            remaining -= len(str(nested))
    return sanitized
