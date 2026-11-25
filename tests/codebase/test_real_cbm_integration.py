"""Real integration tests against pinned codebase-memory-mcp v0.12.1 binary."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[2] / "services" / "codebase"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.cbm_client import StdioCBMClient  # noqa: E402
from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import WorktreeEntry  # noqa: E402
from app.registry import WorktreeRegistry  # noqa: E402

WSL_CBM_BIN = Path(r"D:\workspace1\_cbm_tmp\codebase-memory-mcp")


def _to_wsl_path(win_path: Path) -> str:
    s = str(win_path.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/host/{s[0].lower()}{s[2:]}"
    return s


def _has_wsl_cbm() -> bool:
    if os.name == "nt":
        return WSL_CBM_BIN.is_file()
    return Path("/usr/local/bin/codebase-memory-mcp").is_file()


@pytest.mark.skipif(not _has_wsl_cbm(), reason="codebase-memory-mcp v0.12.1 binary not available")
def test_real_cbm_java_ts_worktrees_dirty_and_restart(tmp_path: Path) -> None:
    """
    End-to-end verification with real codebase-memory-mcp v0.12.1:
    1. Synthetic Java and TypeScript repos with same symbol `processOrder` but different bodies.
    2. Two git worktrees (same branch & detached HEAD) of the TypeScript repo.
    3. Cross-project and cross-worktree isolation.
    4. Dirty edit detection (stale -> incremental refresh -> fresh).
    5. No full reindex on repeated session start (no_op=True).
    6. Restart persistence of SQLite state + CBM cache.
    7. Real MCP /mcp tools/list and tools/call(search_code).
    """
    repos_root = tmp_path / "repos"
    java_repo = repos_root / "billing-java"
    ts_main = repos_root / "billing-ts"
    ts_wt2 = repos_root / "billing-ts-wt2"

    (java_repo / "src").mkdir(parents=True)
    (java_repo / "src" / "Processor.java").write_text(
        "package com.example;\npublic class Processor {\n"
        "  public int processOrder(int amount) {\n    return amount * 999;\n  }\n}\n",
        encoding="utf-8",
    )

    # Create real git repo with 2 commits and a detached HEAD worktree
    (ts_main / "src").mkdir(parents=True)
    (ts_main / "src" / "processor.ts").write_text(
        "export function processOrder(amount: number): number {\n  return amount + 111;\n}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=ts_main, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=ts_main, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=ts_main, check=True)
    subprocess.run(["git", "add", "."], cwd=ts_main, check=True)
    subprocess.run(["git", "commit", "-m", "v1"], cwd=ts_main, check=True, capture_output=True)
    commit1 = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ts_main, text=True).strip()

    # Second commit on main
    (ts_main / "src" / "processor.ts").write_text(
        "export function processOrder(amount: number): number {\n  return amount + 222;\n}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=ts_main, check=True)
    subprocess.run(["git", "commit", "-m", "v2"], cwd=ts_main, check=True, capture_output=True)

    # Linked worktree on detached HEAD at commit1
    subprocess.run(["git", "worktree", "add", "--detach", str(ts_wt2), commit1], cwd=ts_main, check=True, capture_output=True)

    p_java, w_java = uuid.uuid4(), uuid.uuid4()
    p_ts, w_ts_main, w_ts_wt2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    data_dir = tmp_path / "data"
    cache_dir_win = tmp_path / "cbm-cache"
    cache_dir_win.mkdir(parents=True)

    if os.name == "nt":
        # Use /tmp/cbm_it_cache inside WSL2 ext4 so Unix sockets work, and clean up after
        cbm_cmd = [
            "wsl",
            "-d",
            "docker-desktop",
            "-e",
            "/bin/sh",
            "-c",
            "mkdir -p /tmp/cbm_it_cache /tmp/cbm_it_home && "
            "export CBM_CACHE_DIR=/tmp/cbm_it_cache HOME=/tmp/cbm_it_home; "
            f"exec {_to_wsl_path(WSL_CBM_BIN)} \"$@\"",
            "sh",
        ]
        # Note: repo paths passed to CBM must be WSL paths when running inside WSL
        java_cbm_root = _to_wsl_path(java_repo)
        ts_main_cbm_root = _to_wsl_path(ts_main)
        ts_wt2_cbm_root = _to_wsl_path(ts_wt2)
    else:
        cbm_cmd = ["/usr/local/bin/codebase-memory-mcp"]
        java_cbm_root = str(java_repo)
        ts_main_cbm_root = str(ts_main)
        ts_wt2_cbm_root = str(ts_wt2)

    class WSLPathStdioCBMClient(StdioCBMClient):
        """Translates Windows container_root paths to WSL paths when running CBM inside WSL2."""

        def __init__(self, path_map: dict[str, str], **kwargs) -> None:
            super().__init__(**kwargs)
            self._path_map = path_map

        async def _configure_safe_defaults(self) -> None:
            return None

        async def index_repository(
            self, *, repo_path: str, project_name: str, mode: str = "fast", timeout: float = 120.0
        ):
            mapped = self._path_map.get(repo_path, repo_path)
            return await super().index_repository(repo_path=mapped, project_name=project_name, mode=mode, timeout=timeout)

    path_map = {
        str(java_repo): java_cbm_root,
        str(ts_main): ts_main_cbm_root,
        str(ts_wt2): ts_wt2_cbm_root,
    }

    def build_app():
        cfg = Settings(
            data_dir=data_dir,
            cbm_cache_dir=cache_dir_win,
            cbm_allowed_root=repos_root,
            read_tokens={"read-tok"},
            admin_tokens={"admin-tok"},
        )
        reg = WorktreeRegistry(cfg)
        for pid, wid, cid, root in (
            (p_java, w_java, "billing-java", java_repo),
            (p_ts, w_ts_main, "billing-ts", ts_main),
            (p_ts, w_ts_wt2, "billing-ts", ts_wt2),
        ):
            reg.register(
                WorktreeEntry(
                    project_id=pid,
                    worktree_id=wid,
                    canonical_repo_id=cid,
                    container_root=str(root),
                    host_root=str(root),
                )
            )
        cbm = (
            WSLPathStdioCBMClient(
                path_map=path_map,
                command=cbm_cmd,
                cache_dir=cache_dir_win,
                allowed_root=None,
            )
            if os.name == "nt"
            else StdioCBMClient(command=cbm_cmd, cache_dir=cache_dir_win, allowed_root=repos_root)
        )
        return create_app(settings=cfg, cbm_backend=cbm, registry=reg)

    headers_read = {"Authorization": "Bearer read-tok"}
    headers_admin = {"Authorization": "Bearer admin-tok"}

    try:
        with TestClient(build_app()) as client:
            # Index all 3 scopes
            for pid, wid in ((p_java, w_java), (p_ts, w_ts_main), (p_ts, w_ts_wt2)):
                r = client.post("/v1/index", headers=headers_admin, json={"project_id": str(pid), "worktree_id": str(wid)})
                assert r.status_code == 200, r.text
                assert r.json()["state"] == "succeeded"

            # Search Java repo -> must return 999, never 111 or 222
            rj = client.post(
                "/v1/search/code",
                headers=headers_read,
                json={"project_id": str(p_java), "worktree_id": str(w_java), "query": "processOrder"},
            )
            assert rj.status_code == 200
            jbody = rj.json()
            assert jbody["index_freshness"] == "fresh"
            assert any("999" in it["excerpt"] for it in jbody["items"])
            assert all("111" not in it["excerpt"] and "222" not in it["excerpt"] for it in jbody["items"])

            # Search TS main -> must return 222, never 111 or 999
            rt1 = client.post(
                "/v1/search/code",
                headers=headers_read,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_main), "query": "processOrder"},
            )
            assert rt1.status_code == 200
            assert any("222" in it["excerpt"] for it in rt1.json()["items"])
            assert all("111" not in it["excerpt"] for it in rt1.json()["items"])

            # Search TS wt2 (detached HEAD) -> must return 111, never 222
            rt2 = client.post(
                "/v1/search/code",
                headers=headers_read,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_wt2), "query": "processOrder"},
            )
            assert rt2.status_code == 200
            assert any("111" in it["excerpt"] for it in rt2.json()["items"])
            assert all("222" not in it["excerpt"] for it in rt2.json()["items"])

            # Dirty edit on TS main -> search reports stale
            (ts_main / "src" / "processor.ts").write_text(
                "export function processOrder(amount: number): number {\n  return amount + 333;\n}\n",
                encoding="utf-8",
            )
            r_stale = client.post(
                "/v1/search/code",
                headers=headers_read,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_main), "query": "processOrder"},
            )
            assert r_stale.status_code == 200
            assert r_stale.json()["index_freshness"] == "stale"

            # Reindex TS main -> fresh and returns 333
            r_reidx = client.post(
                "/v1/index",
                headers=headers_admin,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_main)},
            )
            assert r_reidx.status_code == 200
            assert r_reidx.json()["no_op"] is False

            # Repeated index on session start -> no_op=True
            r_noop = client.post(
                "/v1/index",
                headers=headers_admin,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_main)},
            )
            assert r_noop.status_code == 200
            assert r_noop.json()["no_op"] is True

        # Simulated restart: new app instance using persisted data_dir & CBM cache
        with TestClient(build_app()) as client2:
            r_persist = client2.post(
                "/v1/search/code",
                headers=headers_read,
                json={"project_id": str(p_ts), "worktree_id": str(w_ts_main), "query": "processOrder"},
            )
            assert r_persist.status_code == 200
            pbody = r_persist.json()
            assert pbody["index_freshness"] == "fresh"
            assert any("333" in it["excerpt"] for it in pbody["items"])
            assert pbody["elapsed_ms"] is not None and pbody["elapsed_ms"] < 5000

            # MCP /mcp tools/list and tools/call
            r_tools = client2.post(
                "/mcp",
                headers=headers_read,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            )
            assert r_tools.status_code == 200
            assert [t["name"] for t in r_tools.json()["result"]["tools"]] == ["search_code"]

            r_call = client2.post(
                "/mcp",
                headers=headers_read,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "search_code",
                        "arguments": {
                            "project_id": str(p_ts),
                            "worktree_id": str(w_ts_main),
                            "query": "processOrder",
                        },
                    },
                },
            )
            assert r_call.status_code == 200
            assert r_call.json()["result"]["isError"] is False
            assert "333" in r_call.json()["result"]["content"][0]["text"]
    finally:
        if os.name == "nt":
            subprocess.run(
                ["wsl", "-d", "docker-desktop", "-e", "/bin/sh", "-c", "rm -rf /tmp/cbm_it_cache /tmp/cbm_it_home"],
                capture_output=True,
            )
