"""End-to-end cross-service integration tests for AI Memory Hub V1/V1.1.

Verifies real Memory Bridge + Codebase Adapter + Dashboard + Hermes Worker +
Hooks + Handoff CLI working together without mocked contracts between services.
"""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
HOOKS_DIR = ROOT / "integrations" / "claude" / "hooks"
HANDOFF_CLI = ROOT / "integrations" / "common" / "handoff_cli.py"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _load_pkg_module(service_dir: Path, pkg_alias: str, module_name: str):
    """Load a service's `app.<module_name>` without colliding `app` package names across services."""
    sdir = str(service_dir)
    if sdir in sys.path:
        sys.path.remove(sdir)
    sys.path.insert(0, sdir)
    # Clear cached `app` modules so each service loads cleanly
    for key in list(sys.modules.keys()):
        if key == "app" or key.startswith("app."):
            del sys.modules[key]
    mod = __import__(f"app.{module_name}", fromlist=["*"])
    return mod


def _start_uvicorn(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            r = httpx.get(f"{base}/livez", timeout=0.5)
            if r.status_code == 200:
                return server, thread
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Service on port {port} failed to start")


class _SyntheticCBMBackend:
    """Deterministic in-memory CBM backend for cross-service wiring tests (zero LLM)."""

    def __init__(self) -> None:
        self.indexed: dict[str, str] = {}

    def is_ready(self) -> bool:
        return True

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def index_repository(self, repo_path: str, project_name: str, mode: str, timeout: float) -> dict[str, Any]:
        self.indexed[project_name] = repo_path
        return {"nodes": 12, "edges": 8}

    async def search_graph(self, project_name: str, name_pattern: str, limit: int, timeout: float) -> dict[str, Any]:
        repo_path = self.indexed.get(project_name)
        if not repo_path:
            return {"groups": []}
        root = Path(repo_path)
        groups = []
        for f in root.rglob("*.py"):
            text = f.read_text(encoding="utf-8", errors="replace")
            if "processOrder" in text:
                rel = f.relative_to(root).as_posix()
                groups.append({"qn_prefix": "app", "file": rel, "rows": [["processOrder", "Function", "1-3", 0, 0]]})
        return {"groups": groups}

    async def search_code(self, project_name: str, pattern: str, limit: int, timeout: float) -> dict[str, Any]:
        return {"cols": ["qn", "label", "file", "lines"], "rows": []}


def _init_git_repo(path: Path, body: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.email", "lead@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Lead Dev"], cwd=str(path), check=True)
    (path / "orders.py").write_text(body, encoding="utf-8")
    subprocess.run(["git", "add", "orders.py"], cwd=str(path), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True)
    return path


def test_full_v1_v11_cross_service_integration(tmp_path: Path):
    repos_root = tmp_path / "repos"
    repo_a = _init_git_repo(repos_root / "repo-a", "def processOrder(order_id):\n    return f'A:{order_id}'\n")
    repo_b = _init_git_repo(repos_root / "repo-b", "def processOrder(order_id):\n    return f'B:{order_id}'\n")

    bridge_port = _free_port()
    codebase_port = _free_port()
    dashboard_port = _free_port()

    # 1. Start Codebase Adapter pointed at Bridge for registry discovery
    cb_cfg_mod = _load_pkg_module(ROOT / "services" / "codebase", "codebase", "config")
    cb_main_mod = __import__("app.main", fromlist=["create_app"])
    cb_settings = cb_cfg_mod.Settings(
        port=codebase_port,
        data_dir=tmp_path / "cb-data",
        cbm_cache_dir=tmp_path / "cb-data" / "cache",
        cbm_allowed_root=repos_root,
        bridge_url=f"http://127.0.0.1:{bridge_port}",
        bridge_token="tok-read",
        read_tokens={"cb-read"},
        admin_tokens={"cb-admin"},
    )
    cb_app = cb_main_mod.create_app(settings=cb_settings, cbm_backend=_SyntheticCBMBackend())

    # 2. Start Memory Bridge pointed at Codebase Adapter
    br_cfg_mod = _load_pkg_module(ROOT / "services" / "memory-bridge", "bridge", "config")
    br_main_mod = __import__("app.main", fromlist=["create_app"])
    br_settings = br_cfg_mod.Settings(
        port=bridge_port,
        data_dir=tmp_path / "br-data",
        db_path=tmp_path / "br-data" / "bridge.db",
        backup_dir=tmp_path / "br-data" / "backups",
        codebase_url=f"http://127.0.0.1:{codebase_port}",
        codebase_read_token="cb-read",
        codebase_admin_token="cb-admin",
        read_tokens={"tok-read"},
        write_tokens={"tok-write"},
        admin_tokens={"tok-admin"},
        worker_tokens={"tok-worker"},
    )
    br_app = br_main_mod.create_app(settings=br_settings)

    # 3. Start Vietnamese Dashboard pointed at Bridge & Codebase
    db_cfg_mod = _load_pkg_module(ROOT / "services" / "dashboard", "dashboard", "config")
    db_main_mod = __import__("app.main", fromlist=["create_dashboard_app"])
    db_settings = db_cfg_mod.DashboardSettings(
        port=dashboard_port,
        bridge_url=f"http://127.0.0.1:{bridge_port}",
        codebase_url=f"http://127.0.0.1:{codebase_port}",
        hermes_url="http://127.0.0.1:59998",  # Hermes disabled / stopped
        bridge_read_token="tok-read",
        bridge_admin_token="tok-admin",
        dashboard_password="lead-test-password",
        session_secret="lead-test-secret",
        allowed_origins={f"http://127.0.0.1:{dashboard_port}"},
    )
    db_app = db_main_mod.create_dashboard_app(settings=db_settings)

    cb_srv, cb_t = _start_uvicorn(cb_app, codebase_port)
    br_srv, br_t = _start_uvicorn(br_app, bridge_port)
    db_srv, db_t = _start_uvicorn(db_app, dashboard_port)

    try:
        br_url = f"http://127.0.0.1:{bridge_port}"
        db_url = f"http://127.0.0.1:{dashboard_port}"
        admin_h = {"Authorization": "Bearer tok-admin"}
        read_h = {"Authorization": "Bearer tok-read"}
        write_h = {"Authorization": "Bearer tok-write"}

        # A. Verify Dashboard Vietnamese UI & status when Hermes is OFF
        dash_html = httpx.get(f"{db_url}/", timeout=5.0)
        assert dash_html.status_code == 200
        assert "AI Memory Hub" in dash_html.text
        assert "lead-test-password" not in dash_html.text
        assert "tok-admin" not in dash_html.text

        status = httpx.get(f"{db_url}/api/status", timeout=5.0).json()
        assert status["bridge"]["ready"] is True
        assert status["codebase"]["ready"] is True
        assert status["hermes"]["ready"] is False  # Hermes disabled

        # B. Register 2 separate repositories (multi-repo isolation)
        p_a = httpx.post(
            f"{br_url}/v1/projects",
            headers=admin_h,
            json={"name": "Repo A", "canonical_identity": "git:repo-a", "hermes_approved": True},
        ).json()["project_id"]
        w_a = httpx.post(
            f"{br_url}/v1/projects/{p_a}/worktrees",
            headers=admin_h,
            json={"canonical_root": str(repo_a), "branch": "main", "head_commit": "aaaa111"},
        ).json()["worktree_id"]

        p_b = httpx.post(
            f"{br_url}/v1/projects",
            headers=admin_h,
            json={"name": "Repo B", "canonical_identity": "git:repo-b", "hermes_approved": False},
        ).json()["project_id"]
        w_b = httpx.post(
            f"{br_url}/v1/projects/{p_b}/worktrees",
            headers=admin_h,
            json={"canonical_root": str(repo_b), "branch": "main", "head_commit": "bbbb222"},
        ).json()["worktree_id"]

        # C. Trigger index via Bridge -> Codebase discovers worktree from Bridge -> Job synced!
        idx_a = httpx.post(
            f"{br_url}/v1/index",
            headers=admin_h,
            json={"project_id": p_a, "worktree_id": w_a, "force": True},
            timeout=15.0,
        )
        assert idx_a.status_code in (200, 202), idx_a.text
        job_a = idx_a.json()
        assert job_a["status"] == "succeeded"

        # Query synced index job via Bridge GET /v1/index/{job_id}
        job_get = httpx.get(
            f"{br_url}/v1/index/{job_a['job_id']}",
            params={"project_id": p_a, "worktree_id": w_a},
            headers=read_h,
            timeout=5.0,
        )
        assert job_get.status_code == 200
        assert job_get.json()["status"] == "succeeded"

        # Search code via Bridge -> Codebase returns Repo A's processOrder only
        sc_a = httpx.post(
            f"{br_url}/v1/search/code",
            headers=read_h,
            json={"project_id": p_a, "worktree_id": w_a, "query": "processOrder"},
            timeout=10.0,
        )
        assert sc_a.status_code == 200
        assert len(sc_a.json()["items"]) == 1
        assert "A:" in sc_a.json()["items"][0]["excerpt"]
        assert "B:" not in sc_a.json()["items"][0]["excerpt"]

        # D. Real Hooks & Handoff CLI against Real Memory Bridge
        env = dict(os.environ)
        env["AI_MEMORY_BRIDGE_URL"] = br_url
        env["AI_MEMORY_BRIDGE_READ_TOKEN"] = "tok-read"
        env["AI_MEMORY_BRIDGE_WRITE_TOKEN"] = "tok-write"

        # Create handoff via CLI (must write to handoffs table AND events table!)
        p_ho = subprocess.run(
            [
                sys.executable,
                str(HANDOFF_CLI),
                "create",
                "--source-agent",
                "claude",
                "--target-agent",
                "codex",
                "--task-id",
                "TASK-INT-01",
                "--summary",
                "Hoàn tất sửa lỗi tích hợp V1, chuyển sang Codex kiểm thử.",
                "--cwd",
                str(repo_a),
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert p_ho.returncode == 0, p_ho.stdout + p_ho.stderr

        # Verify handoff is in Bridge `handoffs` table via GET /v1/handoffs/{task_id}
        ho_get = httpx.get(
            f"{br_url}/v1/handoffs/TASK-INT-01",
            params={"project_id": p_a, "worktree_id": w_a},
            headers=read_h,
        )
        assert ho_get.status_code == 200
        assert len(ho_get.json()["items"]) == 1
        assert "Hoàn tất sửa lỗi tích hợp V1" in ho_get.json()["items"][0]["summary"]

        # Verify session_start.py reads project_id, worktree_id, canonical_root and handoff items[0]
        p_start = subprocess.run(
            [
                sys.executable,
                str(HOOKS_DIR / "session_start.py"),
                "--source-agent",
                "gemini",
                "--format",
                "gemini",
                "--task-id",
                "TASK-INT-01",
            ],
            input=json.dumps({"cwd": str(repo_a)}),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert p_start.returncode == 0, p_start.stderr
        gemini_out = json.loads(p_start.stdout)
        assert "TASK-INT-01" in gemini_out["hookSpecificOutput"]["additionalContext"]
        assert "Hoàn tất sửa lỗi tích hợp V1" in gemini_out["hookSpecificOutput"]["additionalContext"]

        # Verify session_event.py posts event -> GET /v1/agents/status sees claude & gemini
        p_ev = subprocess.run(
            [
                sys.executable,
                str(HOOKS_DIR / "session_event.py"),
                "--kind",
                "session_end",
                "--source-agent",
                "gemini",
                "--task-id",
                "TASK-INT-01",
            ],
            input=json.dumps({"cwd": str(repo_a), "session_id": "sess-gem-1"}),
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )
        assert p_ev.returncode == 0

        agents_st = httpx.get(
            f"{br_url}/v1/agents/status",
            params={"project_id": p_a, "worktree_id": w_a},
            headers=read_h,
        ).json()
        seen_agents = {item["source_agent"] for item in agents_st["items"]}
        assert {"claude", "gemini"}.issubset(seen_agents)

        # E. Real Hermes Worker -> Real Memory Bridge (MockProvider, ZERO paid LLM calls!)
        # Approve the gemini session_end event as admin
        ev_list = httpx.get(f"{br_url}/v1/events", params={"project_id": p_a}, headers=read_h).json()["items"]
        target_ev = next(e for e in ev_list if e["kind"] == "session_end")
        approve_r = httpx.post(f"{br_url}/v1/events/{target_ev['event_id']}/approve", headers=admin_h)
        assert approve_r.status_code == 200

        hermes_cfg_mod = _load_pkg_module(ROOT / "services" / "hermes", "hermes", "config")
        hermes_bc_mod = __import__("app.bridge_client", fromlist=["BridgeClient"])
        hermes_w_mod = __import__("app.worker", fromlist=["HermesWorker"])

        h_cfg = hermes_cfg_mod.HermesConfig.from_env(
            {
                "HERMES_ENABLED": "true",
                "HERMES_PROVIDER": "mock",
                "BRIDGE_URL": br_url,
                "BRIDGE_WORKER_TOKEN": "tok-worker",
                "HERMES_STATE_DIR": str(tmp_path / "hermes-state"),
            }
        )
        h_bridge = hermes_bc_mod.BridgeClient(base_url=br_url, worker_token="tok-worker")
        h_worker = hermes_w_mod.HermesWorker(config=h_cfg, bridge=h_bridge)

        run1 = h_worker.run_once()
        assert run1.fetched >= 1
        assert run1.processed >= 1
        assert run1.errors == 0

        # Second run sees 0 unprocessed approved events (and zero duplicate LLM calls!)
        run2 = h_worker.run_once()
        assert run2.processed == 0
        assert run2.errors == 0

        # Candidate memory created by Hermes appears in Bridge memories list as 'candidate'
        mems = httpx.get(
            f"{br_url}/v1/memories",
            params={"project_id": p_a, "state": "candidate"},
            headers=read_h,
        ).json()["items"]
        assert any(m["source_agent"] == "hermes-worker" for m in mems)

    finally:
        db_srv.should_exit = True
        br_srv.should_exit = True
        cb_srv.should_exit = True
        db_t.join(timeout=3)
        br_t.join(timeout=3)
        cb_t.join(timeout=3)
