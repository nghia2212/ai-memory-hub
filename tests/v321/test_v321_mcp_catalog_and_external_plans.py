"""V3.2.1 (MCP321) P1–P8 Acceptance Tests: Candidate Catalog, Real Specialized Packs, External Plan Sync & Unified P6A."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from services.orchestrator.external_plan_collector import CLIENT_PLAN_COMPATIBILITY, parse_markdown_plan_artifact


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def test_mcp321_catalog_registration_and_real_pack_invocation(client):
    pid, wid = _setup_project(client)

    # 1. Candidate catalog is separate from registered servers and contains >= 10 verified entries
    cat = client.get("/v321/catalog", headers=h("tok-read")).json()
    assert cat["total"] >= 10
    keys = {c["catalog_key"] for c in cat["items"]}
    assert {"pack-shared", "pack-game", "pack-architect", "pack-developer", "github-mcp", "context7-mcp", "filesystem-mcp", "playwright-mcp"} <= keys
    assert all(c["registered"] is False and c["lifecycle_state"] == "candidate_not_installed" for c in cat["items"] if c["catalog_key"] == "pack-shared")

    # 2. Preview register pack-shared
    prev = client.post("/v321/catalog/register", headers=h("tok-admin"), json={"catalog_key": "pack-shared", "preview_only": True}).json()
    assert prev["preview_only"] is True and prev["transport"] == "local_tool"

    # 3. Register + approve + live discover pack-shared (real local_tool handlers!)
    reg = client.post(
        "/v321/catalog/register",
        headers=h("tok-admin"),
        json={"catalog_key": "pack-shared", "preview_only": False, "approve_immediately": True, "allowed_project_ids": [pid]},
    ).json()
    assert reg["registered"] is True
    assert reg["health"]["health_status"] == "ready"
    assert reg["discovery"]["discovered_count"] >= 4
    srv_id = reg["server"]["id"]

    # Bind to Business Workspace, allow doc_lookup, invoke real tool through Gateway!
    biz = client.post("/v31/businesses", headers=h("tok-admin"), json={"name": "MCP321 Biz", "project_ids": [pid]}).json()
    bid = biz["id"]
    assert client.post(f"/v31/businesses/{bid}/mcps", headers=h("tok-admin"), json={"server_id": srv_id}).status_code == 200
    srv_detail = next(s for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"] if s["id"] == srv_id)
    doc_tool = next(t for t in srv_detail["tools"] if t["tool_name"] == "doc_lookup")
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": doc_tool["id"], "policy": "allow"}).status_code == 200

    inv = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "server_id": srv_id, "tool_name": "doc_lookup", "arguments": {"topic": "java"}},
    )
    assert inv.status_code == 200
    assert "java" in inv.json()["result"]["official_sources"]


def test_mcp321_p8_external_plan_sync_collector_and_unified_p6a(client, tmp_path: Path):
    pid, wid = _setup_project(client)

    # P8.1 Read-only markdown collector: secret redaction & unauthorized root rejection
    brain_dir = tmp_path / "brain" / "conv-abc"
    brain_dir.mkdir(parents=True)
    plan_md = brain_dir / "implementation_plan.md"
    plan_md.write_text("# Antigravity Plan Alpha\n- [x] Audit schema\n- [/] Implement sync\n- [ ] Verify QA\nkey=sk-1234567890abcdef1234567890\n", encoding="utf-8")

    parsed = parse_markdown_plan_artifact(plan_md, approved_root=tmp_path / "brain")
    assert parsed["title"] == "Antigravity Plan Alpha"
    assert len(parsed["tasks"]) == 3
    assert "sk-1234567890abcdef1234567890" not in parsed["objective"]
    assert "[REDACTED_SECRET]" in parsed["objective"]

    with pytest.raises(PermissionError):
        parse_markdown_plan_artifact(plan_md, approved_root=tmp_path / "other_root")

    assert any(c["client"] == "antigravity" for c in CLIENT_PLAN_COMPATIBILITY)

    # Create real session + root trace for Plan Alpha
    sess = client.post("/v2/sessions", headers=h("tok-write"), json={"project_id": pid, "worktree_id": wid, "role": "manual_cli", "provider": "google", "model_id": "gemini-4-neon", "auth_mode": "subscription_cli"}).json()
    sid = sess["id"]
    root_a = str(uuid.uuid4())
    root_b = str(uuid.uuid4())
    for tid, sname, s_id in [(root_a, "Antigravity-Alpha-Step", sid), (root_b, "Antigravity-Beta-Step", None)]:
        assert client.post(
            "/v2/traces",
            headers=h("tok-write"),
            json={"trace_id": tid, "root_trace_id": tid, "project_id": pid, "worktree_id": wid, "session_id": s_id, "step_type": "user_task", "step_name": sname, "actor_type": "agent", "actor_id": "agy", "status": "succeeded", "started_at": _now(), "ended_at": _now(), "metadata_json": {"source": "agy"}},
        ).status_code == 201

    # Consent required check
    r_noconsent = client.post(
        "/v321/external-plans/ingest",
        headers=h("tok-write"),
        json={"project_id": pid, "source_client": "antigravity", "external_plan_id": "p-no", "title": "No Consent", "consent_confirmed": False},
    )
    assert r_noconsent.status_code == 403

    # Ingest two external Antigravity plans (Alpha & Beta)
    ep_a = client.post(
        "/v321/external-plans/ingest",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "source_client": "antigravity",
            "external_plan_id": parsed["external_plan_id"], "title": parsed["title"],
            "objective": parsed["objective"], "source_status": parsed["source_status"],
            "normalized_status": parsed["normalized_status"],
            "linked_session_id": sid, "linked_root_trace_id": root_a,
            "tasks": parsed["tasks"], "provenance": parsed["provenance"],
        },
    ).json()
    assert ep_a["is_monitor_only"] is True and ep_a["revision_no"] == 1

    # Re-ingest identical -> deduplicated!
    ep_a_dup = client.post(
        "/v321/external-plans/ingest",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "source_client": "antigravity",
            "external_plan_id": parsed["external_plan_id"], "title": parsed["title"],
            "objective": parsed["objective"], "source_status": parsed["source_status"],
            "normalized_status": parsed["normalized_status"],
            "linked_session_id": sid, "linked_root_trace_id": root_a,
            "tasks": parsed["tasks"], "provenance": parsed["provenance"],
        },
    ).json()
    assert ep_a_dup["deduplicated"] is True

    # Ingest Beta + associate root_b explicitly
    ep_b = client.post(
        "/v321/external-plans/ingest",
        headers=h("tok-write"),
        json={"project_id": pid, "source_client": "antigravity", "external_plan_id": "conv-beta/plan.md", "title": "Antigravity Plan Beta", "normalized_status": "active", "linked_root_trace_id": root_b},
    ).json()

    # Unified P6A scope-options shows BOTH external plans under source=antigravity (monitor_only=true)
    opts = client.get(f"/v2/traces/scope-options?project_id={pid}&source=antigravity", headers=h("tok-read")).json()
    assert len(opts["plans"]) == 2
    assert all(p["is_monitor_only"] is True and p["source"] == "antigravity" for p in opts["plans"])

    # Selecting External Plan Alpha shows ONLY Alpha trace, zero leakage from Beta!
    tr_a = client.get(f"/v2/traces?project_id={pid}&external_plan_id={ep_a['id']}", headers=h("tok-read")).json()
    names_a = {it["step_name"] for it in tr_a["items"]}
    assert "Antigravity-Alpha-Step" in names_a and "Antigravity-Beta-Step" not in names_a

    # Explicit session-to-monitoring-plan fallback works
    s_plan = client.post(
        "/v321/external-plans/from-session",
        headers=h("tok-admin"),
        json={"project_id": pid, "session_id": sid, "source_client": "session_monitor", "title": "Monitored Session Plan", "root_trace_id": root_a},
    )
    assert s_plan.status_code == 201 and s_plan.json()["is_monitor_only"] is True

    # Consent withdrawal / tombstone delete preserves immutable traces!
    del_r = client.delete(f"/v321/external-plans/{ep_b['id']}?project_id={pid}", headers=h("tok-admin"))
    assert del_r.status_code == 200 and del_r.json()["traces_preserved"] is True
    assert client.get(f"/v2/traces?project_id={pid}", headers=h("tok-read")).json()["total"] >= 2
