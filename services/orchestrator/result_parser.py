"""Zero-dependency (stdlib-only) Agent Result Parser & Delivery Contract (MCP321 P16A).

Runs safely on BOTH Windows host Python (without pydantic/third-party deps) and Docker containers.
Guarantees:
- Never swallows valid plain-text, Markdown, or JSON answers when optional metadata is malformed.
- Strips secrets and private reasoning blocks before storage/display.
- Distinguishes execution exit status from result delivery status (delivered / missing / failed).
- Never reports 'No structured result available.' as a valid delivered answer.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

MAX_ANSWER_CHARS = 16000

_REDACT_PATTERNS = (
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"\bsk-[A-Za-z0-9\-_]{12,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{24,}\b"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"),
    re.compile(r"(?i)[A-Z]:\\Users\\[^\\/\s\"']+"),
    re.compile(r"/home/[^/\s\"']+"),
)

_THINK_TAG_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def redact_text(text: str, max_chars: int = MAX_ANSWER_CHARS) -> tuple[str, bool]:
    cleaned = _ANSI_RE.sub("", text or "")
    cleaned = _THINK_TAG_RE.sub("", cleaned).strip()
    for pat in _REDACT_PATTERNS:
        cleaned = pat.sub("[REDACTED]", cleaned)
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 32] + "\n...[truncated due to size limit]", True
    return cleaned, False


def _opt_int(val: Any) -> int | None:
    return int(val) if isinstance(val, int) and not isinstance(val, bool) and val >= 0 else None


def _opt_float(val: Any) -> float | None:
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) and val >= 0 else None


def _extract_text_from_json_obj(data: dict[str, Any]) -> tuple[str, str]:
    """Extract final user-facing answer from common CLI/API JSON envelopes.
    Returns (answer_text, content_type). Never exposes private reasoning fields.
    """
    for key in ("result", "response", "answer", "final_answer", "output_text", "text", "content", "message", "summary"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            ct = "text/markdown" if any(m in val for m in ("```", "\n- ", "\n# ", "**")) else "text/plain"
            return val.strip(), ct
        if isinstance(val, list):
            parts = []
            for item in val:
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
                elif isinstance(item, dict) and item.get("type") in ("text", "output_text", None):
                    t = item.get("text") or item.get("content")
                    if isinstance(t, str) and t.strip():
                        parts.append(t.strip())
            if parts:
                joined = "\n".join(parts)
                ct = "text/markdown" if any(m in joined for m in ("```", "\n- ", "\n# ", "**")) else "text/plain"
                return joined, ct
        if isinstance(val, dict):
            sub_txt, sub_ct = _extract_text_from_json_obj(val)
            if sub_txt:
                return sub_txt, sub_ct

    # OpenAI / Anthropic choices/candidates envelopes
    if isinstance(data.get("choices"), list) and data["choices"]:
        msg = (data["choices"][0] or {}).get("message") or {}
        if isinstance(msg.get("content"), str) and msg["content"].strip():
            return msg["content"].strip(), "text/markdown"
    if isinstance(data.get("candidates"), list) and data["candidates"]:
        parts = ((data["candidates"][0] or {}).get("content") or {}).get("parts") or []
        txts = [str(p.get("text") or "") for p in parts if isinstance(p, dict) and p.get("text") and not p.get("thought")]
        if txts:
            return "\n".join(txts).strip(), "text/markdown"

    # If it's a legitimate structured JSON payload (not just empty metadata), preserve formatted JSON
    stripped = {
        k: v for k, v in data.items()
        if k not in ("usage", "session_id", "thread_id", "reasoning", "reasoning_content", "thinking", "thoughts", "cost_usd", "total_cost_usd")
        and v not in (None, "", [], {})
    }
    if stripped:
        return json.dumps(stripped, ensure_ascii=False, indent=2), "application/json"
    return "", "text/plain"


def parse_agent_stdout(
    *,
    cli_family: str,
    stdout: str,
    stderr: str = "",
    exit_code: int = 0,
) -> dict[str, Any]:
    """Parse CLI/Agent output into a normalized, safe P16A Result Delivery contract."""
    now = datetime.now(timezone.utc).isoformat()
    raw = (stdout or "").strip()
    usage: dict[str, Any] = {
        "input_tokens": None,
        "output_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "cost_usd": None,
        "measurement_kind": "unavailable",
        "counter_mode": "delta",
        "source": cli_family,
        "observed_at": now,
    }
    session_id: str | None = None
    answer_raw = ""
    content_type = "text/plain"

    if raw:
        # 1. JSONL (Codex or streaming JSON lines)
        if cli_family == "codex" or ("\n" in raw and raw.splitlines()[0].strip().startswith("{")):
            last_usage: dict[str, Any] = {}
            msg_parts: list[str] = []
            parsed_any_json = False
            for line in raw.splitlines():
                s = line.strip()
                if not s:
                    continue
                try:
                    ev = json.loads(s)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                parsed_any_json = True
                if ev.get("session_id") or ev.get("thread_id"):
                    session_id = str(ev.get("session_id") or ev.get("thread_id"))
                if isinstance(ev.get("usage"), dict):
                    last_usage = ev["usage"]
                if ev.get("type") == "item.completed" and isinstance(ev.get("item"), dict) and ev["item"].get("type") == "agent_message":
                    t = str(ev["item"].get("text") or "").strip()
                    if t:
                        msg_parts.append(t)
                elif ev.get("type") in ("turn.completed", "message", "result") and (ev.get("content") or ev.get("result")):
                    t = str(ev.get("content") or ev.get("result") or "").strip()
                    if t:
                        msg_parts.append(t)
            if parsed_any_json:
                if last_usage:
                    has_any = any(k in last_usage for k in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_read_tokens"))
                    usage.update(
                        {
                            "input_tokens": _opt_int(last_usage.get("input_tokens")),
                            "output_tokens": _opt_int(last_usage.get("output_tokens")),
                            "cache_read_tokens": _opt_int(last_usage.get("cache_read_tokens", last_usage.get("cached_input_tokens"))),
                            "cache_write_tokens": _opt_int(last_usage.get("cache_write_tokens")),
                            "reasoning_tokens": _opt_int(last_usage.get("reasoning_tokens")),
                            "cost_usd": _opt_float(last_usage.get("cost_usd")),
                            "measurement_kind": "measured" if has_any else "unavailable",
                            "counter_mode": "cumulative" if cli_family == "codex" else "delta",
                            "source": f"{cli_family}_jsonl",
                        }
                    )
                if msg_parts:
                    answer_raw = "\n\n".join(msg_parts)
                    content_type = "text/markdown"

        # 2. Single JSON object (agy / claude / gemini / API)
        if not answer_raw:
            try:
                data = json.loads(raw)
                if isinstance(data, dict):
                    if data.get("session_id"):
                        session_id = str(data["session_id"])
                    u = data.get("usage") if isinstance(data.get("usage"), dict) else {}
                    has_any = any(k in u for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_read_tokens"))
                    cost_val = data.get("total_cost_usd") if "total_cost_usd" in data else u.get("cost_usd")
                    usage.update(
                        {
                            "input_tokens": _opt_int(u.get("input_tokens")),
                            "output_tokens": _opt_int(u.get("output_tokens")),
                            "cache_read_tokens": _opt_int(u.get("cache_read_tokens", u.get("cache_read_input_tokens"))),
                            "cache_write_tokens": _opt_int(u.get("cache_write_tokens", u.get("cache_creation_input_tokens"))),
                            "reasoning_tokens": _opt_int(u.get("reasoning_tokens")),
                            "cost_usd": _opt_float(cost_val),
                            "measurement_kind": "measured" if has_any else "unavailable",
                            "source": f"{cli_family}_json",
                        }
                    )
                    answer_raw, content_type = _extract_text_from_json_obj(data)
                elif isinstance(data, list) and data:
                    answer_raw = json.dumps(data, ensure_ascii=False, indent=2)
                    content_type = "application/json"
            except json.JSONDecodeError:
                # Preserve legitimate plain text / Markdown output on JSON parse failure!
                answer_raw = raw
                content_type = "text/markdown" if any(m in raw for m in ("```", "\n- ", "\n# ", "**")) else "text/plain"

    final_answer, truncated = redact_text(answer_raw)
    if exit_code != 0:
        delivery_status = "failed"
    elif final_answer:
        delivery_status = "delivered"
    else:
        delivery_status = "missing"

    err_redacted, _ = redact_text(stderr, max_chars=800) if stderr else ("", False)
    return {
        "final_answer": final_answer,
        "content_type": content_type,
        "result_delivery_status": delivery_status,
        "truncated": truncated,
        "session_id": session_id,
        "usage": usage,
        "error_excerpt": err_redacted,
    }
