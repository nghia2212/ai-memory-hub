"""Comprehensive V2 tests for Memory Bridge: schema v3, RBAC, catalog/routing, brain, plans/DAG, jobs, sessions, dedup usage, forecasts, quotas, restart reconciliation."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BRIDGE_ROOT = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from app.config import Settings
from app.main import create_app


def make_v2_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "bridge_v2.db",
        backup_dir=tmp_path / "backups",
        read_tokens={"tok-read"},
        write_tokens={"tok-write"},
        planner_tokens={"tok-planner"},
        runner_tokens={"tok-runner"},
        admin_tokens={"tok-admin"},
        worker_tokens={"tok-worker"},
    )


def h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    with TestClient(create_app(make_v2_settings(tmp_path))) as c:
        yield c


def _setup_project(client: TestClient, name: str = "v2-proj", identity: str = "git:v2") -> tuple[str, str]:
    p = client.post("/v1/projects", headers=h("tok-admin"), json={"name": name, "canonical_identity": identity})
    assert p.status_code == 201
    pid = p.json()["project_id"]
    w = client.post(
        f"/v1/projects/{pid}/worktrees",
        headers=h("tok-admin"),
        json={"canonical_root": f"D:\\repo\\{name}", "branch": "main", "head_commit": "3bea6cc"},
    )
    assert w.status_code == 201
    return pid, w.json()["worktree_id"]


def test_v2_catalog_routing_and_adaptive_brain_defaults(client: TestClient):
    assert client.get("/healthz").json()["schema_version"] == 4
    pid, wid = _setup_project(client)

    # Adaptive brain defaults reflect verified host CLI discovery (Neon/Hydrogen unavailable)
    bc = client.get(f"/v2/brain/config?project_id={pid}", headers=h("tok-read"))
    assert bc.status_code == 200
    roles = {r["role_name"]: r for r in bc.json()["roles"]}
    assert roles["tier2_primary"]["preferred_label"] == "Gemini 4 Neon Medium"
    assert roles["tier2_primary"]["availability_status"] == "unavailable"
    assert roles["tier0_rules"]["availability_status"] == "verified"

    # Refresh catalog with verified agy CLI model + api_key model
    cat = client.post(
        "/v2/catalog/refresh",
        headers=h("tok-admin"),
        json={
            "version": 1,
            "source_urls": ["https://docs.anthropic.com"],
            "models": [
                {
                    "provider": "agy",
                    "model_id": "claude-sonnet-5-5-medium",
                    "cli_family": "agy",
                    "cli_version": "1.1.27",
                    "auth_mode": "subscription_cli",
                    "status": "verified",
                    "context_limit": 200000,
                    "output_limit": 8192,
                },
                {
                    "provider": "deepseek",
                    "model_id": "deepseek-reasoner",
                    "auth_mode": "api_key",
                    "status": "verified",
                    "price_input_per_1m": 0.55,
                },
            ],
        },
    )
    assert cat.status_code == 201
    assert len(cat.json()["models"]) == 2

    # Routine task uses tier0 deterministic rules
    r_routine = client.post(
        "/v2/routing/recommend",
        headers=h("tok-planner"),
        json={"project_id": pid, "task_type": "routine", "complexity": "XS"},
    )
    assert r_routine.status_code == 200
    assert r_routine.json()["model_id"] == "deterministic-rules"

    # Implementation task selects verified subscription_cli model, never paid api_key
    r_impl = client.post(
        "/v2/routing/recommend",
        headers=h("tok-planner"),
        json={"project_id": pid, "task_type": "implementation", "complexity": "M"},
    )
    assert r_impl.status_code == 200
    assert r_impl.json()["model_id"] == "claude-sonnet-5-5-medium"
    assert r_impl.json()["auth_mode"] == "subscription_cli"


def test_v2_plans_dag_atomic_approval_and_runner_lifecycle(client: TestClient):
    pid, wid = _setup_project(client)

    # DAG cycle rejected with 422
    cyc = client.post(
        "/v2/plans",
        headers=h("tok-planner"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "objective": "Cycle test",
            "tasks": [
                {
                    "task_key": "A",
                    "title": "Task A",
                    "objective": "Do A",
                    "dependencies": ["B"],
                    "features": {"task_type": "implementation", "complexity": "S"},
                    "assigned_agent": "agy",
                    "assigned_provider": "agy",
                    "assigned_model_id": "claude-sonnet-5-5-medium",
                },
                {
                    "task_key": "B",
                    "title": "Task B",
                    "objective": "Do B",
                    "dependencies": ["A"],
                    "features": {"task_type": "testing", "complexity": "XS"},
                    "assigned_agent": "agy",
                    "assigned_provider": "agy",
                    "assigned_model_id": "gemini-3.8-flash-medium",
                },
            ],
        },
    )
    assert cyc.status_code == 422

    # Valid 2-task DAG (B depends on A) + persisted forecast
    tasks_spec = [
        {
            "task_key": "A",
            "title": "Implement bridge",
            "objective": "Code",
            "dependencies": [],
            "features": {"task_type": "implementation", "complexity": "M"},
            "assigned_agent": "agy",
            "assigned_provider": "agy",
            "assigned_model_id": "claude-sonnet-5-5-medium",
            "budget_input_tokens": 2000,
            "budget_output_tokens": 500,
            "budget_cost_usd": 0.05,
        },
        {
            "task_key": "B",
            "title": "Run tests",
            "objective": "Test",
            "dependencies": ["A"],
            "features": {"task_type": "testing", "complexity": "S"},
            "assigned_agent": "agy",
            "assigned_provider": "agy",
            "assigned_model_id": "gemini-3.8-flash-medium",
            "budget_input_tokens": 1000,
            "budget_output_tokens": 300,
        },
    ]
    fc = client.post(
        "/v2/forecasts",
        headers=h("tok-planner"),
        json={"project_id": pid, "worktree_id": wid, "tasks": tasks_spec, "persist": True},
    )
    assert fc.status_code == 200
    forecast_id = fc.json()["forecast_id"]
    assert fc.json()["confidence"] == "cold_start"

    plan = client.post(
        "/v2/plans",
        headers=h("tok-planner"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "objective": "Build feature X",
            "tasks": tasks_spec,
            "forecast_id": forecast_id,
        },
    )
    assert plan.status_code == 201
    plan_id = plan.json()["id"]
    rev1_id = plan.json()["current_revision_id"]
    rev1_hash = plan.json()["revisions"][0]["revision_hash"]

    # Planner cannot self-approve (403)
    assert (
        client.post(
            f"/v2/plans/{plan_id}/approve?project_id={pid}",
            headers=h("tok-planner"),
            json={"revision_id": rev1_id, "revision_hash": rev1_hash, "actor": "planner"},
        ).status_code
        == 403
    )

    # Runner cannot claim unapproved plan
    empty_claim = client.post(
        "/v2/jobs/claim",
        headers=h("tok-runner"),
        json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-1"},
    )
    assert empty_claim.status_code == 200
    assert empty_claim.json()["claimed"] is False

    # Tampered revision hash rejected with 409
    assert (
        client.post(
            f"/v2/plans/{plan_id}/approve?project_id={pid}",
            headers=h("tok-admin"),
            json={"revision_id": rev1_id, "revision_hash": "0" * 32, "actor": "admin"},
        ).status_code
        == 409
    )

    # Valid atomic approval
    app_res = client.post(
        f"/v2/plans/{plan_id}/approve?project_id={pid}",
        headers=h("tok-admin"),
        json={"revision_id": rev1_id, "revision_hash": rev1_hash, "actor": "admin"},
    )
    assert app_res.status_code == 200
    assert app_res.json()["status"] == "APPROVED"

    # Runner claims Task A (Task B blocked by dependency)
    c1 = client.post(
        "/v2/jobs/claim",
        headers=h("tok-runner"),
        json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-1"},
    )
    assert c1.status_code == 200
    assert c1.json()["claimed"] is True
    assert c1.json()["task_key"] == "A"
    job_a = c1.json()["job_id"]
    lease_a = c1.json()["lease_id"]
    sess_a = c1.json()["session_id"]

    # Second claim sees no ready tasks (B waits for A)
    c2 = client.post(
        "/v2/jobs/claim",
        headers=h("tok-runner"),
        json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-2"},
    )
    assert c2.json()["claimed"] is False

    # Heartbeat + usage telemetry (deduped cumulative + delta)
    hb = client.post(
        f"/v2/jobs/{job_a}/heartbeat",
        headers=h("tok-runner"),
        json={"project_id": pid, "lease_id": lease_a, "runner_id": "runner-1"},
    )
    assert hb.status_code == 200

    # Complete Task A -> unlocks Task B
    comp_a = client.post(
        f"/v2/jobs/{job_a}/complete",
        headers=h("tok-runner"),
        json={"project_id": pid, "lease_id": lease_a, "outcome": "succeeded", "accepted": True, "tests_passed": True},
    )
    assert comp_a.status_code == 200

    # V2.2: planner and runner traces are produced by real Bridge operations.
    traces = client.get("/v2/traces", params={"project_id": pid, "plan_id": plan_id}, headers=h("tok-read"))
    assert traces.status_code == 200, traces.text
    items = traces.json()["items"]
    planner = next(t for t in items if t["step_name"] == "validate_and_persist_plan")
    agent = next(t for t in items if t["session_id"] == sess_a)
    assert planner["status"] == "succeeded"
    assert planner["used_llm"] is False
    assert agent["parent_trace_id"] == planner["trace_id"]
    assert agent["root_trace_id"] == planner["trace_id"]
    assert agent["status"] == "succeeded"
    assert agent["used_llm"] is None
    assert agent["input_tokens"] is None
    assert agent["duration_ms"] >= 0
    assert "heartbeat" in agent["metadata_json"]["coverage"]


    # Claim & complete Task B
    c3 = client.post(
        "/v2/jobs/claim",
        headers=h("tok-runner"),
        json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-1"},
    )
    assert c3.json()["claimed"] is True
    assert c3.json()["task_key"] == "B"
    assert (
        client.post(
            f"/v2/jobs/{c3.json()['job_id']}/complete",
            headers=h("tok-runner"),
            json={"project_id": pid, "lease_id": c3.json()["lease_id"], "outcome": "succeeded", "accepted": True},
        ).status_code
        == 200
    )

    # Plan is now COMPLETED
    assert client.get(f"/v2/plans/{plan_id}?project_id={pid}", headers=h("tok-read")).json()["status"] == "COMPLETED"
    assert sess_a


def test_v2_usage_dedup_quotas_isolation_retention_and_restart(tmp_path: Path):
    settings = make_v2_settings(tmp_path)
    with TestClient(create_app(settings)) as c:
        p1, w1 = _setup_project(c, "p1", "git:p1")
        p2, w2 = _setup_project(c, "p2", "git:p2")

        # Create planner & executor sessions in p1
        s_plan = c.post(
            "/v2/sessions",
            headers=h("tok-planner"),
            json={
                "project_id": p1,
                "worktree_id": w1,
                "role": "planner",
                "provider": "agy",
                "model_id": "claude-sonnet-5-5-medium",
                "auth_mode": "subscription_cli",
                "status": "completed",
            },
        ).json()["id"]

        s_exec = c.post(
            "/v2/sessions",
            headers=h("tok-runner"),
            json={
                "project_id": p1,
                "worktree_id": w1,
                "role": "executor",
                "provider": "agy",
                "model_id": "claude-sonnet-5-5-medium",
                "auth_mode": "subscription_cli",
                "status": "running",
                "features": {"task_type": "implementation", "complexity": "M"},
            },
        ).json()["id"]

        # Record cumulative usage twice (seq 1 = 100, seq 2 = 250) + duplicate source_event_id
        for seq, eid, inp in [(1, "ev-1", 100), (2, "ev-2", 250), (2, "ev-2", 250)]:
            r = c.post(
                "/v2/usage",
                headers=h("tok-runner"),
                json={
                    "session_id": s_exec,
                    "project_id": p1,
                    "worktree_id": w1,
                    "source_event_id": eid,
                    "sequence_no": seq,
                    "usage": {
                        "input_tokens": inp,
                        "output_tokens": 50,
                        "measurement_kind": "measured",
                        "counter_mode": "cumulative",
                        "source": "agy-cli",
                    },
                },
            )
            assert r.status_code == 201

        # Record planner delta usage (cache_read_tokens stays None -> never 0)
        c.post(
            "/v2/usage",
            headers=h("tok-planner"),
            json={
                "session_id": s_plan,
                "project_id": p1,
                "worktree_id": w1,
                "source_event_id": "pl-1",
                "usage": {
                    "input_tokens": 40,
                    "output_tokens": 20,
                    "measurement_kind": "measured",
                    "counter_mode": "delta",
                    "source": "agy-cli",
                },
            },
        )

        # Record unknown quota (denominator None -> 'Không có dữ liệu')
        c.post(
            "/v2/quotas",
            headers=h("tok-runner"),
            json={
                "provider": "agy",
                "auth_mode": "subscription_cli",
                "quota_kind": "subscription_quota",
                "unit": "requests",
                "numerator": None,
                "denominator": None,
                "source": "agy-cli",
                "observed_at": "2026-11-25T10:20:00Z",
            },
        )

        u = c.get(f"/v2/usage?project_id={p1}", headers=h("tok-read")).json()
        # Cumulative snapshots are NOT summed (250, not 100+250+250)
        assert u["executor"]["input_tokens"] == 250
        assert u["executor"]["output_tokens"] == 50
        assert u["executor"]["cache_read_tokens"] is None
        assert u["planner"]["input_tokens"] == 40
        assert u["quotas"][0]["display_value"] == "Không có dữ liệu"

        # Cross-project isolation
        assert c.get(f"/v2/sessions/{s_exec}?project_id={p2}", headers=h("tok-read")).status_code == 404
        assert c.get(f"/v2/sessions?project_id={p2}", headers=h("tok-read")).json()["items"] == []

        # Policy AUTO_SCOPED with auto_persist_across_restart=False
        c.put(
            "/v2/policies",
            headers=h("tok-admin"),
            json={"project_id": p1, "approval_mode": "AUTO_SCOPED", "auto_persist_across_restart": False},
        )

    # Restart reconciliation: AUTO_SCOPED resets to REVIEW_REQUIRED & running session becomes partial
    with TestClient(create_app(settings)) as c2:
        pol = c2.get(f"/v2/policies?project_id={p1}", headers=h("tok-read")).json()
        assert pol["approval_mode"] == "REVIEW_REQUIRED"
        sess_after = c2.get(f"/v2/sessions/{s_exec}?project_id={p1}", headers=h("tok-read")).json()
        assert sess_after["status"] == "partial"

        # Retention delete removes session and derived usage
        del_res = c2.delete(f"/v2/sessions/{s_exec}?project_id={p1}", headers=h("tok-admin"))
        assert del_res.status_code == 200
        assert c2.get(f"/v2/sessions/{s_exec}?project_id={p1}", headers=h("tok-read")).status_code == 404
