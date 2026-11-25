"""Structured, injection-safe CLI command builder and telemetry parser."""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.orchestrator.contracts import QuotaSnapshot, TokenUsageCounters

ALLOWED_CLI_FAMILIES = frozenset({"agy", "claude", "codex", "gemini"})
ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
_SAFE_SELECTOR_RE = re.compile(r"^[A-Za-z0-9._:/-]{1,128}$")

_REDACT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{12,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
)


class UnsafeCLIRequestError(ValueError):
    """Raised when CLI family, selector, or worktree path violates safety rules."""


def redact_summary(text: str, max_chars: int = 600) -> str:
    cleaned = text.strip()
    for pat in _REDACT_PATTERNS:
        cleaned = pat.sub("[REDACTED]", cleaned)
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 3] + "..."
    return cleaned


def validate_worktree_path(worktree_path: str) -> str:
    if not worktree_path or "\x00" in worktree_path:
        raise UnsafeCLIRequestError("Invalid empty or null-byte worktree path.")
    raw_parts = Path(worktree_path).parts
    if ".." in raw_parts:
        raise UnsafeCLIRequestError("Path traversal '..' is forbidden in worktree_path.")
    resolved = Path(worktree_path).resolve()
    if not resolved.is_dir():
        raise UnsafeCLIRequestError(f"Worktree directory does not exist: {resolved}")
    return str(resolved)


def build_cli_command(
    *,
    cli_family: str,
    model_id: str,
    worktree_path: str,
    prompt: str,
    effort: str | None = None,
    executable: str | None = None,
    max_budget_usd: float | None = None,
) -> tuple[list[str], str]:
    """Build structured argument vector (no shell=True, no permission bypass flags)."""
    if cli_family not in ALLOWED_CLI_FAMILIES:
        raise UnsafeCLIRequestError(f"Unsupported CLI family: {cli_family}")
    if not _SAFE_SELECTOR_RE.match(model_id):
        raise UnsafeCLIRequestError(f"Unsafe model selector: {model_id}")
    if effort is not None and effort not in ALLOWED_EFFORTS:
        raise UnsafeCLIRequestError(f"Unsupported effort level: {effort}")

    cwd = validate_worktree_path(worktree_path)
    exe = executable or shutil.which(cli_family)
    if not exe:
        raise UnsafeCLIRequestError(f"CLI executable '{cli_family}' not found on PATH.")

    bounded_prompt = prompt.strip()[:4000]
    if not bounded_prompt:
        raise UnsafeCLIRequestError("Prompt must be non-empty.")

    if cli_family == "agy":
        cmd = [exe, "--print", "--output-format", "json", "--model", model_id]
        if effort:
            cmd.extend(["--effort", effort])
        cmd.append(bounded_prompt)
        return cmd, cwd

    if cli_family == "claude":
        cmd = [exe, "-p", "--output-format", "json", "--model", model_id, "--permission-mode", "acceptEdits"]
        if effort:
            cmd.extend(["--effort", effort])
        if max_budget_usd is not None:
            cmd.extend(["--max-budget-usd", f"{max_budget_usd:.4f}"])
        cmd.append(bounded_prompt)
        return cmd, cwd

    if cli_family == "codex":
        cmd = [exe, "exec", "--json", "-m", model_id, "-s", "workspace-write", "-C", cwd, bounded_prompt]
        return cmd, cwd

    cmd = [exe, "-p", bounded_prompt, "--output-format", "json", "-m", model_id]
    return cmd, cwd


def _opt_int(val: Any) -> int | None:
    return int(val) if isinstance(val, int) and not isinstance(val, bool) and val >= 0 else None


def _opt_float(val: Any) -> float | None:
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= 0 else None


def parse_cli_telemetry(
    *,
    cli_family: str,
    stdout: str,
) -> tuple[TokenUsageCounters, str | None, str]:
    """Extract measured counters (None if missing, never 0) and bounded redacted summary."""
    now = datetime.now(timezone.utc).isoformat()
    raw = stdout.strip()
    if not raw:
        return TokenUsageCounters(measurement_kind="unavailable", source=cli_family, observed_at=now), None, ""

    if cli_family == "codex":
        last_usage: dict[str, Any] = {}
        session_id: str | None = None
        last_msg = ""
        for line in raw.splitlines():
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("session_id") or ev.get("thread_id"):
                session_id = str(ev.get("session_id") or ev.get("thread_id"))
            if isinstance(ev.get("usage"), dict):
                last_usage = ev["usage"]
            if ev.get("type") in {"turn.completed", "message"} and ev.get("content"):
                last_msg = str(ev["content"])
        has_any = any(
            k in last_usage for k in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_read_tokens")
        )
        counters = TokenUsageCounters(
            input_tokens=_opt_int(last_usage.get("input_tokens")),
            output_tokens=_opt_int(last_usage.get("output_tokens")),
            cache_read_tokens=_opt_int(
                last_usage.get("cache_read_tokens") or last_usage.get("cached_input_tokens")
            ),
            cache_write_tokens=_opt_int(last_usage.get("cache_write_tokens")),
            reasoning_tokens=_opt_int(last_usage.get("reasoning_tokens")),
            cost_usd=_opt_float(last_usage.get("cost_usd")),
            measurement_kind="measured" if has_any else "unavailable",
            counter_mode="cumulative",
            source="codex_exec_json",
            observed_at=now,
        )
        return counters, session_id, redact_summary(last_msg or raw)

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return (
            TokenUsageCounters(measurement_kind="unavailable", source=cli_family, observed_at=now),
            None,
            redact_summary(raw),
        )
    if not isinstance(data, dict):
        return (
            TokenUsageCounters(measurement_kind="unavailable", source=cli_family, observed_at=now),
            None,
            redact_summary(raw),
        )

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    session_id = str(data["session_id"]) if data.get("session_id") else None
    has_any = any(k in usage for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_read_tokens"))
    cost_val = data.get("total_cost_usd") if "total_cost_usd" in data else usage.get("cost_usd")
    counters = TokenUsageCounters(
        input_tokens=_opt_int(usage.get("input_tokens")),
        output_tokens=_opt_int(usage.get("output_tokens")),
        cache_read_tokens=_opt_int(usage.get("cache_read_tokens") or usage.get("cache_read_input_tokens")),
        cache_write_tokens=_opt_int(usage.get("cache_write_tokens") or usage.get("cache_creation_input_tokens")),
        reasoning_tokens=_opt_int(usage.get("reasoning_tokens")),
        cost_usd=_opt_float(cost_val),
        measurement_kind="measured" if has_any else "unavailable",
        counter_mode="delta",
        source=f"{cli_family}_json",
        observed_at=now,
    )
    summary = str(data.get("result") or data.get("output") or data.get("message") or raw)
    return counters, session_id, redact_summary(summary)


def discover_supported_quotas(cli_family: str) -> list[QuotaSnapshot]:
    """Only query documented quota interfaces; never fabricate 0% or infer from tokens."""
    _ = cli_family
    return []
