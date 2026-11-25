"""Claude Code Stop / SessionEnd hook: capture bounded session metadata only.

Never reads transcript_path or source files. Always fails open (exit 0).
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time

from common import (
    http_json,
    load_bridge_settings,
    match_registered_scope,
    read_hook_stdin,
    resolve_git_scope,
)


def main(argv: list[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(add_help=False)
        parser.add_argument("--kind", default="")
        args, _ = parser.parse_known_args(argv)

        stdin_data = read_hook_stdin()
        cwd = str(stdin_data.get("cwd") or os.getcwd())
        scope = resolve_git_scope(cwd)
        if not scope:
            return 0

        raw_kind = (args.kind or str(stdin_data.get("hook_event_name") or "stop")).strip().lower()
        event_kind = "session_end" if raw_kind in {"sessionend", "session_end"} else "session_stop"

        raw_session_id = str(stdin_data.get("session_id") or "unknown")
        session_hash = hashlib.sha256(raw_session_id.encode("utf-8")).hexdigest()[:12]

        bridge_url, read_token, write_token = load_bridge_settings()
        project_id, worktree_id = match_registered_scope(bridge_url, read_token, scope, timeout=1.5)
        if not project_id or not worktree_id:
            return 0

        ts = int(time.time())
        idem_seed = f"{project_id}:{worktree_id}:{event_kind}:{session_hash}:{scope.head_commit}:{scope.dirty_fingerprint}"
        idempotency_key = hashlib.sha256(idem_seed.encode("utf-8")).hexdigest()[:24]

        payload = {
            "project_id": project_id,
            "worktree_id": worktree_id,
            "kind": event_kind,
            "idempotency_key": idempotency_key,
            "timestamp": ts,
            "payload": {
                "branch": scope.branch[:80],
                "head_commit": scope.head_commit[:40],
                "dirty": scope.dirty,
                "dirty_fingerprint": scope.dirty_fingerprint,
                "session_ref": session_hash,
            },
        }
        http_json(
            f"{bridge_url}/v1/events",
            method="POST",
            payload=payload,
            token=write_token or read_token,
            timeout=2.0,
        )
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
