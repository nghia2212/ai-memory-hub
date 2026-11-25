"""End-to-end V2 cross-agent integration test: Agent A + Agent B + Agent C.

Exercises:
- Agent A: CatalogService, DeterministicRouter, resolve_brain_roles, ForecastEngine
- Agent B: Real Memory Bridge V2 SQLite + FastAPI (/v2/*) + Vietnamese Dashboard (/api/v2/*)
- Agent C: DAGPlanner + HostRunner + HttpRunnerBridge against real Bridge HTTP (fake CLI proc, 0 paid calls)
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import uvicorn

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.orchestrator.catalog.registry import CatalogService
from services.orchestrator.catalog.sources import HostDiscoveryCatalogMapper
from services.orchestrator.contracts import (
    BrainPolicyConfig,
    HostCapabilityReport,
    HostCLICapability,
    ModelRecord,
)
from services.orchestrator.dispatch.host_runner import HostRunner, HttpRunnerBridge
from services.orchestrator.forecast.engine import ForecastEngine
from services.orchestrator.planner.brain_adapters import RuleBrainAdapter
from services.orchestrator.planner.dag_planner import build_plan_from_brain
from services.orchestrator.routing.brain_resolver import resolve_brain_roles


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _load_service(service_dir: Path, module_name: str):
    sdir = str(service_dir)
    if sdir in sys.path:
        sys.path.remove(sdir)
    sys.path.insert(0, sdir)
    for k in list(sys.modules.keys()):
        if k == "app" or k.startswith("app."):
            del sys.modules[k]
    return __import__(f"app.{module_name}", fromlist=["*"])


def _start_uvicorn(app: Any, port: int) -> tuple[uvicorn.Server, threading.Thread]:
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(60):
        try:
            if httpx.get(f"http://127.0.0.1:{port}/livez", timeout=0.5).status_code == 200:
                return srv, t
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"Service on port {port} failed to start")


def test_v2_cross_agent_a_b_c_end_to_end(tmp_path: Path):
    worktree_dir = tmp_path / "demo-wt"
    worktree_dir.mkdir(parents=True, exist_ok=True)

    bridge_port = _free_port()
    dash_port = _free_port()

    br_cfg_mod = _load_service(ROOT / "services" / "memory-bridge", "config")
    br_main_mod = __import__("app.main", fromlist=["create_app"])
    br_app = br_main_mod.create_app(
        br_cfg_mod.Settings(
            port=bridge_port,
            data_dir=tmp_path / "br",
            db_path=tmp_path / "br" / "v2_e2e.db",
            backup_dir=tmp_path / "br" / "bak",
            read_tokens={"tok-read"},
            write_tokens={"tok-write"},
            admin_tokens={"tok-admin"},
            worker_tokens={"tok-worker"},
            planner_tokens={"tok-planner"},
            runner_tokens={"tok-runner"},
        )
    )

    db_cfg_mod = _load_service(ROOT / "services" / "dashboard", "config")
    db_main_mod = __import__("app.main", fromlist=["create_dashboard_app"])
    db_app = db_main_mod.create_dashboard_app(
        db_cfg_mod.DashboardSettings(
            port=dash_port,
            bridge_url=f"http://127.0.0.1:{bridge_port}",
            codebase_url="http://127.0.0.1:59997",
            hermes_url="http://127.0.0.1:59998",
            bridge_read_token="tok-read",
            bridge_admin_token="tok-admin",
            dashboard_password="v2-lead-password",
            session_secret="v2-lead-secret",
            allowed_origins={f"http://127.0.0.1:{dash_port}"},
        )
    )

    br_srv, br_t = _start_uvicorn(br_app, bridge_port)
    db_srv, db_t = _start_uvicorn(db_app, dash_port)
    try:
        br_url = f"http://127.0.0.1:{bridge_port}"
        db_url = f"http://127.0.0.1:{dash_port}"
        admin_h = {"Authorization": "Bearer tok-admin"}
        planner_h = {"Authorization": "Bearer tok-planner"}
        read_h = {"Authorization": "Bearer tok-read"}

        # 1. Register project & worktree via Bridge V1
        pid = httpx.post(
            f"{br_url}/v1/projects",
            headers=admin_h,
            json={"name": "V2-E2E", "canonical_identity": "git:v2-e2e"},
        ).json()["project_id"]
        wid = httpx.post(
            f"{br_url}/v1/projects/{pid}/worktrees",
            headers=admin_h,
            json={"canonical_root": str(worktree_dir), "branch": "lead/v2-orchestrator", "head_commit": "abc1234"},
        ).json()["worktree_id"]

        # 2. Agent A: resolve Adaptive Brain roles against real agy 1.1.27 capability snapshot
        now_iso = datetime.now(timezone.utc).isoformat()
        host_report = HostCapabilityReport(
            host_os="Windows 11",
            discovered_at=now_iso,
            clis=[
                HostCLICapability(
                    cli_name="agy",
                    installed=True,
                    version="1.1.27",
                    auth_mode="subscription_cli",
                    noninteractive_supported=True,
                    structured_json_supported=True,
                    verified_model_ids=[
                        "gemini-3.8-flash-medium",
                        "gemini-3.8-flash-high",
                        "claude-sonnet-5-5-medium",
                        "claude-opus-5-5-medium",
                    ],
                    verified_at=now_iso,
                )
            ],
        )
        policy = BrainPolicyConfig(project_id=UUID(pid), approval_mode="REVIEW_REQUIRED", allow_paid_api_fallback=False)
        cat_service = CatalogService(adapters=[])
        snapshot = cat_service.refresh(host_report=host_report)
        roles = resolve_brain_roles(snapshot=snapshot, host_report=host_report, policy=policy)
        role_map = {r.role_name: r for r in roles}
        assert role_map["tier2_primary"].availability_status == "unavailable"
        assert role_map["secondary_fallback"].effective_model_id == "claude-sonnet-5-5-medium"

        # 3. Persist catalog snapshot & Adaptive Brain config into Agent B's Bridge
        sonnet_model = ModelRecord(
            provider="agy",
            model_id="claude-sonnet-5-5-medium",
            cli_family="agy",
            cli_version="1.1.27",
            auth_mode="subscription_cli",
            status="verified",
            context_limit=200000,
            output_limit=16384,
        )
        cat_r = httpx.post(
            f"{br_url}/v2/catalog/refresh",
            headers=admin_h,
            json={
                "version": 1,
                "source_urls": ["https://ai.google.dev/"],
                "models": [sonnet_model.model_dump(mode="json")],
            },
        )
        assert cat_r.status_code == 201, cat_r.text

        # 4. Agent C: Build bounded DAG plan using Tier 0 rule brain (0 LLM) + Agent A forecast
        plan_req, _client_rev_hash, _brain_resp = build_plan_from_brain(
            project_id=UUID(pid),
            worktree_id=UUID(wid),
            objective="Tích hợp V2 Orchestrator end-to-end",
            policy=policy,
            adapter=RuleBrainAdapter(),
            role="tier0_rules",
            assigned_agent="agy",
            assigned_provider="agy",
            assigned_model_id="claude-sonnet-5-5-medium",
            assigned_auth_mode="subscription_cli",
        )
        # Agent A cold-start forecast (0 fake observations)
        forecast_engine = ForecastEngine()
        task_forecasts = [
            forecast_engine.forecast_task(
                task_key=t.task_key,
                project_id=UUID(pid),
                features=t.features,
                model=sonnet_model,
                effort=t.assigned_effort,
                samples=[],
            )
            for t in plan_req.tasks
        ]
        assert all(tf.confidence == "cold_start" for tf in task_forecasts)
        assert all(tf.quota_impact_pct is None for tf in task_forecasts)

        # Submit plan to Bridge /v2/plans (REVIEW_REQUIRED)
        create_plan_r = httpx.post(
            f"{br_url}/v2/plans",
            headers=planner_h,
            json=plan_req.model_dump(mode="json"),
        )
        assert create_plan_r.status_code == 201, create_plan_r.text
        plan_data = create_plan_r.json()
        plan_id = plan_data["id"]
        rev_id = plan_data["current_revision_id"]
        rev_hash = plan_data["revisions"][0]["revision_hash"]

        # 5. HostRunner CANNOT claim unapproved plan
        http_bridge = HttpRunnerBridge(br_url, "tok-runner", project_id=pid, worktree_id=wid)

        def fake_cli_proc(cmd: list[str], cwd: str, timeout: float):
            out = json.dumps(
                {
                    "session_id": "agy-sess-e2e-1",
                    "usage": {"input_tokens": 320, "output_tokens": 110, "cache_read_tokens": 64},
                    "summary": "Subtask completed cleanly via fake CLI.",
                }
            )
            return 0, out, ""

        runner = HostRunner(
            http_bridge,
            runner_id="lead-e2e-runner",
            executable_overrides={"agy": "agy.exe"},
            subprocess_runner=fake_cli_proc,
        )
        assert runner.run_once()["claimed"] is False

        # 6. Admin atomically approves exact (revision_id, revision_hash)
        app_r = httpx.post(
            f"{br_url}/v2/plans/{plan_id}/approve",
            params={"project_id": pid},
            headers=admin_h,
            json={
                "revision_id": rev_id,
                "revision_hash": rev_hash,
                "actor": "lead-admin",
                "mode": "manual",
            },
        )
        assert app_r.status_code == 200, app_r.text

        # 7. HostRunner claims & executes all DAG tasks in dependency order against REAL Bridge!
        executed = 0
        for _ in range(5):
            res = runner.run_once()
            if not res["claimed"]:
                break
            assert res["status"] == "succeeded", res
            executed += 1
        assert executed == len(plan_req.tasks)

        # 8. Verify Dashboard Vietnamese proxy sees real measured executor tokens & unknown quota
        usage_r = httpx.get(f"{db_url}/api/v2/usage", params={"project_id": pid})
        assert usage_r.status_code == 200
        usage_data = usage_r.json()
        assert usage_data["executor"]["input_tokens"] == 320 * executed
        assert usage_data["executor"]["output_tokens"] == 110 * executed
        assert usage_data["executor"]["cache_read_tokens"] == 64 * executed
        assert usage_data["planner"]["input_tokens"] is None  # Tier 0 rule planner used 0 LLM

        brain_r = httpx.get(f"{db_url}/api/v2/brain/config", params={"project_id": pid})
        assert brain_r.status_code == 200
        assert brain_r.json()["approval_mode"] == "REVIEW_REQUIRED"

        # Verify plan reached COMPLETED state in Bridge
        final_plan = httpx.get(f"{br_url}/v2/plans/{plan_id}", params={"project_id": pid}, headers=read_h).json()
        assert final_plan["status"] == "COMPLETED"
    finally:
        db_srv.should_exit = True
        br_srv.should_exit = True
        db_t.join(timeout=3)
        br_t.join(timeout=3)
