"""V3.1 — Opt-in local read-only Conversation Collector for Antigravity, Claude Code, and Codex CLI.

Privacy & Security:
- Collects ONLY visible user & assistant text messages.
- Explicitly excludes private reasoning (`thinking`), hidden/system prompts, raw tool payloads, and secrets.
- Applies deterministic secret redaction before returning or syncing any text.
- Incremental byte-offset cursor with rotation handling and SHA-256 content_hash deduplication.
- Zero LLM calls during sync.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MAX_VISIBLE_CHARS = 16000

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9\-._~+/]+=*"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9\-_]{12,}\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"(?i)(password|secret|api_key|token)\s*[:=]\s*['\"]?[^\s'\",;]{6,}"), r"\1=[REDACTED]"),
)


def redact_visible_text(text: str, max_chars: int = MAX_VISIBLE_CHARS) -> str:
    cleaned = text.strip()
    for pat, repl in _SECRET_PATTERNS:
        cleaned = pat.sub(repl, cleaned)
    if len(cleaned) > max_chars:
        return cleaned[: max_chars - 16] + "\n[TRUNCATED]"
    return cleaned


def _iso_or_now(raw: Any) -> str:
    if isinstance(raw, str) and raw.strip():
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).isoformat(timespec="microseconds")
        except ValueError:
            pass
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


@dataclass
class CollectedMessage:
    seq_no: int
    role: str  # "user" | "assistant"
    visible_text: str
    content_hash: str
    created_at: str
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class CollectedConversation:
    client_type: str
    external_conversation_id: str
    provider: str | None
    model_id: str | None
    title: str
    messages: list[CollectedMessage] = field(default_factory=list)
    skipped_records: int = 0
    error_records: int = 0


def _read_jsonl_incremental(path: Path, cursors: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    key = str(path.resolve())
    stat = path.stat()
    offset = int(cursors.get(key, {}).get("offset", 0))
    if offset > stat.st_size:
        offset = 0
    out: list[dict[str, Any]] = []
    errs = 0
    new_offset = offset
    with path.open("rb") as fh:
        fh.seek(offset)
        while True:
            pos = fh.tell()
            raw = fh.readline()
            if not raw:
                break
            if not raw.endswith(b"\n"):
                fh.seek(pos)
                break
            new_offset = fh.tell()
            txt = raw.decode("utf-8", errors="replace").strip()
            if not txt.startswith("{"):
                continue
            try:
                obj = json.loads(txt)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                errs += 1
    cursors[key] = {"offset": new_offset, "size": stat.st_size, "mtime": stat.st_mtime}
    return out, errs


def _make_msg(seq_no: int, role: str, raw_text: str, created_at: str, model_id: str | None = None, in_tok: int | None = None, out_tok: int | None = None) -> CollectedMessage | None:
    text = redact_visible_text(raw_text)
    if not text:
        return None
    h = hashlib.sha256(f"{seq_no}:{role}:{text}".encode("utf-8")).hexdigest()
    return CollectedMessage(
        seq_no=seq_no,
        role=role,
        visible_text=text,
        content_hash=h,
        created_at=_iso_or_now(created_at),
        model_id=model_id,
        input_tokens=in_tok if isinstance(in_tok, int) and in_tok >= 0 else None,
        output_tokens=out_tok if isinstance(out_tok, int) and out_tok >= 0 else None,
    )


def collect_antigravity_conversation(path: Path, conversation_id: str, cursors: dict[str, dict[str, Any]]) -> CollectedConversation:
    conv = CollectedConversation(client_type="antigravity", external_conversation_id=conversation_id, provider="agy", model_id=None, title=f"Antigravity {conversation_id[:8]}")
    if not path.is_file():
        return conv
    records, errs = _read_jsonl_incremental(path, cursors)
    conv.error_records += errs
    for idx, obj in enumerate(records, start=1):
        typ = obj.get("type")
        if typ == "USER_INPUT":
            m = _make_msg(int(obj.get("step_index") or idx), "user", str(obj.get("content") or ""), obj.get("created_at"))
            if m:
                if conv.title.startswith("Antigravity "):
                    conv.title = m.visible_text.splitlines()[0][:100]
                conv.messages.append(m)
            else:
                conv.skipped_records += 1
        elif typ == "PLANNER_RESPONSE":
            # Explicitly ignore `thinking` and `tool_calls`; capture only visible `content`.
            model = str(obj["model"]) if obj.get("model") else None
            if model:
                conv.model_id = model
            m = _make_msg(
                int(obj.get("step_index") or idx),
                "assistant",
                str(obj.get("content") or ""),
                obj.get("created_at"),
                model_id=model,
                in_tok=obj.get("input_tokens"),
                out_tok=obj.get("output_tokens"),
            )
            if m:
                conv.messages.append(m)
            else:
                conv.skipped_records += 1
        else:
            conv.skipped_records += 1
    return conv


def collect_claude_conversation(path: Path, cursors: dict[str, dict[str, Any]]) -> CollectedConversation:
    conv = CollectedConversation(client_type="claude", external_conversation_id=path.stem, provider="claude", model_id=None, title=f"Claude {path.stem[:8]}")
    if not path.is_file():
        return conv
    records, errs = _read_jsonl_incremental(path, cursors)
    conv.error_records += errs
    for idx, obj in enumerate(records, start=1):
        if obj.get("sessionId"):
            conv.external_conversation_id = str(obj["sessionId"])
        typ = obj.get("type")
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        if typ not in ("user", "assistant") or not msg:
            conv.skipped_records += 1
            continue
        content = msg.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") == "text" and isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
        text = "\n".join(parts).strip()
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else {}
        model = str(msg["model"]) if msg.get("model") else None
        if model:
            conv.model_id = model
        m = _make_msg(idx, typ, text, obj.get("timestamp"), model_id=model, in_tok=usage.get("input_tokens"), out_tok=usage.get("output_tokens"))
        if m:
            if typ == "user" and conv.title.startswith("Claude "):
                conv.title = m.visible_text.splitlines()[0][:100]
            conv.messages.append(m)
        else:
            conv.skipped_records += 1
    return conv


def collect_codex_conversation(path: Path, cursors: dict[str, dict[str, Any]]) -> CollectedConversation:
    conv = CollectedConversation(client_type="codex", external_conversation_id=path.stem, provider="codex", model_id=None, title=f"Codex {path.stem[:8]}")
    if not path.is_file():
        return conv
    records, errs = _read_jsonl_incremental(path, cursors)
    conv.error_records += errs
    for idx, obj in enumerate(records, start=1):
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if obj.get("type") == "session_meta" and payload.get("id"):
            conv.external_conversation_id = str(payload["id"])
        if payload.get("model"):
            conv.model_id = str(payload["model"])
        role = payload.get("role")
        if role not in ("user", "assistant"):
            conv.skipped_records += 1
            continue
        content = payload.get("content")
        parts: list[str] = []
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and blk.get("type") in ("input_text", "output_text", "text") and isinstance(blk.get("text"), str):
                    parts.append(blk["text"])
        m = _make_msg(int(obj.get("ordinal") or idx), role, "\n".join(parts), obj.get("timestamp"), model_id=conv.model_id)
        if m:
            if role == "user" and conv.title.startswith("Codex "):
                conv.title = m.visible_text.splitlines()[0][:100]
            conv.messages.append(m)
        else:
            conv.skipped_records += 1
    return conv
