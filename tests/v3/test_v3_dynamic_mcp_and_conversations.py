"""V3 M2, M4, M5, M6 integration tests: Dynamic MCP Registry/Routing, Conversation Storage, FTS5 Vietnamese Search & Promote."""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def test_v3_dynamic_mcp_registry_approval_and_scoped_routing(client):
    pid, _ = _setup_project(client)

    # Builtin servers seeded automatically
    srvs = client.get("/v3/mcp/servers", headers=h("tok-read"))
    assert srvs.status_code == 200
    names = {s["name"] for s in srvs.json()["items"]}
    assert {"ai-memory-bridge", "ai-memory-codebase"} <= names

    # Unsafe shell syntax rejected
    r_bad = client.post(
        "/v3/mcp/servers",
        headers=h("tok-admin"),
        json={"name": "evil", "transport": "stdio", "endpoint_or_cmd": "sh -c 'whoami'"},
    )
    assert r_bad.status_code == 422

    # Register local MCP server -> starts as pending_approval (never auto-activated)
    r_reg = client.post(
        "/v3/mcp/servers",
        headers=h("tok-admin"),
        json={
            "name": "local-db-inspector",
            "transport": "streamable_http",
            "endpoint_or_cmd": "http://127.0.0.1:9099/mcp",
            "version": "1.0.0",
            "allowed_project_ids": [pid],
            "tools": [
                {"tool_name": "inspect_schema", "capability_tags": ["sql_schema"], "side_effect_level": "read_only"},
                {"tool_name": "drop_table", "capability_tags": ["sql_admin"], "side_effect_level": "restricted"},
            ],
        },
    )
    assert r_reg.status_code == 201
    sid = r_reg.json()["id"]
    assert r_reg.json()["approval_state"] == "pending_approval"

    # Pending server must NOT be routed before approval
    r_pre = client.post("/v3/mcp/route", headers=h("tok-planner"), json={"project_id": pid, "required_capabilities": ["sql_schema"]})
    assert r_pre.status_code == 200 and r_pre.json()["matches"] == []

    # Approve server -> now read_only tool routes cleanly, restricted tool excluded when allow_write=False
    assert client.post(f"/v3/mcp/servers/{sid}/approval", headers=h("tok-admin"), json={"decision": "approve"}).status_code == 200
    r_post = client.post("/v3/mcp/route", headers=h("tok-planner"), json={"project_id": pid, "required_capabilities": ["sql_schema", "sql_admin"], "allow_write": False})
    assert r_post.status_code == 200
    matched_tools = [m["tool_name"] for m in r_post.json()["matches"]]
    assert matched_tools == ["inspect_schema"]

    # Audit log recorded
    aud = client.get("/v3/mcp/audit", headers=h("tok-read"))
    assert aud.status_code == 200 and len(aud.json()["items"]) >= 3


def test_v3_conversations_ingest_dedupe_vietnamese_fts5_promote_export_and_isolation(client):
    pid1, wid1 = _setup_project(client, name="v3-conv-p1", identity="git:v3-conv-p1")
    pid2, _ = _setup_project(client, name="v3-conv-p2", identity="git:v3-conv-p2")

    msg1 = "Cần kiểm tra lỗi phân quyền Dashboard và tối ưu hóa ngữ cảnh V3."
    msg2 = "Đã khắc phục xác thực cookie và bổ sung bộ tìm kiếm FTS5 tiếng Việt."
    msgs = [
        {"seq_no": 1, "role": "user", "visible_text": msg1, "content_hash": hashlib.sha256(msg1.encode()).hexdigest(), "created_at": "2026-11-25T08:00:00Z"},
        {"seq_no": 2, "role": "assistant", "visible_text": msg2, "content_hash": hashlib.sha256(msg2.encode()).hexdigest(), "created_at": "2026-11-25T08:01:00Z", "model_id": "gemini-4-neon-medium", "input_tokens": 500, "output_tokens": 120},
    ]

    r1 = client.post(
        "/v3/conversations/ingest",
        headers=h("tok-write"),
        json={
            "project_id": pid1, "worktree_id": wid1, "client_type": "antigravity",
            "external_conversation_id": "conv-vn-001", "provider": "agy",
            "model_id": "gemini-4-neon-medium", "title": "Tối ưu V3 tiếng Việt", "messages": msgs,
        },
    )
    assert r1.status_code == 201, r1.text
    cid = r1.json()["conversation_id"]
    assert r1.json()["inserted"] == 2 and r1.json()["deduplicated"] == 0

    # Re-ingest same messages -> deduplicated cleanly
    r2 = client.post(
        "/v3/conversations/ingest",
        headers=h("tok-write"),
        json={
            "project_id": pid1, "worktree_id": wid1, "client_type": "antigravity",
            "external_conversation_id": "conv-vn-001", "title": "Tối ưu V3 tiếng Việt", "messages": msgs,
        },
    )
    assert r2.status_code == 201 and r2.json()["inserted"] == 0 and r2.json()["deduplicated"] == 2

    # Vietnamese FTS5 search scoped to project 1 (project 2 must see nothing)
    s1 = client.get("/v3/conversations/search", params={"project_id": pid1, "query": "phân quyền Dashboard"}, headers=h("tok-read"))
    assert s1.status_code == 200 and len(s1.json()["items"]) == 1
    s2 = client.get("/v3/conversations/search", params={"project_id": pid2, "query": "phân quyền Dashboard"}, headers=h("tok-read"))
    assert s2.status_code == 200 and s2.json()["items"] == []

    # Detail + promote excerpt to candidate memory
    det = client.get(f"/v3/conversations/{cid}", params={"project_id": pid1}, headers=h("tok-read"))
    assert det.status_code == 200 and det.json()["conversation"]["links"]["unlinked"] is True
    mid = det.json()["messages"][1]["id"]

    prom = client.post(
        f"/v3/conversations/{cid}/promote",
        headers=h("tok-admin"),
        json={"project_id": pid1, "message_id": mid, "title": "Kinh nghiệm FTS5 tiếng Việt"},
    )
    assert prom.status_code == 201 and prom.json()["state"] == "candidate"
    assert prom.json()["evidence_ref"] == f"conversation:{cid}#seq:2"

    # Export Markdown & JSON + Delete
    exp_md = client.get(f"/v3/conversations/{cid}/export", params={"project_id": pid1, "format": "md"}, headers=h("tok-read"))
    assert exp_md.status_code == 200 and "Tối ưu V3 tiếng Việt" in exp_md.text
    assert client.delete(f"/v3/conversations/{cid}", params={"project_id": pid1}, headers=h("tok-admin")).status_code == 200
    assert client.get(f"/v3/conversations/{cid}", params={"project_id": pid1}, headers=h("tok-read")).status_code == 404
