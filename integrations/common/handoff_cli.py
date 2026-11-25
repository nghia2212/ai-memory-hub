"""Cross-agent task handoff CLI using shared Memory Bridge (no separate DB)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parents[1] / "claude" / "hooks"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))

from common import (  # noqa: E402
    http_json,
    load_bridge_settings,
    match_registered_scope,
    resolve_git_scope,
)

VALID_AGENTS = ("claude", "gemini", "codex", "hermes")


def create_handoff(
    *,
    source_agent: str,
    target_agent: str,
    task_id: str,
    summary: str,
    cwd: str | None = None,
    project_id: str | None = None,
    worktree_id: str | None = None,
) -> dict:
    bridge_url, read_token, write_token = load_bridge_settings()
    scope = resolve_git_scope(cwd or os.getcwd())
    if not project_id or not worktree_id:
        if not scope:
            return {"ok": False, "error": "git_scope_not_found"}
        project_id, worktree_id = match_registered_scope(bridge_url, read_token, scope, timeout=2.0)
        if not project_id or not worktree_id:
            return {"ok": False, "error": "unregistered_project_or_worktree"}

    bounded_summary = summary.strip()[:600]
    ts = int(time.time())
    seed = f"{project_id}:{worktree_id}:{task_id}:{source_agent}:{target_agent}:{bounded_summary}"
    idem_key = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]

    payload = {
        "project_id": project_id,
        "worktree_id": worktree_id,
        "task_id": task_id,
        "source_agent": source_agent,
        "kind": "handoff_created",
        "idempotency_key": idem_key,
        "timestamp": ts,
        "payload": {
            "target_agent": target_agent,
            "summary": bounded_summary,
            "branch": scope.branch if scope else "unknown",
            "head_commit": scope.head_commit[:40] if scope else "unknown",
        },
    }
    resp = http_json(
        f"{bridge_url}/v1/events",
        method="POST",
        payload=payload,
        token=write_token or read_token,
        timeout=3.0,
    )
    if resp is None:
        return {"ok": False, "error": "bridge_unavailable"}
    return {"ok": True, "project_id": project_id, "worktree_id": worktree_id, "task_id": task_id, "response": resp}


def get_handoff(
    *,
    task_id: str,
    cwd: str | None = None,
    project_id: str | None = None,
    worktree_id: str | None = None,
) -> dict:
    bridge_url, read_token, _ = load_bridge_settings()
    if not project_id or not worktree_id:
        scope = resolve_git_scope(cwd or os.getcwd())
        if not scope:
            return {"ok": False, "error": "git_scope_not_found"}
        project_id, worktree_id = match_registered_scope(bridge_url, read_token, scope, timeout=2.0)
        if not project_id or not worktree_id:
            return {"ok": False, "error": "unregistered_project_or_worktree"}

    url = f"{bridge_url}/v1/handoffs/{task_id}?project_id={project_id}&worktree_id={worktree_id}"
    resp = http_json(url, method="GET", token=read_token, timeout=3.0)
    if resp is None:
        return {"ok": False, "error": "handoff_not_found_or_bridge_unavailable"}
    return {"ok": True, "project_id": project_id, "worktree_id": worktree_id, "task_id": task_id, "handoff": resp}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI Memory Hub cross-agent handoff CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create", help="Create a bounded task handoff")
    p_create.add_argument("--source-agent", required=True, choices=VALID_AGENTS)
    p_create.add_argument("--target-agent", required=True, choices=VALID_AGENTS)
    p_create.add_argument("--task-id", required=True)
    p_create.add_argument("--summary", required=True)
    p_create.add_argument("--cwd", default=None)
    p_create.add_argument("--project-id", default=None)
    p_create.add_argument("--worktree-id", default=None)

    p_get = sub.add_parser("get", help="Retrieve a task handoff")
    p_get.add_argument("--task-id", required=True)
    p_get.add_argument("--cwd", default=None)
    p_get.add_argument("--project-id", default=None)
    p_get.add_argument("--worktree-id", default=None)

    args = parser.parse_args(argv)
    if args.cmd == "create":
        res = create_handoff(
            source_agent=args.source_agent,
            target_agent=args.target_agent,
            task_id=args.task_id,
            summary=args.summary,
            cwd=args.cwd,
            project_id=args.project_id,
            worktree_id=args.worktree_id,
        )
    else:
        res = get_handoff(
            task_id=args.task_id,
            cwd=args.cwd,
            project_id=args.project_id,
            worktree_id=args.worktree_id,
        )

    print(json.dumps(res, ensure_ascii=False))
    return 0 if res.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
