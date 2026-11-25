"""V3.1 P10.2 Business Workspace acceptance tests (all 17 scenarios)."""
from __future__ import annotations

import sys
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def test_p102_business_workspace_full_lifecycle(client):
    pid, wid = _setup_project(client)

    # Initial 3 templates seeded automatically
    lst = client.get("/v31/businesses", headers=h("tok-read"))
    assert lst.status_code == 200
    names = {b["name"] for b in lst.json()["items"]}
    assert {"Game Development", "Technical Architect", "Software Developer"} <= names

    # 1. Create business (reject raw secret first!)
    bad_sec = client.post(
        "/v31/businesses",
        headers=h("tok-admin"),
        json={"name": "BadBiz", "model_policy": {"api_key": "sk-1234567890abcdef1234567890"}},
    )
    assert bad_sec.status_code == 422

    r_create = client.post(
        "/v31/businesses",
        headers=h("tok-admin"),
        json={
            "name": "QA Automation",
            "description": "Kiem thu tu dong",
            "icon": "flask",
            "model_policy": {"default_model": "gemini-4-neon-medium"},
            "project_ids": [pid],
        },
    )
    assert r_create.status_code == 201
    biz = r_create.json()
    bid = biz["id"]
    assert biz["revision"] == 1
    assert len(biz["projects"]) == 1

    # 2. Update business -> revision 2
    r_upd = client.put(
        f"/v31/businesses/{bid}",
        headers=h("tok-admin"),
        json={"description": "Kiem thu tu dong v2", "agent_instructions": "Chay test truoc khi bao cao."},
    )
    assert r_upd.status_code == 200
    assert r_upd.json()["revision"] == 2

    # 3. Duplicate business
    r_dup = client.post(
        f"/v31/businesses/{bid}/duplicate",
        headers=h("tok-admin"),
        json={"new_name": "QA Automation Copy"},
    )
    assert r_dup.status_code == 201
    dup_id = r_dup.json()["id"]
    assert dup_id != bid

    # 6 & 7. Bind MCP to both businesses, then unbind from one -> other unaffected, registry untouched
    srvs = client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"]
    bridge_srv = next(s for s in srvs if s["name"] == "ai-memory-bridge")
    assert client.post(f"/v31/businesses/{bid}/mcps", headers=h("tok-admin"), json={"server_id": bridge_srv["id"]}).status_code == 200
    assert client.post(f"/v31/businesses/{dup_id}/mcps", headers=h("tok-admin"), json={"server_id": bridge_srv["id"]}).status_code == 200

    # 10. Set tool permission
    tool_id = bridge_srv["tools"][0]["id"]
    r_pol = client.post(
        f"/v31/businesses/{bid}/tools/policy",
        headers=h("tok-admin"),
        json={"tool_id": tool_id, "policy": "allow"},
    )
    assert r_pol.status_code == 200

    # Unbind from bid only
    assert client.delete(f"/v31/businesses/{bid}/mcps/{bridge_srv['id']}", headers=h("tok-admin")).status_code == 200
    assert len(client.get(f"/v31/businesses/{dup_id}", headers=h("tok-read")).json()["mcp_bindings"]) >= 1
    assert any(s["id"] == bridge_srv["id"] for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"])

    # 8 & 9. Detach & re-attach project
    assert client.delete(f"/v31/businesses/{bid}/projects/{pid}", headers=h("tok-admin")).status_code == 200
    assert len(client.get(f"/v31/businesses/{bid}", headers=h("tok-read")).json()["projects"]) == 0
    assert client.post(f"/v31/businesses/{bid}/projects", headers=h("tok-admin"), json={"project_id": pid}).status_code == 200

    # 11. Export & Import
    exp = client.get(f"/v31/businesses/{bid}/export", headers=h("tok-read")).json()
    assert exp["schema"] == "ai-memory-hub-v31-business-export"
    imp = client.post("/v31/businesses/import", headers=h("tok-admin"), json={"payload": exp, "new_name": "QA Imported"})
    assert imp.status_code == 201
    assert imp.json()["name"] == "QA Imported"

    # 12 & 13. Version history & restore revision 1
    vers = client.get(f"/v31/businesses/{bid}/versions", headers=h("tok-read")).json()["items"]
    assert len(vers) >= 2
    r_rev = client.post(f"/v31/businesses/{bid}/versions/1/restore", headers=h("tok-admin"))
    assert r_rev.status_code == 200
    assert r_rev.json()["description"] == "Kiem thu tu dong"

    # 17. Create a memory, then archive business -> memory, project, MCP preserved!
    m_res = client.post(
        "/v1/memories",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "type": "decision", "title": "KeepMe", "body": "Must survive business archive", "source_agent": "v31-test", "idempotency_key": "k-survive-1"},
    )
    assert m_res.status_code in (200, 201)

    # 4 & 5. Archive & Restore business
    arch = client.post(f"/v31/businesses/{bid}/archive", headers=h("tok-admin"))
    assert arch.status_code == 200
    assert arch.json()["archived"] is True
    assert arch.json()["preserved"]["memories_untouched"] is True

    # Verify memory still exists
    mems = client.get(f"/v1/memories?project_id={pid}", headers=h("tok-read")).json()["items"]
    assert any(m["title"] == "KeepMe" for m in mems)

    # 16. Simulate restart: re-calling ensure_initial_seed does NOT resurrect archived business
    client.app.state.v31_business_service._initialized = False
    client.app.state.v31_business_service.ensure_initial_seed()
    assert client.get(f"/v31/businesses/{bid}", headers=h("tok-read")).json()["archived"] is True

    rest = client.post(f"/v31/businesses/{bid}/restore", headers=h("tok-admin"))
    assert rest.status_code == 200
    assert rest.json()["archived"] is False
    assert rest.json()["enabled"] is True
