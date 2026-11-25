"""V3.2 (SK32) P6A Mandatory Acceptance Tests: Task-Centric Execution Map (Zero Cross-Task Leakage, Descendants, Foreign ID Rejection)."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_plan_with_task(client, pid: str, wid: str, objective: str, task_key: str) -> tuple[str, str, str]:
    p = client.post(
        "/v2/plans",
        headers=h("tok-planner"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "objective": objective,
            "tasks": [
                {
                    "task_key": task_key,
                    "title": f"Task {task_key}",
                    "objective": objective,
                    "dependencies": [],
                    "features": {"task_type": "implementation", "complexity": "S"},
                    "assigned_agent": "claude",
                    "assigned_provider": "anthropic",
                    "assigned_model_id": "claude-sonnet",
                    "auth_mode": "subscription_cli",
                }
            ],
        },
    )
    assert p.status_code == 201, p.text
    pj = p.json()
    plan_id = pj["id"]
    task_id = pj["tasks"][0]["id"]
    # Approve plan and claim job so we get a real attempt_id!
    assert client.post(
        f"/v2/plans/{plan_id}/approve?project_id={pid}",
        headers=h("tok-admin"),
        json={"revision_id": pj["current_revision_id"], "revision_hash": pj["revisions"][0]["revision_hash"], "actor": "admin", "mode": "manual"},
    ).status_code == 200
    claim = client.post("/v2/jobs/claim", headers=h("tok-runner"), json={"runner_id": "r1", "project_id": pid, "worktree_id": wid})
    assert claim.status_code == 200, claim.text
    attempt_id = claim.json()["attempt_id"]
    return plan_id, task_id, attempt_id


def test_p6a_task_centric_isolation_descendants_and_foreign_scope_rejection(client):
    pid, wid = _setup_project(client)
    p2 = client.post("/v1/projects", headers=h("tok-admin"), json={"name": "ForeignProj", "canonical_identity": "git:foreign"}).json()
    foreign_pid = p2["project_id"]

    # Create two plans with distinct tasks and attempts in same project
    plan1, task1, att1 = _create_plan_with_task(client, pid, wid, "Plan One Objective", "T1")
    plan2, task2, att2 = _create_plan_with_task(client, pid, wid, "Plan Two Objective", "T2")

    # Post scoped trace for Plan 1 / Task 1 / Attempt 1 + explicit child trace
    t1_root = str(uuid.uuid4())
    t1_child = str(uuid.uuid4())
    t2_root = str(uuid.uuid4())
    t_legacy = str(uuid.uuid4())

    for payload in [
        {"trace_id": t1_root, "root_trace_id": t1_root, "project_id": pid, "worktree_id": wid, "plan_id": plan1, "task_id": task1, "attempt_id": att1, "step_type": "agent_step", "step_name": "P1-T1-Main", "actor_type": "agent", "actor_id": "a1", "status": "succeeded", "started_at": _now(), "ended_at": _now(), "metadata_json": {"source": "test"}},
        {"trace_id": t1_child, "parent_trace_id": t1_root, "root_trace_id": t1_root, "project_id": pid, "worktree_id": wid, "plan_id": plan1, "task_id": task1, "attempt_id": att1, "step_type": "mcp_tool_call", "step_name": "P1-T1-Child-MCP", "actor_type": "mcp", "actor_id": "bridge", "status": "succeeded", "started_at": _now(), "ended_at": _now(), "metadata_json": {"source": "test"}},
        {"trace_id": t2_root, "root_trace_id": t2_root, "project_id": pid, "worktree_id": wid, "plan_id": plan2, "task_id": task2, "attempt_id": att2, "step_type": "agent_step", "step_name": "P2-T2-Main", "actor_type": "agent", "actor_id": "a2", "status": "succeeded", "started_at": _now(), "ended_at": _now(), "metadata_json": {"source": "test"}},
        {"trace_id": t_legacy, "root_trace_id": t_legacy, "project_id": pid, "worktree_id": wid, "step_type": "local_rule", "step_name": "Legacy-Unlinked", "actor_type": "system", "actor_id": "legacy", "status": "succeeded", "started_at": _now(), "ended_at": _now(), "metadata_json": {"source": "test"}},
    ]:
        r = client.post("/v2/traces", headers=h("tok-write"), json=payload)
        assert r.status_code == 201, r.text

    # 1. Cascading scope options returns both plans, tasks, attempts
    opts = client.get(f"/v2/traces/scope-options?project_id={pid}", headers=h("tok-read")).json()
    assert len(opts["plans"]) == 2
    assert len(opts["tasks"]) == 2
    assert len(opts["attempts"]) == 2

    # 2. Selecting Plan 1 / Task 1 / Attempt 1 has ZERO cross-task leakage from Plan 2 or Legacy!
    r1 = client.get(f"/v2/traces?project_id={pid}&plan_id={plan1}&task_id={task1}&attempt_id={att1}", headers=h("tok-read")).json()
    names1 = {it["step_name"] for it in r1["items"]}
    assert "P1-T1-Main" in names1 and "P1-T1-Child-MCP" in names1
    assert "P2-T2-Main" not in names1 and "Legacy-Unlinked" not in names1

    # Export respects identical task scope (JSON & CSV)
    exp_json = client.get(f"/v2/traces/export?project_id={pid}&plan_id={plan1}&task_id={task1}&attempt_id={att1}&format=json", headers=h("tok-read")).json()
    assert all(it["task_id"] == task1 and it["attempt_id"] == att1 for it in exp_json["items"])
    exp_csv = client.get(f"/v2/traces/export?project_id={pid}&plan_id={plan1}&task_id={task1}&attempt_id={att1}&format=csv", headers=h("tok-read"))
    assert exp_csv.status_code == 200 and "P1-T1-Main" in exp_csv.text and "P2-T2-Main" not in exp_csv.text

    # 3. Project view labels legacy unlinked trace honestly & tracks unlinked_project_traces
    proj_view = client.get(f"/v2/traces?project_id={pid}", headers=h("tok-read")).json()
    leg_item = next(it for it in proj_view["items"] if it["trace_id"] == t_legacy)
    assert leg_item["task_link_label"] == "Chưa liên kết nhiệm vụ"
    assert proj_view["unlinked_project_traces"] >= 1

    # 4. Reject foreign project / mismatched plan-task / mismatched task-attempt IDs
    assert client.get(f"/v2/traces?project_id={foreign_pid}&plan_id={plan1}", headers=h("tok-read")).status_code == 404
    assert client.get(f"/v2/traces?project_id={pid}&plan_id={plan1}&task_id={task2}", headers=h("tok-read")).status_code == 422
    assert client.get(f"/v2/traces?project_id={pid}&task_id={task1}&attempt_id={att2}", headers=h("tok-read")).status_code == 422
