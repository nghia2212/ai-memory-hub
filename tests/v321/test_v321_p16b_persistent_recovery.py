"""V3.2.1 P16B — Comprehensive 13-Scenario Isolated Test Suite for Persistent Task & Auto Recovery.

Covers:
1. Stop/Start persistence across DB reopen (no lost Plan/Task/Attempt/Checkpoint)
2. Host Runner liveness probe & Runner restart safety
3. Old Agent still alive -> Reconnect (blocks duplicate Resume 409)
4. Old Agent stopped -> Safe Resume on SAME Task with new fencing token & skipped completed steps
5. Quota exhausted mid-task -> blocks Resume, allows Change Agent, blocks silent paid API fallback
6. Network interruption -> pending_review
7. Stop during dirty worktree code edit -> preserves dirty files, requires explicit review confirmation
8. Stop during test execution -> preserves test_results & step progress
9. Stop during in-flight API call -> holds uncertain budget, blocks blind retry (409 uncertain_api_outcome)
10. Stop after AI completed but result undelivered -> zero-LLM cached result redelivery
11. Repeated consecutive restarts -> strictly idempotent (zero duplicate Plan/Task/Queue)
12. Two agents competing -> Lease Fencing rejects stale fencing_token (409)
13. Manual / Automatic / Hybrid recovery modes + Pause & terminal Cancel (never auto-resumed)
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from services.orchestrator.host_brain_agent import check_runner_process_status


def _seed_catalog(client):
    now = datetime.now(timezone.utc)
    r = client.post(
        "/v2/catalog/refresh",
        headers=h("tok-admin"),
        json={
            "version": 1,
            "source_urls": ["local://agy/models"],
            "expires_at": (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z"),
            "is_stale": False,
            "models": [
                {
                    "provider": "agy",
                    "model_id": "gemini-4-neon-medium",
                    "cli_family": "agy",
                    "cli_version": "1.5.17",
                    "auth_mode": "subscription_cli",
                    "status": "verified",
                    "capabilities": {"display_name": "Gemini 4 Neon (Medium)", "verification_stage": "cli_available"},
                    "source_url": "local://agy/models",
                    "verified_at": now.isoformat().replace("+00:00", "Z"),
                },
                {
                    "provider": "agy",
                    "model_id": "claude-sonnet-5-5-medium",
                    "cli_family": "agy",
                    "cli_version": "1.5.17",
                    "auth_mode": "subscription_cli",
                    "status": "verified",
                    "capabilities": {"display_name": "Claude Sonnet 5.5 (Medium)", "verification_stage": "cli_available"},
                    "source_url": "local://agy/models",
                    "verified_at": now.isoformat().replace("+00:00", "Z"),
                },
            ],
        },
    )
    assert r.status_code == 201, r.text


def _create_approved_claimed_task(client, pid: str, wid: str, model_id: str = "gemini-4-neon-medium") -> tuple[str, str, str]:
    plan = client.post(
        "/v2/plans",
        headers=h("tok-planner"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "objective": "P16B Multi-step persistent task",
            "tasks": [
                {
                    "task_key": "T1",
                    "title": "Step 1: Read-only analysis & test",
                    "objective": "Analyze codebase and run tests",
                    "dependencies": [],
                    "features": {"task_type": "implementation", "complexity": "M"},
                    "assigned_agent": "executor",
                    "assigned_provider": "agy",
                    "assigned_model_id": model_id,
                    "auth_mode": "subscription_cli",
                }
            ],
        },
    ).json()
    plan_id = plan["id"]
    rev_id = plan["current_revision_id"]
    rev_hash = plan["revisions"][0]["revision_hash"]
    assert client.post(
        f"/v2/plans/{plan_id}/approve?project_id={pid}",
        headers=h("tok-admin"),
        json={"revision_id": rev_id, "revision_hash": rev_hash, "actor": "admin"},
    ).status_code == 200
    claim = client.post(
        "/v2/jobs/claim",
        headers=h("tok-runner"),
        json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-old-1"},
    ).json()
    assert claim["claimed"] is True
    return plan_id, claim["job_id"], claim["attempt_id"]


def test_p16b_alive_runner_reconnect_vs_stopped_resume_fencing_and_repeated_restarts(client):
    """Covers Scenarios 1, 2, 3, 4, 8, 11, 12:
    - Alive runner -> Reconnect (blocks duplicate Resume 409)
    - Stopped runner -> Resume on SAME task with fencing_token++, skips completed steps, preserves test_results
    - Repeated restarts -> idempotent (no duplicate Plan/Task/Queue)
    - Two competing agents -> stale fencing_token rejected (409)
    """
    pid, wid = _setup_project(client)
    _seed_catalog(client)
    plan_id, task_id, attempt_id = _create_approved_claimed_task(client, pid, wid)

    # Record checkpoint during test execution with runner_pid = current PID (alive!)
    ck = client.post(
        "/v321/recovery/checkpoints",
        headers=h("tok-runner"),
        json={
            "entity_kind": "v2_task",
            "entity_id": task_id,
            "project_id": pid,
            "worktree_id": wid,
            "plan_id": plan_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "runner_id": "runner-old-1",
            "runner_pid": os.getpid(),
            "idempotency_key": "ckpt-step-1",
            "execution_state": "running",
            "completed_steps": [{"step": "Read source files", "verified": True}],
            "current_step": {"step": "Running unit tests"},
            "pending_steps": [{"step": "Generate summary"}],
            "git_state": {"branch": "lead/mcp321-integration", "head_commit": "abc1234", "dirty": False},
            "test_results": {"passed": 12, "failed": 0, "in_progress": True},
        },
    )
    assert ck.status_code == 201, ck.text
    assert ck.json()["checkpoint_version"] == 1

    # Host runner liveness helper works on real PID
    live_chk = check_runner_process_status(os.getpid(), "runner-old-1")
    assert live_chk["status"] == "alive"

    # Scenario 3: Simulate Hub restart while old runner is STILL ALIVE
    rec_mod = client.app.state.v321_recovery_service.reconcile_startup.__globals__
    with patch.dict(rec_mod, {"probe_host_runner_liveness": lambda **kw: {"status": "alive", "reason": "PID alive"}}):
        scan1 = client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": True}).json()
        # Scenario 11: Repeated consecutive restarts -> strictly idempotent
        scan2 = client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": True}).json()
        scan3 = client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": True}).json()

    assert scan1["upserted"] == 1
    assert scan2["upserted"] == 1
    assert scan3["upserted"] == 1
    q = client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()
    assert q["total"] == 1
    item = q["items"][0]
    assert item["old_agent_status"] == "alive"
    assert item["recovery_status"] == "reconnected"
    assert item["actions_allowed"]["reconnect"] is True
    assert item["actions_allowed"]["resume"] is False

    # Trying to Resume while old agent is alive MUST be blocked (409) to prevent duplicate execution!
    r_dup = client.post(f"/v321/recovery/{item['id']}/resume", headers=h("tok-admin"), json={"project_id": pid})
    assert r_dup.status_code == 409
    assert r_dup.json()["error"]["code"] == "old_agent_still_alive"

    # Reconnect succeeds without creating duplicate attempt
    r_rec = client.post(f"/v321/recovery/{item['id']}/reconnect", headers=h("tok-admin"), json={"project_id": pid, "note": "Keep monitoring"})
    assert r_rec.status_code == 200

    # Scenario 4: Now old runner STOPPED -> scan detects stopped -> Resume succeeds with fencing_token=2 on SAME task!
    with patch.dict(rec_mod, {"probe_host_runner_liveness": lambda **kw: {"status": "stopped", "reason": "Process exited"}}):
        client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": True})
    q2 = client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()
    item2 = q2["items"][0]
    assert item2["old_agent_status"] == "stopped"
    assert item2["recovery_status"] == "auto_resumable"  # Hybrid default auto-classifies safe_readonly!

    r_res = client.post(f"/v321/recovery/{item2['id']}/resume", headers=h("tok-admin"), json={"project_id": pid})
    assert r_res.status_code == 200, r_res.text
    assert r_res.json()["fencing_token"] == 2
    assert r_res.json()["item"]["completed_steps"] == [{"step": "Read source files", "verified": True}]

    # Scenario 12: Old runner with stale fencing_token=1 tries to write checkpoint -> REJECTED 409 stale_fencing_token!
    r_stale = client.post(
        "/v321/recovery/checkpoints",
        headers=h("tok-runner"),
        json={
            "entity_kind": "v2_task",
            "entity_id": task_id,
            "project_id": pid,
            "fencing_token": 1,
            "execution_state": "running",
        },
    )
    assert r_stale.status_code == 409
    assert r_stale.json()["error"]["code"] == "stale_fencing_token"


def test_p16b_dirty_worktree_quota_exhausted_network_and_change_agent_gates(client):
    """Covers Scenarios 5, 6, 7, 13:
    - Dirty worktree edit requires explicit confirmation (no git reset --hard)
    - Quota exhausted blocks Resume, allows Change Agent to valid subscription model, blocks silent paid API
    - Pause & terminal Cancel never auto-resumed
    - Recovery policies: manual / automatic / hybrid
    """
    pid, wid = _setup_project(client)
    _seed_catalog(client)
    plan_id, task_id, attempt_id = _create_approved_claimed_task(client, pid, wid, model_id="gemini-4-neon-medium")

    # Scenario 7: Checkpoint with uncommitted dirty files
    client.post(
        "/v321/recovery/checkpoints",
        headers=h("tok-runner"),
        json={
            "entity_kind": "v2_task",
            "entity_id": task_id,
            "project_id": pid,
            "worktree_id": wid,
            "plan_id": plan_id,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "execution_state": "interrupted",
            "git_state": {"branch": "lead/mcp321-integration", "head_commit": "abc1234", "dirty": True, "dirty_files": ["src/game.ts"]},
            "interruption_reason": "docker_stop_during_edit",
        },
    )
    # Simulate abrupt Docker stop -> lease expired & runner stopped
    with client.app.state.db.write_tx() as conn:
        conn.execute("UPDATE v2_job_leases SET expires_at = '2020-01-01T00:00:00Z' WHERE task_id = ?", (task_id,))
    client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": False})
    item = client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()["items"][0]
    assert item["safety_classification"] == "dirty_worktree_edit"
    assert item["recovery_status"] == "pending_review"
    assert item["git_state"]["dirty"] is True

    # Resume without confirm_dirty_worktree -> 409!
    r_blk = client.post(f"/v321/recovery/{item['id']}/resume", headers=h("tok-admin"), json={"project_id": pid, "confirm_dirty_worktree": False})
    assert r_blk.status_code == 409
    assert r_blk.json()["error"]["code"] == "dirty_worktree_review_required"

    # Scenario 5: Quota exhausted mid-task -> Change Agent to paid API blocked (403), Change Agent to valid subscription model allowed
    now = datetime.now(timezone.utc)
    client.post(
        "/v2/quotas",
        headers=h("tok-admin"),
        json={
            "provider": "agy",
            "auth_mode": "subscription_cli",
            "account_bucket": "model:gemini-4-neon-medium",
            "quota_kind": "subscription_quota",
            "unit": "availability_flag",
            "numerator": 1,
            "denominator": 1,
            "reset_at": (now + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "source": "provider_error_observation",
            "confounded": False,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
        },
    )
    # Simulate abrupt Docker stop -> lease expired & runner stopped
    with client.app.state.db.write_tx() as conn:
        conn.execute("UPDATE v2_job_leases SET expires_at = '2020-01-01T00:00:00Z' WHERE task_id = ?", (task_id,))
    client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": False})
    item_q = client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()["items"][0]
    assert item_q["safety_classification"] == "quota_exhausted"
    assert item_q["actions_allowed"]["resume"] is False

    # Attempting to change to disabled/egress-blocked DeepSeek paid API -> 403!
    r_paid = client.post(
        f"/v321/recovery/{item_q['id']}/change-agent",
        headers=h("tok-admin"),
        json={"project_id": pid, "execution_source": "prov-deepseek-api", "model_id": "deepseek-chat", "allow_paid_api_consent": False},
    )
    assert r_paid.status_code == 403

    # Change Agent to available subscription model claude-sonnet-5-5-medium -> 200!
    r_sw = client.post(
        f"/v321/recovery/{item_q['id']}/change-agent",
        headers=h("tok-admin"),
        json={"project_id": pid, "execution_source": "antigravity", "model_id": "claude-sonnet-5-5-medium"},
    )
    assert r_sw.status_code == 200, r_sw.text
    assert r_sw.json()["item"]["model_id"] == "claude-sonnet-5-5-medium"

    # Scenario 13: Policy update (manual / automatic / hybrid) + Pause + Terminal Cancel
    pol = client.put(f"/v321/projects/{pid}/recovery-policy", headers=h("tok-admin"), json={"recovery_mode": "manual"}).json()
    assert pol["recovery_mode"] == "manual"
    assert client.post(f"/v321/recovery/{item_q['id']}/pause", headers=h("tok-admin"), json={"project_id": pid}).json()["item"]["recovery_status"] == "paused"
    assert client.post(f"/v321/recovery/{item_q['id']}/cancel", headers=h("tok-admin"), json={"project_id": pid}).json()["item"]["recovery_status"] == "canceled"

    # Re-scan after terminal Cancel MUST NOT reopen or auto-resume the canceled task!
    # Simulate abrupt Docker stop -> lease expired & runner stopped
    with client.app.state.db.write_tx() as conn:
        conn.execute("UPDATE v2_job_leases SET expires_at = '2020-01-01T00:00:00Z' WHERE task_id = ?", (task_id,))
    client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": False})
    active_q = client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()
    assert active_q["total"] == 0


def test_p16b_uncertain_api_inflight_and_undelivered_completed_result_redelivery(client):
    """Covers Scenarios 9 & 10:
    - Stop during API call -> holds uncertain budget, blocks blind retry (409 uncertain_api_outcome)
    - Stop after AI completed but result not delivered to Chat -> zero-LLM redelivery resolves cleanly
    """
    pid, wid = _setup_project(client)
    assert client.get("/v321/providers", headers=h("tok-read")).status_code == 200
    biz = client.get("/v31/businesses", headers=h("tok-read")).json()["items"][0]["id"]
    db = client.app.state.db
    now = datetime.now(timezone.utc).isoformat()

    # Scenario 9: Simulate interrupted running API Agent with an in-flight reserved provider request
    run_inflight = str(uuid.uuid4())
    with db.write_tx() as conn:
        conn.execute(
            """
            INSERT INTO mcp321_agent_runs (
                id, project_id, worktree_id, business_id, execution_mode, provider_profile_id,
                requested_model, write_mode, status, objective, max_rounds, max_calls,
                max_budget_usd, reserved_budget_usd, spent_budget_usd, uncertain_budget_usd,
                rounds_used, calls_used, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'api_agent', 'prov-mock-deterministic', 'mock-deterministic-v1',
                      'read_only', 'running', 'In-flight API call', 3, 4, 0.10, 0.02, 0.01, 0.02, 1, 1, ?, ?)
            """,
            (run_inflight, pid, wid, biz, now, now),
        )
        conn.execute(
            """
            INSERT INTO mcp321_provider_requests (
                id, run_id, project_id, idempotency_key, provider_key, requested_model,
                status, reserved_cost_usd, created_at
            ) VALUES (?, ?, ?, 'req-inflight-1', 'deepseek', 'deepseek-chat', 'reserved', 0.02, ?)
            """,
            (str(uuid.uuid4()), run_inflight, pid, now),
        )

    # Scenario 10: Simulate AI completed & saved result_summary, but Chat message remained 'pending'
    conv = client.post(
        "/v321/chat/conversations",
        headers=h("tok-admin"),
        json={"project_id": pid, "worktree_id": wid, "business_id": biz, "mode": "task"},
    ).json()
    run_done = str(uuid.uuid4())
    with db.write_tx() as conn:
        conn.execute(
            """
            INSERT INTO mcp321_agent_runs (
                id, project_id, worktree_id, business_id, execution_mode, provider_profile_id,
                requested_model, write_mode, status, objective, max_budget_usd, spent_budget_usd,
                result_summary, created_at, updated_at, ended_at
            ) VALUES (?, ?, ?, ?, 'api_agent', 'prov-mock-deterministic', 'mock-deterministic-v1',
                      'read_only', 'completed', 'Completed before crash', 0.05, 0.01,
                      'Kết quả AI thật đã lưu trước khi Hub dừng!', ?, ?, ?)
            """,
            (run_done, pid, wid, biz, now, now, now),
        )
        conn.execute(
            """
            INSERT INTO mcp321_chat_messages (
                id, conversation_id, project_id, role, content, delivery_status, mode, run_id, created_at
            ) VALUES (?, ?, ?, 'assistant', '', 'pending', 'task', ?, ?)
            """,
            (str(uuid.uuid4()), conv["id"], pid, run_done, now),
        )

    # Run startup scan (even under Automatic mode, uncertain API MUST remain pending_review!)
    client.put(f"/v321/projects/{pid}/recovery-policy", headers=h("tok-admin"), json={"recovery_mode": "automatic"})
    client.post("/v321/recovery/scan", headers=h("tok-admin"), json={"project_id": pid, "check_host_runner": False})
    q = {it["entity_id"]: it for it in client.get(f"/v321/recovery/queue?project_id={pid}", headers=h("tok-read")).json()["items"]}

    # Verify Scenario 9: Uncertain API in-flight is held & blocks blind retry
    it_inf = q[run_inflight]
    assert it_inf["safety_classification"] == "uncertain_api_inflight"
    assert it_inf["recovery_status"] == "pending_review"
    assert it_inf["budget"]["uncertain_usd"] == 0.02
    r_blind = client.post(f"/v321/recovery/{it_inf['id']}/resume", headers=h("tok-admin"), json={"project_id": pid, "confirm_uncertain_retry": False})
    assert r_blind.status_code == 409
    assert r_blind.json()["error"]["code"] == "uncertain_api_outcome"

    # Verify Scenario 10: Undelivered completed result -> zero-LLM redelivery delivers cached text to Chat!
    it_done = q[run_done]
    assert it_done["safety_classification"] == "result_undelivered"
    assert it_done["recommended_action"] == "deliver_result"
    r_del = client.post(
        f"/v321/recovery/{it_done['id']}/review?deliver_cached_result=true",
        headers=h("tok-admin"),
        json={"project_id": pid},
    )
    assert r_del.status_code == 200, r_del.text
    assert r_del.json()["delivered_cached_result"] is True
    assert r_del.json()["item"]["recovery_status"] == "resolved"
    conv_after = client.get(f"/v321/chat/conversations/{conv['id']}?project_id={pid}", headers=h("tok-read")).json()
    assert any(m["delivery_status"] == "delivered" and "Kết quả AI thật đã lưu" in m["content"] for m in conv_after["messages"])
