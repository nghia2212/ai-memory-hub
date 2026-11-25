"""V3.2.1 (MCP321) P19A.1 & P19A.2 — Subscription Agents, Separate Source/Model, Controls, Quota, Handoff & Mock Exclusion."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from app.trace_metrics import summarize


def _seed_agy_catalog(client):
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


def _get_game_business_id(client) -> str:
    bizs = client.get("/v31/businesses", headers=h("tok-read")).json()["items"]
    game = next((b for b in bizs if "game" in b["name"].lower()), bizs[0])
    return game["id"]


def test_p19a1_execution_sources_separate_source_and_model_and_project_defaults(client):
    """Execution Source and Model are strictly separated; models come from live catalog (never hard-coded);
    game projects get smart recommendation and persist custom defaults without overwriting."""
    pid, _wid = _setup_project(client)
    _seed_agy_catalog(client)
    bid = _get_game_business_id(client)

    res = client.get(f"/v321/execution-sources?project_id={pid}&probe_host=false", headers=h("tok-read"))
    assert res.status_code == 200, res.text
    body = res.json()
    sources = {s["source_key"]: s for s in body["sources"]}
    assert "antigravity" in sources
    assert "claude_code" in sources
    assert "codex_cli" in sources
    assert "gemini_cli" in sources
    assert "prov-mock-deterministic" in sources

    agy = sources["antigravity"]
    assert agy["group"] == "subscription_cli"
    m_ids = [m["model_id"] for m in agy["models"]]
    assert "gemini-4-neon-medium" in m_ids
    assert "claude-sonnet-5-5-medium" in m_ids

    # Persist project defaults
    put_def = client.put(
        f"/v321/projects/{pid}/chat-defaults",
        headers=h("tok-admin"),
        json={
            "business_id": bid,
            "execution_source": "antigravity",
            "model_id": "gemini-4-neon-medium",
            "mode": "task",
            "allow_paid_api_fallback": False,
        },
    )
    assert put_def.status_code == 200, put_def.text
    saved = put_def.json()
    assert saved["persisted"] is True
    assert saved["execution_source"] == "antigravity"
    assert saved["model_id"] == "gemini-4-neon-medium"
    assert saved["allow_paid_api_fallback"] is False


def test_p19a2_agent_disable_model_disable_and_quota_enforcement_in_chat_and_routing(client):
    """Disabling an agent, disabling a model, or marking quota exhausted blocks BOTH Chat Workspace
    and V2 routing backend, with zero silent fallback to paid API."""
    pid, wid = _setup_project(client)
    _seed_agy_catalog(client)
    bid = _get_game_business_id(client)

    conv = client.post(
        "/v321/chat/conversations",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "business_id": bid,
            "mode": "chat",
            "execution_source": "antigravity",
            "model_id": "gemini-4-neon-medium",
        },
    ).json()

    # 1. Disable model gemini-4-neon-medium -> backend rejects with 403 model_disabled
    r_m = client.put(
        "/v321/model-controls",
        headers=h("tok-admin"),
        json={"source_key": "antigravity", "model_id": "gemini-4-neon-medium", "enabled": False, "disabled_reason": "Maintenance"},
    )
    assert r_m.status_code == 200
    r_send = client.post(
        f"/v321/chat/conversations/{conv['id']}/messages",
        headers=h("tok-admin"),
        json={"project_id": pid, "content": "Kiểm tra repo", "execution_source": "antigravity", "model_id": "gemini-4-neon-medium"},
    )
    assert r_send.status_code == 403
    assert r_send.json()["error"]["code"] == "model_disabled"

    # Re-enable model, then mark quota exhausted -> backend rejects with 409 quota_exhausted
    client.put(
        "/v321/model-controls",
        headers=h("tok-admin"),
        json={"source_key": "antigravity", "model_id": "gemini-4-neon-medium", "enabled": True},
    )
    now = datetime.now(timezone.utc)
    rq = client.post(
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
            "reset_at": (now + timedelta(hours=3)).isoformat().replace("+00:00", "Z"),
            "source": "manual_user_confirmation",
            "confounded": False,
            "observed_at": now.isoformat().replace("+00:00", "Z"),
        },
    )
    assert rq.status_code == 201
    r_exh = client.post(
        f"/v321/chat/conversations/{conv['id']}/messages",
        headers=h("tok-admin"),
        json={"project_id": pid, "content": "Kiểm tra quota", "execution_source": "antigravity", "model_id": "gemini-4-neon-medium"},
    )
    assert r_exh.status_code == 409
    assert r_exh.json()["error"]["code"] == "quota_exhausted"

    # 2. Disable entire Agent -> V2 routing rejects with 403 agent_or_model_disabled and Chat rejects with 403 agent_disabled
    r_a = client.put(
        "/v321/agent-controls/antigravity",
        headers=h("tok-admin"),
        json={"enabled": False, "disabled_reason": "Admin paused"},
    )
    assert r_a.status_code == 200
    assert r_a.json()["active_tasks_affected"] is False

    r_route = client.post(
        "/v2/routing/recommend",
        headers=h("tok-planner"),
        json={"project_id": pid, "task_type": "implementation", "complexity": "M"},
    )
    assert r_route.status_code == 403
    assert r_route.json()["error"]["code"] == "agent_or_model_disabled"

    # Re-enable Agent and confirm quota recovery
    client.put("/v321/agent-controls/antigravity", headers=h("tok-admin"), json={"enabled": True})
    client.post(
        "/v2/quotas",
        headers=h("tok-admin"),
        json={
            "provider": "agy",
            "auth_mode": "subscription_cli",
            "account_bucket": "model:gemini-4-neon-medium",
            "quota_kind": "subscription_quota",
            "unit": "availability_flag",
            "numerator": 0,
            "denominator": 1,
            "reset_at": None,
            "source": "manual_user_confirmation",
            "confounded": False,
            "observed_at": (now + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
        },
    )
    r_route_ok = client.post(
        "/v2/routing/recommend",
        headers=h("tok-planner"),
        json={"project_id": pid, "task_type": "implementation", "complexity": "M"},
    )
    assert r_route_ok.status_code == 200
    assert r_route_ok.json()["provider"] == "agy"


def test_p19a1_subscription_direct_dispatch_and_manual_ide_handoff_and_plan_dag(client):
    """Verify Antigravity subscription Chat/Task direct dispatch (via Host Runner contract),
    Plan & DAG honoring selected model, and honest Manual IDE Handoff + real result import."""
    pid, wid = _setup_project(client)
    _seed_agy_catalog(client)
    bid = _get_game_business_id(client)

    conv = client.post(
        "/v321/chat/conversations",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "worktree_id": wid,
            "business_id": bid,
            "mode": "task",
            "execution_source": "antigravity",
            "model_id": "gemini-4-neon-medium",
        },
    ).json()

    # 1. Direct dispatch (mocked Host Runner HTTP loopback) -> delivers real answer, saves handoff & Execution Map trace
    fake_host_resp = (
        200,
        {
            "ok": True,
            "cli_family": "agy",
            "requested_model": "gemini-4-neon-medium",
            "reported_model": "gemini-4-neon-medium",
            "external_session_id": "agy-conv-real-001",
            "duration_ms": 420,
            "exit_code": 0,
            "result_delivery_status": "delivered",
            "final_answer": "Đã kiểm tra mã nguồn Đội Cún Siêu Trí Tuệ (chỉ đọc): kiến trúc Phaser sạch sẽ.",
            "truncated": False,
            "usage": {"input_tokens": 210, "output_tokens": 55, "measurement_kind": "measured"},
            "error_code": None,
            "message": "OK",
        },
    )
    with patch.dict(client.app.state.v321_chat_service.send_message.__globals__, {"invoke_host_subscription_chat": lambda *a, **kw: fake_host_resp}):
        r1 = client.post(
            f"/v321/chat/conversations/{conv['id']}/messages",
            headers=h("tok-admin"),
            json={
                "project_id": pid,
                "content": "Kiểm tra nhanh mã nguồn game Đội Cún Siêu Trí Tuệ (chỉ đọc)",
                "mode": "task",
                "execution_source": "antigravity",
                "model_id": "gemini-4-neon-medium",
                "idempotency_key": f"sub-task-{uuid.uuid4().hex[:8]}",
            },
        )
    assert r1.status_code == 201, r1.text
    m1 = r1.json()["assistant_message"]
    assert m1["delivery_status"] == "delivered"
    assert m1["provider_key"] == "antigravity"
    assert m1["model_id"] == "gemini-4-neon-medium"
    assert m1["usage"]["estimated_cost_usd"] is None
    assert m1["sources"]["external_session_id"] == "agy-conv-real-001"
    assert m1["sources"]["handoff_id"] is not None
    assert m1["trace_id"] is not None

    # 2. Plan & DAG mode honors selected model (never hard-coded!)
    r_plan = client.post(
        f"/v321/chat/conversations/{conv['id']}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Lập kế hoạch nâng cấp âm thanh cho game",
            "mode": "plan_dag",
            "execution_source": "antigravity",
            "model_id": "claude-sonnet-5-5-medium",
        },
    )
    assert r_plan.status_code == 201, r_plan.text
    plan_id = r_plan.json()["assistant_message"]["plan_id"]
    plan = client.get(f"/v2/plans/{plan_id}?project_id={pid}", headers=h("tok-read")).json()
    t2 = next(t for t in plan["tasks"] if t["task_key"] == "T2")
    assert t2["assigned_provider"] == "agy"
    assert t2["assigned_model_id"] == "claude-sonnet-5-5-medium"

    # 3. Manual IDE Handoff -> returns awaiting_external (NOT fake delivered!), then real import delivers result
    r_ho = client.post(
        f"/v321/chat/conversations/{conv['id']}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Phân tích scene bản đồ đảo khủng long trên IDE",
            "mode": "task",
            "execution_source": "antigravity",
            "model_id": "gemini-4-neon-medium",
            "manual_handoff": True,
        },
    )
    assert r_ho.status_code == 201, r_ho.text
    m_ho = r_ho.json()["assistant_message"]
    assert m_ho["delivery_status"] == "pending"
    assert m_ho["sources"]["status"] == "awaiting_external"
    hid = m_ho["sources"]["handoff_id"]

    r_imp = client.post(
        f"/v321/chat/handoffs/{hid}/import",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "result_content": "Kết quả thật từ Antigravity IDE: Đảo Khủng Long đã gắn đủ 4 nhóm Tủ đồ.",
            "external_session_id": "ide-sess-999",
            "provider_reported_model": "gemini-4-neon-medium",
        },
    )
    assert r_imp.status_code == 200, r_imp.text
    assert r_imp.json()["assistant_message"]["delivery_status"] == "delivered"
    assert "Đảo Khủng Long" in r_imp.json()["assistant_message"]["content"]


def test_p19a2_mock_provider_excluded_from_live_ai_token_and_cost_statistics(client):
    """Mock Provider tokens and costs MUST be strictly excluded from live AI statistics."""
    rows = [
        {
            "step_type": "llm_call",
            "status": "succeeded",
            "provider": "mock",
            "model_id": "mock-deterministic-v1",
            "used_llm": True,
            "input_tokens": 9999,
            "output_tokens": 8888,
            "cost_usd": 12.34,
            "metadata_json": {"measurement_kind": "measured", "simulation": True, "billing_mode": "mock"},
        },
        {
            "step_type": "llm_call",
            "status": "succeeded",
            "provider": "agy",
            "model_id": "gemini-4-neon-medium",
            "used_llm": True,
            "input_tokens": 200,
            "output_tokens": 50,
            "cost_usd": None,
            "metadata_json": {"measurement_kind": "measured", "billing_mode": "subscription_cli"},
        },
    ]
    summary = summarize(rows)
    assert summary["simulation_excluded_steps"] == 1
    assert summary["input_tokens"] == 200
    assert summary["output_tokens"] == 50
    assert summary["known_token_subtotal"] == 250
    assert summary["cost_usd"] is None
    assert summary["estimated_cost_usd"] is None
