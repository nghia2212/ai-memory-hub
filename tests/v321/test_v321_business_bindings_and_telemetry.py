"""V3.2.1 (MCP321) Business MCP Binding, Project Overrides, Dynamic Resolution & P9-P12 Telemetry Tests."""
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


def test_default_business_bindings_idempotent_and_accurate_stats(client):
    """Verify:
    1. Registering catalog entries (local packs + external pending_approval MCPs) triggers idempotent default bindings.
    2. Game Development / Technical Architect / Software Developer get exact counts & honest stats breakdown:
       - local_tool packs are counted in local_packs_ready_count, NEVER in mcp_online_count!
       - pending_approval external MCPs are counted in mcp_pending_approval_count, NEVER auto-approved!
    3. Manual user unbind from ONE business does NOT affect other businesses or global MCP registry,
       and re-running sync_default_bindings does NOT overwrite user removal!
    """
    # Register all 4 local packs (approved) + 6 external MCPs (pending_approval)
    for key, approve in [
        ("pack-shared", True),
        ("pack-game", True),
        ("pack-architect", True),
        ("pack-developer", True),
        ("github-mcp", False),
        ("context7-mcp", False),
        ("filesystem-mcp", False),
        ("blender-mcp", False),
        ("chrome-devtools-mcp", False),
        ("playwright-mcp", False),
    ]:
        r = client.post(
            "/v321/catalog/register",
            headers=h("tok-admin"),
            json={"catalog_key": key, "preview_only": False, "approve_immediately": approve},
        )
        assert r.status_code == 200, r.text

    # Trigger explicit sync (idempotent)
    sync1 = client.post("/v321/businesses/sync-default-bindings", headers=h("tok-admin")).json()
    assert sync1["synced"] is True
    sync2 = client.post("/v321/businesses/sync-default-bindings", headers=h("tok-admin")).json()
    assert sync2["added_count"] == 0  # Strictly idempotent!

    biz_list = client.get("/v31/businesses", headers=h("tok-read")).json()["items"]
    by_slug = {b["slug"]: b for b in biz_list}

    # Game Development: 11 total (2 builtin + 3 packs + 6 pending external)
    game = by_slug["game-development"]
    assert game["mcp_bound_total"] == 11
    assert game["local_packs_ready_count"] == 3
    assert game["mcp_pending_approval_count"] == 6
    assert game["mcp_online_count"] <= 2  # NEVER includes local_packs!

    # Technical Architect: 9 total (2 builtin + 3 packs + 4 pending external)
    arch = by_slug["technical-architect"]
    assert arch["mcp_bound_total"] == 9
    assert arch["local_packs_ready_count"] == 3
    assert arch["mcp_pending_approval_count"] == 4

    # Software Developer: 9 total (2 builtin + 2 packs + 5 pending external)
    dev = by_slug["software-developer"]
    assert dev["mcp_bound_total"] == 9
    assert dev["local_packs_ready_count"] == 2
    assert dev["mcp_pending_approval_count"] == 5

    # Unbind github-mcp from Software Developer ONLY -> does not delete from global registry or Game Dev
    dev_detail = client.get(f"/v31/businesses/{dev['id']}", headers=h("tok-read")).json()
    gh_bind = next(m for m in dev_detail["mcp_bindings"] if m["server_name"] == "github-mcp")
    assert gh_bind["server_kind"] == "mcp_server"
    assert gh_bind["approval_state"] in ("pending", "pending_approval")

    r_unbind = client.delete(f"/v31/businesses/{dev['id']}/mcps/{gh_bind['server_id']}", headers=h("tok-admin"))
    assert r_unbind.status_code == 200
    assert len(r_unbind.json()["mcp_bindings"]) == 8

    # Still exists in global registry and in Game Development!
    all_srvs = {s["name"] for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"]}
    assert "github-mcp" in all_srvs
    game_detail = client.get(f"/v31/businesses/{game['id']}", headers=h("tok-read")).json()
    assert any(m["server_name"] == "github-mcp" for m in game_detail["mcp_bindings"])

    # Re-sync MUST NOT re-add user-removed github-mcp to Software Developer!
    sync3 = client.post("/v321/businesses/sync-default-bindings", headers=h("tok-admin")).json()
    assert sync3["added_count"] == 0
    dev_detail_after = client.get(f"/v31/businesses/{dev['id']}", headers=h("tok-read")).json()
    assert not any(m["server_name"] == "github-mcp" for m in dev_detail_after["mcp_bindings"])


def test_project_override_non_escalation_and_dynamic_mcp_resolution(client):
    """Verify:
    1. Project inherits business MCPs and can disable a server via project override.
    2. Project override CANNOT escalate an unbound or pending_approval server (403).
    3. Dynamic MCP resolution infers capabilities deterministically (0 LLM) from task_objective,
       excludes pending_approval & project-disabled servers, and returns honest schema bytes telemetry.
    """
    pid, wid = _setup_project(client)

    reg_shared = client.post(
        "/v321/catalog/register",
        headers=h("tok-admin"),
        json={"catalog_key": "pack-shared", "preview_only": False, "approve_immediately": True},
    ).json()
    srv_shared = reg_shared["server"]["id"]

    reg_gh = client.post(
        "/v321/catalog/register",
        headers=h("tok-admin"),
        json={"catalog_key": "github-mcp", "preview_only": False, "approve_immediately": False},
    ).json()
    srv_gh = reg_gh["server"]["id"]

    biz = next(b for b in client.get("/v31/businesses", headers=h("tok-read")).json()["items"] if b["slug"] == "software-developer")
    bid = biz["id"]

    # Attach project explicitly
    assert client.post(f"/v31/businesses/{bid}/projects", headers=h("tok-admin"), json={"project_id": pid}).status_code == 200

    # Allow doc_lookup on pack-shared
    srv_info = next(s for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"] if s["id"] == srv_shared)
    doc_tool = next(t for t in srv_info["tools"] if t["tool_name"] == "doc_lookup")
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": doc_tool["id"], "policy": "allow"}).status_code == 200

    # Dynamic route by task_objective (0 LLM deterministic inference!)
    route1 = client.post(
        "/v31/mcp/route",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "task_objective": "Tra cứu tài liệu docs hướng dẫn"},
    ).json()
    assert route1["status"] == "ready"
    assert srv_shared in route1["selected_servers"]
    assert srv_gh not in route1["selected_servers"]
    assert any(e.get("server_name") == "github-mcp" and e.get("reason", "").startswith("approval_pending") for e in route1["excluded_reasons"])
    assert route1["telemetry"]["tool_schema_bytes"] > 0
    assert route1["telemetry"]["subset_of_input_not_added_to_total"] is True

    # Project override CANNOT enable pending_approval github-mcp (403 non-escalation)
    r_esc = client.post(
        f"/v321/projects/{pid}/mcp-overrides",
        headers=h("tok-admin"),
        json={"business_id": bid, "server_id": srv_gh, "mode": "enabled"},
    )
    assert r_esc.status_code == 403

    # Project override disables pack-shared for this project
    r_dis = client.post(
        f"/v321/projects/{pid}/mcp-overrides",
        headers=h("tok-admin"),
        json={"business_id": bid, "server_id": srv_shared, "mode": "disabled"},
    )
    assert r_dis.status_code == 200
    assert any(x["server_id"] == srv_shared and x["effective_enabled"] is False for x in r_dis.json()["items"])

    # Invocation is now blocked by project override (403)
    r_inv = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "server_id": srv_shared, "tool_name": "doc_lookup", "arguments": {"topic": "java"}},
    )
    assert r_inv.status_code == 403
    assert r_inv.json()["error"]["code"] == "project_override_disabled"

    # Routing also excludes pack-shared with explicit reason
    route2 = client.post(
        "/v31/mcp/route",
        headers=h("tok-write"),
        json={"project_id": pid, "business_id": bid, "required_capabilities": ["docs_lookup"]},
    ).json()
    assert srv_shared not in route2["selected_servers"]
    assert any(e.get("server_id") == srv_shared and e.get("reason") == "disabled_by_project_override" for e in route2["excluded_reasons"])


def test_p9_runtime_adapters_and_p11_token_honesty(client):
    """Verify P9 Runtime Adapter inventory & P11 Token/Thinking/Pricing null semantics without double-counting."""
    pid, wid = _setup_project(client)

    # P9 Runtime adapters manifest
    ra = client.get("/v321/runtime-adapters", headers=h("tok-read")).json()
    cids = {a["client_id"] for a in ra["adapters"]}
    assert {"antigravity", "claude", "codex", "gemini", "deepseek_api", "qwen_local"} <= cids
    assert ra["managed_namespace"] == "ai-memory-hub-managed"

    # P11 Token telemetry: measured llm_call with reasoning_tokens (never double-counted into known_token_subtotal!)
    t1 = str(uuid.uuid4())
    t2 = str(uuid.uuid4())
    assert client.post(
        "/v2/traces",
        headers=h("tok-write"),
        json={
            "trace_id": t1, "root_trace_id": t1, "project_id": pid, "worktree_id": wid,
            "step_type": "llm_call", "step_name": "Measured Call", "actor_type": "model", "actor_id": "deepseek",
            "provider": "deepseek", "model_id": "deepseek-reasoner", "status": "succeeded",
            "started_at": _now(), "ended_at": _now(), "used_llm": True,
            "input_tokens": 1000, "output_tokens": 500, "reasoning_tokens": 300,
            "metadata_json": {"source": "runner", "measurement_kind": "measured"},
        },
    ).status_code == 201
    # Unknown token call -> MUST remain null, NOT 0!
    assert client.post(
        "/v2/traces",
        headers=h("tok-write"),
        json={
            "trace_id": t2, "root_trace_id": t2, "project_id": pid, "worktree_id": wid,
            "step_type": "llm_call", "step_name": "Unknown Call", "actor_type": "model", "actor_id": "agy",
            "provider": "google", "model_id": "gemini-4-neon", "status": "succeeded",
            "started_at": _now(), "ended_at": _now(), "used_llm": True,
            "metadata_json": {"source": "agy", "measurement_kind": "unavailable"},
        },
    ).status_code == 201

    res = client.get(f"/v2/traces?project_id={pid}", headers=h("tok-read")).json()
    m = res["metrics"]
    assert m["input_tokens"] == 1000
    assert m["output_tokens"] == 500
    assert m["reasoning_tokens"] == 300
    assert m["cache_read_tokens"] is None  # NEVER fabricated as 0!
    assert m["known_token_subtotal"] == 1500  # 1000 + 500 (NEVER double-counts 300 reasoning!)
    assert m["cost_kind"] == "estimated"
    assert m["estimated_cost_usd"] is not None and m["estimated_cost_usd"] > 0
    assert "skill_runs" in res
