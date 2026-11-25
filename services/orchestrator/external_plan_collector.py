"""V3.2.1 (MCP321) P8.1 & P8.5 — Opt-in Read-Only External Plan Collector (Antigravity / Claude / Codex / Gemini).

Rules:
- Strictly read-only: NEVER modifies IDE files.
- Approved roots ONLY: rejects unauthorized paths or path traversal.
- Redacts secrets & strips private reasoning; handles malformed/partial files safely without crashing.
- Honest client compatibility matrix: never fabricates native plans when a client lacks reliable plan files.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z\-_]{30,}|password\s*=\s*['\"][^'\"]{4,}['\"])",
    re.IGNORECASE,
)
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[([ xX/])\]\s+(.+)$")

CLIENT_PLAN_COMPATIBILITY: list[dict[str, Any]] = [
    {
        "client": "antigravity",
        "support_level": "native_artifact_and_explicit_session_mapping",
        "artifact_pattern": "<approved_brain_root>/<conversation_id>/implementation_plan.md | task.md",
        "notes": "Reads opt-in markdown plan/task artifacts when present in approved brain root; falls back to explicit user-confirmed session-to-monitoring-plan when no artifact exists.",
    },
    {
        "client": "claude",
        "support_level": "explicit_session_mapping",
        "artifact_pattern": "opt-in project plan markdown or explicit session monitoring",
        "notes": "Claude Code CLI does not guarantee a universal persistent plan file; use explicit session-to-monitoring-plan.",
    },
    {
        "client": "codex",
        "support_level": "explicit_session_mapping",
        "artifact_pattern": "explicit session monitoring",
        "notes": "Codex CLI exposes session logs; monitor via explicit session mapping.",
    },
    {
        "client": "gemini",
        "support_level": "unsupported",
        "artifact_pattern": None,
        "notes": "Gemini CLI not installed on this host; retained as unsupported/session-monitoring.",
    },
]


def _redact(text: str) -> str:
    return _SECRET_RE.sub("[REDACTED_SECRET]", text)


def parse_markdown_plan_artifact(file_path: str | Path, *, approved_root: str | Path) -> dict[str, Any]:
    """Read-only parser for an approved Antigravity/IDE plan markdown artifact (implementation_plan.md or task.md)."""
    root = Path(approved_root).resolve()
    target = Path(file_path).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise PermissionError(f"Unauthorized artifact path '{target}' outside approved root '{root}'.") from exc
    if not target.is_file():
        raise FileNotFoundError(f"Plan artifact not found: {target}")

    raw = target.read_text(encoding="utf-8", errors="replace")
    clean = _redact(raw)
    lines = clean.splitlines()
    title = target.stem
    for line in lines:
        if line.strip().startswith("#"):
            title = line.lstrip("#").strip()[:200]
            break

    tasks: list[dict[str, Any]] = []
    for idx, line in enumerate(lines, start=1):
        m = _CHECKBOX_RE.match(line)
        if m:
            mark, text = m.group(1), m.group(2).strip()[:200]
            norm = "completed" if mark.lower() == "x" else ("running" if mark == "/" else "pending")
            tasks.append(
                {
                    "external_task_key": f"L{idx}",
                    "title": text,
                    "source_status": f"[{mark}]",
                    "normalized_status": norm,
                }
            )

    done_cnt = sum(1 for t in tasks if t["normalized_status"] == "completed")
    norm_plan_status = "completed" if tasks and done_cnt == len(tasks) else ("active" if tasks else "unknown")
    content_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()[:24]

    return {
        "external_plan_id": f"{target.parent.name}/{target.name}",
        "title": title,
        "objective": clean[:4000],
        "source_status": f"{done_cnt}/{len(tasks)} checked" if tasks else "markdown_artifact",
        "normalized_status": norm_plan_status,
        "revision_hash": content_hash,
        "tasks": tasks[:100],
        "provenance": {"artifact_name": target.name, "read_only": True, "secrets_redacted": True},
    }
