"""V2.2.4 opt-in read-only local Usage Collector for Antigravity IDE, Claude Code, and Codex CLI.

Security & privacy:
- Reads ONLY token/model/session/timestamp metadata from local JSONL files.
- Never reads or stores private reasoning (`thinking`), prompts, visible chat text, raw tool payloads, or secrets.
- Uses incremental byte-offset cursors (handles active files and rotation) and deterministic source_event_id deduplication.
- Never fabricates token counts or writes events to unregistered/unmatched projects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _opt_int(val: Any) -> int | None:
    return int(val) if isinstance(val, int) and not isinstance(val, bool) and val >= 0 else None


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
class UsageSample:
    client: str
    provider: str
    upstream_session_id: str
    source_event_id: str
    sequence_no: int
    observed_at: str
    counter_mode: str  # "delta" | "cumulative"
    measurement_kind: str  # "measured" | "unavailable"
    model_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    cost_usd: float | None = None
    source: str = ""


@dataclass
class CollectorStats:
    files_scanned: int = 0
    records_processed: int = 0
    records_skipped: int = 0
    records_error: int = 0
    samples: list[UsageSample] = field(default_factory=list)


def _read_jsonl_incremental(path: Path, cursors: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Read new JSONL lines from a file safely, handling active writes and file rotation/truncation."""
    key = str(path.resolve())
    stat = path.stat()
    prev = cursors.get(key, {})
    offset = int(prev.get("offset", 0))
    if offset > stat.st_size:
        # File was rotated or truncated; reset cursor safely.
        offset = 0

    records: list[dict[str, Any]] = []
    errors = 0
    new_offset = offset
    with path.open("rb") as fh:
        fh.seek(offset)
        while True:
            pos = fh.tell()
            raw_line = fh.readline()
            if not raw_line:
                break
            if not raw_line.endswith(b"\n"):
                # Partial line still being written by active IDE/CLI; stop before it.
                fh.seek(pos)
                break
            new_offset = fh.tell()
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text.startswith("{"):
                continue
            try:
                obj = json.loads(text)
                if isinstance(obj, dict):
                    records.append(obj)
            except json.JSONDecodeError:
                errors += 1

    cursors[key] = {"offset": new_offset, "size": stat.st_size, "mtime": stat.st_mtime}
    return records, errors


def collect_antigravity_transcript(
    transcript_path: Path,
    conversation_id: str,
    cursors: dict[str, dict[str, Any]],
) -> CollectorStats:
    stats = CollectorStats()
    if not transcript_path.is_file():
        return stats
    stats.files_scanned = 1
    records, errs = _read_jsonl_incremental(transcript_path, cursors)
    stats.records_error += errs

    for obj in records:
        if obj.get("type") != "PLANNER_RESPONSE":
            stats.records_skipped += 1
            continue
        in_tok = _opt_int(obj.get("input_tokens"))
        out_tok = _opt_int(obj.get("output_tokens"))
        cache_r = _opt_int(obj.get("cache_read_tokens"))
        if in_tok is None and out_tok is None and cache_r is None:
            stats.records_skipped += 1
            continue
        step_idx = _opt_int(obj.get("step_index")) or 0
        obs = _iso_or_now(obj.get("created_at"))
        ev_hash = hashlib.sha256(f"agy:{conversation_id}:{step_idx}:{obs}:{in_tok}:{out_tok}".encode()).hexdigest()[:24]
        stats.samples.append(
            UsageSample(
                client="antigravity",
                provider="agy",
                upstream_session_id=conversation_id,
                source_event_id=f"agy:{conversation_id[:16]}:{step_idx}:{ev_hash[:12]}",
                sequence_no=step_idx,
                observed_at=obs,
                counter_mode="delta",
                measurement_kind="measured",
                model_id=str(obj["model"]) if obj.get("model") else None,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_r,
                source="antigravity_transcript_collector",
            )
        )
        stats.records_processed += 1
    return stats


def collect_claude_jsonl(path: Path, cursors: dict[str, dict[str, Any]]) -> CollectorStats:
    stats = CollectorStats()
    if not path.is_file():
        return stats
    stats.files_scanned = 1
    records, errs = _read_jsonl_incremental(path, cursors)
    stats.records_error += errs

    for idx, obj in enumerate(records, start=1):
        msg = obj.get("message") if isinstance(obj.get("message"), dict) else {}
        usage = msg.get("usage") if isinstance(msg.get("usage"), dict) else None
        if not usage:
            stats.records_skipped += 1
            continue
        in_tok = _opt_int(usage.get("input_tokens"))
        out_tok = _opt_int(usage.get("output_tokens"))
        cache_r = _opt_int(usage.get("cache_read_input_tokens"))
        cache_w = _opt_int(usage.get("cache_creation_input_tokens"))
        out_det = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
        reason_tok = _opt_int(out_det.get("thinking_tokens"))
        if all(v is None for v in (in_tok, out_tok, cache_r, cache_w, reason_tok)):
            stats.records_skipped += 1
            continue
        sid = str(obj.get("sessionId") or path.stem)
        obs = _iso_or_now(obj.get("timestamp"))
        msg_id = str(msg.get("id") or obj.get("uuid") or idx)
        ev_hash = hashlib.sha256(f"claude:{sid}:{msg_id}:{obs}".encode()).hexdigest()[:24]
        stats.samples.append(
            UsageSample(
                client="claude",
                provider="claude",
                upstream_session_id=sid,
                source_event_id=f"claude:{ev_hash}",
                sequence_no=idx,
                observed_at=obs,
                counter_mode="delta",
                measurement_kind="measured",
                model_id=str(msg["model"]) if msg.get("model") else None,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_r,
                cache_write_tokens=cache_w,
                reasoning_tokens=reason_tok,
                source="claude_jsonl_collector",
            )
        )
        stats.records_processed += 1
    return stats


def collect_codex_jsonl(path: Path, cursors: dict[str, dict[str, Any]]) -> CollectorStats:
    stats = CollectorStats()
    if not path.is_file():
        return stats
    stats.files_scanned = 1
    records, errs = _read_jsonl_incremental(path, cursors)
    stats.records_error += errs

    session_id = path.stem
    model_id: str | None = None
    for idx, obj in enumerate(records, start=1):
        payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else {}
        if payload.get("id") and obj.get("type") == "session_meta":
            session_id = str(payload["id"])
        if payload.get("model"):
            model_id = str(payload["model"])
        info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
        total_u = info.get("total_token_usage") if isinstance(info.get("total_token_usage"), dict) else None
        if not total_u:
            stats.records_skipped += 1
            continue
        in_tok = _opt_int(total_u.get("input_tokens"))
        out_tok = _opt_int(total_u.get("output_tokens"))
        cache_r = _opt_int(total_u.get("cached_input_tokens"))
        cache_w = _opt_int(total_u.get("cache_write_input_tokens"))
        reason_tok = _opt_int(total_u.get("reasoning_output_tokens"))
        if all(v is None for v in (in_tok, out_tok, cache_r, cache_w, reason_tok)):
            stats.records_skipped += 1
            continue
        obs = _iso_or_now(obj.get("timestamp"))
        ordinal = _opt_int(obj.get("ordinal")) or idx
        ev_hash = hashlib.sha256(f"codex:{session_id}:{ordinal}:{obs}".encode()).hexdigest()[:24]
        stats.samples.append(
            UsageSample(
                client="codex",
                provider="codex",
                upstream_session_id=session_id,
                source_event_id=f"codex:{ev_hash}",
                sequence_no=ordinal,
                observed_at=obs,
                counter_mode="cumulative",
                measurement_kind="measured",
                model_id=model_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cache_read_tokens=cache_r,
                cache_write_tokens=cache_w,
                reasoning_tokens=reason_tok,
                source="codex_jsonl_collector",
            )
        )
        stats.records_processed += 1
    return stats


def sync_samples_to_bridge(
    *,
    bridge_url: str,
    write_token: str,
    project_id: str,
    worktree_id: str,
    session_id: str,
    samples: list[UsageSample],
) -> dict[str, int]:
    """Post collected usage samples to Memory Bridge /v2/usage with idempotent source_event_id."""
    posted = 0
    deduped = 0
    failed = 0
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Bearer {write_token}",
    }
    for s in samples:
        body = {
            "session_id": session_id,
            "project_id": project_id,
            "worktree_id": worktree_id,
            "source_event_id": s.source_event_id,
            "sequence_no": s.sequence_no,
            "usage": {
                "input_tokens": s.input_tokens,
                "output_tokens": s.output_tokens,
                "cache_read_tokens": s.cache_read_tokens,
                "cache_write_tokens": s.cache_write_tokens,
                "reasoning_tokens": s.reasoning_tokens,
                "cost_usd": s.cost_usd,
                "measurement_kind": s.measurement_kind,
                "counter_mode": s.counter_mode,
                "source": s.source,
                "observed_at": s.observed_at,
            },
        }
        req = urllib.request.Request(
            f"{bridge_url.rstrip('/')}/v2/usage",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw) if raw else {}
                if data.get("deduplicated"):
                    deduped += 1
                else:
                    posted += 1
        except urllib.error.HTTPError:
            failed += 1
    return {"posted": posted, "deduplicated": deduped, "failed": failed}


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in read-only local token usage collector for AI Memory Hub.")
    parser.add_argument("--antigravity-conversation", help="Antigravity conversation UUID to inspect/sync.")
    parser.add_argument("--session-id", help="Registered AI Memory Hub session UUID to attach usage to.")
    parser.add_argument("--project-id", help="Registered project UUID.")
    parser.add_argument("--worktree-id", help="Registered worktree UUID.")
    parser.add_argument("--sync", action="store_true", help="Post matched samples to Memory Bridge /v2/usage.")
    parser.add_argument("--bridge-url", default=os.environ.get("AI_MEMORY_BRIDGE_URL", "http://127.0.0.1:8082"))
    args = parser.parse_args()

    cursors: dict[str, dict[str, Any]] = {}
    if args.antigravity_conversation:
        p = Path.home() / ".gemini" / "antigravity" / "brain" / args.antigravity_conversation / ".system_generated" / "logs" / "transcript.jsonl"
        st = collect_antigravity_transcript(p, args.antigravity_conversation, cursors)
        out: dict[str, Any] = {
            "client": "antigravity",
            "files_scanned": st.files_scanned,
            "records_processed": st.records_processed,
            "records_skipped": st.records_skipped,
            "records_error": st.records_error,
            "sum_input_tokens": sum(s.input_tokens or 0 for s in st.samples),
            "sum_output_tokens": sum(s.output_tokens or 0 for s in st.samples),
            "sum_cache_read_tokens": sum(s.cache_read_tokens or 0 for s in st.samples),
        }
        if args.sync:
            tok = os.environ.get("BRIDGE_WRITE_TOKEN") or os.environ.get("BRIDGE_RUNNER_TOKEN") or ""
            if not (args.session_id and args.project_id and args.worktree_id and tok):
                print(json.dumps({"error": "Missing --session-id, --project-id, --worktree-id, or BRIDGE_WRITE_TOKEN"}))
                return 2
            out["sync"] = sync_samples_to_bridge(
                bridge_url=args.bridge_url,
                write_token=tok,
                project_id=args.project_id,
                worktree_id=args.worktree_id,
                session_id=args.session_id,
                samples=st.samples,
            )
        print(json.dumps(out, ensure_ascii=False))
        return 0
    print(json.dumps({"status": "ready", "supported_clients": ["antigravity", "claude", "codex"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
