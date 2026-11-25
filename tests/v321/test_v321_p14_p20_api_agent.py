"""V3.2.1 (MCP321) P14-P20 Comprehensive Acceptance Tests:
AI Provider Gateway, Secret/Egress Security, Hub-managed API Agent, MCP/Memory/Skill Integration,
Atomic Budget Controller, Usage Honesty & Hybrid Plan Stages (0 paid network calls).
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def test_p14_p15_provider_security_masking_ssrf_and_egress_fail_closed(client):
    pid, wid = _setup_project(client)

    # 1. Seeded 6 providers (mock enabled; external paid disabled by default)
    provs = client.get("/v321/providers", headers=h("tok-read")).json()
    pkeys = {p["provider_key"] for p in provs["items"]}
    assert {"deepseek", "gemini", "qwen_local", "openai", "anthropic", "mock"} == pkeys
    assert "budget_disclosure" in provs
    for p in provs["items"]:
        if p["provider_key"] != "mock":
            assert p["enabled"] is False
        assert "sk-" not in str(p)

    # 2. Reject plaintext API key in credential_env_ref (422 raw_api_key_forbidden)
    r_raw = client.post(
        "/v321/providers",
        headers=h("tok-admin"),
        json={
            "provider_key": "deepseek",
            "display_name": "Bad Raw Key Provider",
            "billing_mode": "metered_api",
            "credential_env_ref": "sk-1234567890abcdef1234567890",
            "selected_model": "deepseek-chat",
        },
    )
    assert r_raw.status_code == 422
    assert r_raw.json()["error"]["code"] == "raw_api_key_forbidden"

    # 3. Reject SSRF / non-allowlisted endpoint
    r_ssrf = client.post(
        "/v321/providers",
        headers=h("tok-admin"),
        json={
            "provider_key": "deepseek",
            "display_name": "Evil Endpoint Provider",
            "billing_mode": "metered_api",
            "credential_env_ref": "DEEPSEEK_API_KEY",
            "endpoint_url": "https://evil.example.com/steal",
            "selected_model": "deepseek-chat",
        },
    )
    assert r_ssrf.status_code == 403
    assert r_ssrf.json()["error"]["code"] == "endpoint_not_allowlisted"

    # 4. Connection check defaults to config check (0 paid network calls)
    chk = client.post("/v321/providers/prov-deepseek-api/test", headers=h("tok-admin"), json={"allow_paid_probe": False}).json()
    assert chk["mode"] == "config_check_no_paid_call"

    # 5. Project egress policy is fail-closed by default
    eg = client.get(f"/v321/projects/{pid}/egress-policy", headers=h("tok-read")).json()
    assert eg["allow_external_paid_api"] is False
    assert eg["default_fail_closed"] is True

    # Enable DeepSeek profile and try calling WITHOUT project egress permission -> 403 project_egress_denied!
    assert client.put("/v321/providers/prov-deepseek-api", headers=h("tok-admin"), json={"enabled": True}).status_code == 200
    biz = client.get("/v31/businesses", headers=h("tok-read")).json()["items"][0]
    r_denied = client.post(
        "/v321/agent-runs",
        headers=h("tok-admin"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": biz["id"],
            "provider_profile_id": "prov-deepseek-api", "objective": "Analyze sensitive banking code",
            "max_budget_usd": 0.10, "egress_consent_confirmed": True,
        },
    )
    assert r_denied.status_code == 403
    assert r_denied.json()["error"]["code"] == "project_egress_denied"


def test_p16_p17_p18_api_agent_tool_calls_budget_guard_and_usage_honesty(client):
    pid, wid = _setup_project(client)
    pid2, wid2 = _setup_project(client, name="Other Project", identity="git:other-proj")

    # Register pack-shared (real local_tool) and allow ONLY doc_lookup (leave other tools default-deny!)
    reg = client.post(
        "/v321/catalog/register",
        headers=h("tok-admin"),
        json={"catalog_key": "pack-shared", "preview_only": False, "approve_immediately": True},
    ).json()
    srv_shared = reg["server"]["id"]
    biz = next(b for b in client.get("/v31/businesses", headers=h("tok-read")).json()["items"] if b["slug"] == "software-developer")
    bid = biz["id"]

    srv_info = next(s for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"] if s["id"] == srv_shared)
    doc_tool = next(t for t in srv_info["tools"] if t["tool_name"] == "doc_lookup")
    denied_tool_name = next(t["tool_name"] for t in srv_info["tools"] if t["tool_name"] != "doc_lookup")
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": doc_tool["id"], "policy": "allow"}).status_code == 200

    # Cross-project worktree isolation check -> 403
    r_cross = client.post(
        "/v321/agent-runs",
        headers=h("tok-admin"),
        json={
            "project_id": pid, "worktree_id": wid2, "business_id": bid,
            "provider_profile_id": "prov-mock-deterministic", "objective": "Cross project attempt",
            "max_budget_usd": 0.05,
        },
    )
    assert r_cross.status_code == 403
    assert r_cross.json()["error"]["code"] == "worktree_scope_denied"

    # 1. Preview run (deterministic 0 LLM: resolves Memory + Skills + Minimal MCP tools)
    prev = client.post(
        "/v321/agent-runs/preview",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "provider_profile_id": "prov-mock-deterministic",
            "objective": "Tra cứu tài liệu docs API Java và tạo bản tóm tắt",
            "thinking_level": "medium", "max_budget_usd": 0.05,
        },
    ).json()
    assert prev["egress_allowed"] is True
    assert any(t["tool_name"] == "doc_lookup" for t in prev["selected_tools"])
    assert prev["estimated_single_call_max_usd"] is not None

    # 2. Full multi-step Mock Provider execution:
    #    Round 0: calls allowed tool `doc_lookup` AND denied tool `safe_read_file`
    #    Round 1: produces final answer + diff preview
    script = [
        {
            "content": "Đang tra cứu tài liệu và kiểm tra quyền tool.",
            "tool_calls": [
                {"call_id": "c1", "server_id": srv_shared, "tool_name": "doc_lookup", "arguments": {"topic": "java"}},
                {"call_id": "c2", "server_id": srv_shared, "tool_name": denied_tool_name, "arguments": {"path": "README.md"}},
            ],
            "usage": {"input_tokens": 500, "cached_input_tokens": 100, "output_tokens": 200, "reasoning_tokens": 50},
        },
        {
            "content": "Đã hoàn tất phân tích read-only dựa trên tài liệu chính thức.\n```diff\n--- a/demo.txt\n+++ b/demo.txt\n+ok\n```",
            "usage": {"input_tokens": 600, "cached_input_tokens": 200, "output_tokens": 150, "reasoning_tokens": 40},
        },
    ]
    run = client.post(
        "/v321/agent-runs",
        headers=h("tok-admin"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "provider_profile_id": "prov-mock-deterministic",
            "objective": "Tra cứu tài liệu docs API Java",
            "thinking_level": "medium", "max_rounds": 3, "max_calls": 4,
            "max_budget_usd": 0.05, "mock_script": script,
        },
    ).json()
    assert run["status"] == "completed"
    assert run["rounds_used"] == 2
    assert run["calls_used"] == 2
    assert run["handoff_id"] is not None
    assert "--- a/demo.txt" in run["patch_diff"]
    assert run["spent_budget_usd"] > 0 and run["reserved_budget_usd"] == 0

    # Verify Execution Map traces recorded both allowed & denied tool calls AND honest token metrics (no reasoning double-count!)
    tr = client.get(f"/v2/traces?project_id={pid}", headers=h("tok-read")).json()
    steps = tr["items"]
    assert any(s["tool_name"] == "doc_lookup" and s["status"] == "succeeded" for s in steps)
    assert any(s["tool_name"] == denied_tool_name and s["status"] == "blocked" for s in steps)
    m = tr["metrics"]
    assert m["input_tokens"] == 1100
    assert m["output_tokens"] == 350
    assert m["reasoning_tokens"] == 90
    assert m["cache_read_tokens"] == 300
    # Known subtotal = 1100 + 350 + 300 = 1750 (NEVER double-counts 90 reasoning!)
    assert m["known_token_subtotal"] == 1750

    # 3. Budget Controller blocks call exceeding tiny budget ($0.0001 < conservative reserve)
    run_b = client.post(
        "/v321/agent-runs",
        headers=h("tok-admin"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "provider_profile_id": "prov-mock-deterministic",
            "objective": "Test budget guard", "max_budget_usd": 0.0001,
        },
    ).json()
    assert run_b["status"] == "stopped_budget"
    assert "budget_exceeded" in (run_b["stop_reason"] or "")

    # 4. Pending run + Cancel works cleanly
    run_pend = client.post(
        "/v321/agent-runs",
        headers=h("tok-admin"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "provider_profile_id": "prov-mock-deterministic",
            "objective": "Pending run to cancel", "max_budget_usd": 0.05, "execute_now": False,
        },
    ).json()
    assert run_pend["status"] == "pending_approval"
    cancelled = client.post(f"/v321/agent-runs/{run_pend['id']}/cancel", headers=h("tok-admin")).json()
    assert cancelled["status"] == "cancelled"


def test_p19_hybrid_plan_stages_external_and_api_agent(client):
    pid, wid = _setup_project(client)
    plan_id = f"hybrid-{uuid.uuid4().hex[:8]}"

    hp = client.post(
        "/v321/hybrid-plans",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "plan_id": plan_id,
            "stages": [
                {"stage_order": 1, "stage_name": "Lập kế hoạch bằng API Agent", "agent_kind": "api_agent", "actor_label": "DeepSeek API", "provider_profile_id": "prov-mock-deterministic"},
                {"stage_order": 2, "stage_name": "Coding bằng Antigravity IDE", "agent_kind": "external", "actor_label": "Antigravity IDE"},
                {"stage_order": 3, "stage_name": "Review bằng Gemini API", "agent_kind": "api_agent", "actor_label": "Gemini API"},
            ],
        },
    ).json()
    assert len(hp["stages"]) == 3
    # External stage MUST be awaiting_external honestly (never fabricated as completed!)
    assert hp["stages"][1]["agent_kind"] == "external"
    assert hp["stages"][1]["status"] == "awaiting_external"

    # Update stage 1 with completed handoff
    st1_id = hp["stages"][0]["id"]
    upd = client.patch(
        f"/v321/hybrid-stages/{st1_id}",
        headers=h("tok-admin"),
        json={"status": "completed", "handoff_summary": "Đã lập kế hoạch 3 bước, bàn giao cho Antigravity."},
    ).json()
    assert upd["status"] == "completed"
    assert "Antigravity" in upd["handoff_summary"]
