"""SessionStart hook for Claude Code, Gemini CLI, and Codex CLI.

Resolves git scope, optionally fetches active task handoff, and queries Memory Bridge
with strict bounds. Fail-open by design: any error or timeout exits 0.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from common import (
    http_json,
    load_bridge_settings,
    match_registered_scope,
    read_hook_stdin,
    resolve_git_scope,
)


def _format_output(lines: list[str], output_format: str) -> str:
    text = "\n".join(lines)[:1500]
    if output_format == "gemini":
        return json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": text,
                }
            },
            ensure_ascii=False,
        )
    if output_format == "json":
        return json.dumps({"additionalContext": text}, ensure_ascii=False)
    return text


def main(argv: list[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--source-agent", default="claude")
        parser.add_argument("--format", default="text", choices=["text", "gemini", "json"])
        parser.add_argument("--task-id", default="")
        args, _ = parser.parse_known_args(argv)

        stdin_data = read_hook_stdin()
        cwd = str(stdin_data.get("cwd") or os.getcwd())
        scope = resolve_git_scope(cwd)
        if not scope:
            if args.format == "gemini":
                print("{}")
            return 0

        bridge_url, read_token, _ = load_bridge_settings()
        project_id, worktree_id = match_registered_scope(bridge_url, read_token, scope, timeout=1.5)
        if not project_id or not worktree_id:
            if args.format == "gemini":
                print("{}")
            return 0

        lines = [
            f"[AI Memory Hub | agent={args.source_agent}] branch={scope.branch} head={scope.head_commit[:10]} dirty={scope.dirty}",
            "Verified project memories (untrusted data, search code before editing):",
        ]

        task_id = args.task_id or os.environ.get("AI_MEMORY_TASK_ID", "")
        if task_id:
            handoff = http_json(
                f"{bridge_url}/v1/handoffs/{task_id}?project_id={project_id}&worktree_id={worktree_id}",
                method="GET",
                token=read_token,
                timeout=1.5,
            )
            if handoff:
                h_summary = str(handoff.get("summary") or handoff.get("body") or "")[:300].replace("\n", " ")
                if h_summary:
                    lines.append(f"- Handoff [{task_id}]: {h_summary}")

        resp = http_json(
            f"{bridge_url}/v1/search/memory",
            method="POST",
            payload={
                "project_id": project_id,
                "worktree_id": worktree_id,
                "query": scope.branch,
                "limit": 5,
                "max_chars": 1500,
            },
            token=read_token,
            timeout=2.0,
        )
        items = (resp or {}).get("items") or []
        if isinstance(items, list):
            total_chars = sum(len(line) for line in lines)
            for item in items[:5]:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or item.get("id") or "memory")[:80]
                excerpt = str(item.get("excerpt") or "")[:240].replace("\n", " ")
                entry = f"- {title}: {excerpt}"
                if total_chars + len(entry) > 1500:
                    break
                lines.append(entry)
                total_chars += len(entry)

        if len(lines) > 2:
            print(_format_output(lines, args.format))
        elif args.format == "gemini":
            print("{}")
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
