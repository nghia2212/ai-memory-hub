"""Shared fail-open helpers for Claude Code AI Memory Hub hooks."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GitScope:
    repo_root: str
    git_common_dir: str
    worktree_root: str
    branch: str
    head_commit: str
    dirty: bool
    dirty_fingerprint: str


def _run_git(cwd: str, args: list[str], timeout: float = 1.5) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", "-C", cwd, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip()
    except Exception:
        return 1, ""


def resolve_git_scope(cwd: str | None = None) -> GitScope | None:
    target = cwd or os.getcwd()
    if not target or not os.path.isdir(target):
        return None

    rc, toplevel = _run_git(target, ["rev-parse", "--show-toplevel"])
    if rc != 0 or not toplevel:
        return None

    _, common_dir = _run_git(target, ["rev-parse", "--git-common-dir"])
    _, head = _run_git(target, ["rev-parse", "HEAD"])
    rc_branch, branch = _run_git(target, ["symbolic-ref", "--short", "HEAD"])
    if rc_branch != 0 or not branch:
        branch = "DETACHED"

    rc_status, porcelain = _run_git(target, ["status", "--porcelain"])
    dirty = bool(rc_status == 0 and porcelain)
    dirty_fp = hashlib.sha256(porcelain.encode("utf-8")).hexdigest()[:12] if dirty else "clean"

    norm_top = str(Path(toplevel).resolve())
    norm_common = str((Path(target) / common_dir).resolve()) if common_dir else norm_top
    return GitScope(
        repo_root=norm_top,
        git_common_dir=norm_common,
        worktree_root=norm_top,
        branch=branch,
        head_commit=head or "unknown",
        dirty=dirty,
        dirty_fingerprint=dirty_fp,
    )


def read_hook_stdin(max_bytes: int = 16384) -> dict[str, Any]:
    """Read bounded JSON from stdin without raising."""
    try:
        import sys

        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read(max_bytes)
        if not raw or not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def load_bridge_settings() -> tuple[str, str, str]:
    """Return (bridge_url, read_token, write_token) from env or hub .env."""
    env_vals: dict[str, str] = {}
    hub_root = Path(__file__).resolve().parents[3]
    env_file = hub_root / ".env"
    if env_file.is_file():
        try:
            for line in env_file.read_text(encoding="utf-8", errors="replace").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                k, v = stripped.split("=", 1)
                env_vals[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass

    def get(key: str, default: str = "") -> str:
        return os.environ.get(key) or env_vals.get(key) or default

    port = get("BRIDGE_PORT", "8082")
    bridge_url = get("AI_MEMORY_BRIDGE_URL", f"http://127.0.0.1:{port}").rstrip("/")
    read_token = get("AI_MEMORY_BRIDGE_READ_TOKEN", get("BRIDGE_READ_TOKEN", ""))
    write_token = get("AI_MEMORY_BRIDGE_WRITE_TOKEN", get("BRIDGE_WRITE_TOKEN", ""))
    return bridge_url, read_token, write_token


def http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str = "",
    timeout: float = 2.0,
) -> dict[str, Any] | None:
    """Perform a bounded HTTP JSON call; returns None on any error/timeout."""
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(32768).decode("utf-8", errors="replace")
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def match_registered_scope(
    bridge_url: str,
    read_token: str,
    scope: GitScope,
    timeout: float = 1.5,
) -> tuple[str | None, str | None]:
    """Look up registered (project_id, worktree_id) matching git scope."""
    projects_resp = http_json(f"{bridge_url}/v1/projects", method="GET", token=read_token, timeout=timeout)
    if not projects_resp:
        return None, None
    projects = projects_resp.get("items") or projects_resp.get("projects") or []
    if not isinstance(projects, list):
        return None, None

    norm_worktree = os.path.normcase(os.path.normpath(scope.worktree_root))
    for proj in projects:
        if not isinstance(proj, dict):
            continue
        raw_pid = proj.get("project_id") or proj.get("id")
        if not raw_pid:
            continue
        pid = str(raw_pid)
        wt_resp = http_json(
            f"{bridge_url}/v1/projects/{pid}/worktrees",
            method="GET",
            token=read_token,
            timeout=timeout,
        )
        if not wt_resp:
            continue
        worktrees = wt_resp.get("items") or wt_resp.get("worktrees") or []
        if not isinstance(worktrees, list):
            continue
        for wt in worktrees:
            if not isinstance(wt, dict):
                continue
            raw_wid = wt.get("worktree_id") or wt.get("id")
            if not raw_wid:
                continue
            wt_path = str(wt.get("canonical_root") or wt.get("host_path") or wt.get("path") or "")
            if wt_path and os.path.normcase(os.path.normpath(wt_path)) == norm_worktree:
                return pid, str(raw_wid)
    return None, None
