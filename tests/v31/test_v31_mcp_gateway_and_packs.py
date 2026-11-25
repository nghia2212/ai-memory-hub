"""V3.1 P10.3–P10.6 tests: Real STDIO MCP Gateway, Security Enforcement, Tool Packs, Architect & Developer E2E."""
from __future__ import annotations

import json
import sqlite3
import struct
import sys
from pathlib import Path

import pytest

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from services.orchestrator.mcp_activation import generate_session_mcp_config, inspect_agent_clients
from services.orchestrator.tool_packs import register_all_local_packs
from services.orchestrator.tool_packs.architect_pack import (
    db_inspect_schema,
    db_validate_readonly_query,
    diagram_validate_and_save,
    http_api_test,
    openapi_diff_breaking,
    openapi_inspect,
)
from services.orchestrator.tool_packs.developer_pack import (
    java_semantic_analyze,
    junit_parse_reports,
    log_analyze,
    maven_inspect_pom,
    security_scan_local,
    ts_semantic_analyze,
)
from services.orchestrator.tool_packs.game_pack import glb_inspect, image_inspect
from services.orchestrator.tool_packs.shared_pack import doc_lookup, fs_read_bounded

FIXTURE_MCP = str(Path(__file__).resolve().with_name("fixture_stdio_mcp.py"))


def test_p103_real_stdio_mcp_gateway_lifecycle_and_security(client):
    pid, wid = _setup_project(client)

    # Built-in servers must NOT be hardcoded 'ready' on seed (P2.5 fix)
    srvs = client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"]
    bridge_srv = next(s for s in srvs if s["name"] == "ai-memory-bridge")
    assert bridge_srv["health_status"] != "ready"

    # Command injection denied
    r_inj = client.post(
        "/v31/mcp/servers",
        headers=h("tok-admin"),
        json={"name": "inj", "adapter_transport": "stdio", "executable": "python; whoami", "args": []},
    )
    assert r_inj.status_code == 422

    # Unauthorized non-loopback HTTP endpoint denied
    r_bad_http = client.post(
        "/v31/mcp/servers",
        headers=h("tok-admin"),
        json={"name": "ext-http", "adapter_transport": "streamable_http", "endpoint": "http://10.0.0.5:8080/mcp"},
    )
    assert r_bad_http.status_code == 422

    # Register real STDIO fixture MCP server
    r_reg = client.post(
        "/v31/mcp/servers",
        headers=h("tok-admin"),
        json={
            "name": "fixture-stdio",
            "description": "Synthetic STDIO MCP server for P10.3 tests",
            "adapter_transport": "stdio",
            "executable": sys.executable,
            "args": [FIXTURE_MCP, "v1"],
            "capability_tags": ["echo_cap"],
            "allowed_project_ids": [pid],
        },
    )
    assert r_reg.status_code == 201
    srv_id = r_reg.json()["id"]
    assert r_reg.json()["approval_state"] == "pending_approval"

    # Health check while pending_approval returns pending_approval
    hc0 = client.post(f"/v31/mcp/servers/{srv_id}/health", headers=h("tok-write")).json()
    assert hc0["health_status"] == "pending_approval"

    # Approve server, run real STDIO health check & real STDIO tools/list discovery!
    assert client.post(f"/v3/mcp/servers/{srv_id}/approval", headers=h("tok-admin"), json={"decision": "approve"}).status_code == 200
    hc1 = client.post(f"/v31/mcp/servers/{srv_id}/health", headers=h("tok-write")).json()
    assert hc1["health_status"] == "ready"
    assert hc1["process_status"] == "running"

    disc = client.post(f"/v31/mcp/servers/{srv_id}/discover", headers=h("tok-admin")).json()
    assert disc["discovered_count"] == 2
    assert set(disc["added"]) == {"echo_tool", "temp_tool"}

    # Bind to a business workspace
    biz = client.post("/v31/businesses", headers=h("tok-admin"), json={"name": "Gateway Biz", "project_ids": [pid]}).json()
    bid = biz["id"]
    assert client.post(f"/v31/businesses/{bid}/mcps", headers=h("tok-admin"), json={"server_id": srv_id}).status_code == 200

    # Default-deny: invoking echo_tool before setting allow MUST return 403!
    inv_deny = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {"text": "hi"}},
    )
    assert inv_deny.status_code == 403

    # Set tool policy to 'approval' -> without approval_ref returns 403, with approval_ref succeeds!
    srv_detail = next(s for s in client.get("/v31/mcp/servers", headers=h("tok-read")).json()["items"] if s["id"] == srv_id)
    echo_tool_id = next(t["id"] for t in srv_detail["tools"] if t["tool_name"] == "echo_tool")
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": echo_tool_id, "policy": "approval"}).status_code == 200

    inv_need_app = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {"text": "hello"}},
    )
    assert inv_need_app.status_code == 403

    # Real STDIO tools/call with approval_ref & idempotency_key!
    inv_ok = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "server_id": srv_id, "tool_name": "echo_tool",
            "arguments": {"text": "hello-v31"},
            "approval_ref": "apr-001",
            "idempotency_key": "idem-echo-1",
        },
    )
    assert inv_ok.status_code == 200
    assert inv_ok.json()["status"] == "completed"
    assert inv_ok.json()["result"]["content"][0]["text"] == "hello-v31"

    # Idempotency replay
    inv_rep = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "server_id": srv_id, "tool_name": "echo_tool",
            "arguments": {"text": "hello-v31"},
            "approval_ref": "apr-001",
            "idempotency_key": "idem-echo-1",
        },
    )
    assert inv_rep.status_code == 200
    assert inv_rep.json()["idempotent_replay"] is True

    # Schema validation rejects missing required 'text'
    inv_bad_args = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {}, "approval_ref": "apr-001"},
    )
    assert inv_bad_args.status_code == 422

    # Output size limit bounding
    inv_big = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {"text": "Z", "repeat": 2000}, "approval_ref": "apr-001", "max_output_chars": 300},
    )
    assert inv_big.status_code == 200
    assert inv_big.json()["truncated"] is True

    # Timeout enforcement
    inv_to = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {"text": "slow", "sleep_sec": 3}, "approval_ref": "apr-001", "timeout_sec": 1},
    )
    assert inv_to.status_code == 504

    # Emergency permission revocation takes effect immediately
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": echo_tool_id, "policy": "deny"}).status_code == 200
    inv_revoked = client.post(
        "/v31/mcp/invoke",
        headers=h("tok-write"),
        json={"project_id": pid, "business_id": bid, "server_id": srv_id, "tool_name": "echo_tool", "arguments": {"text": "after-revoke"}, "approval_ref": "apr-001"},
    )
    assert inv_revoked.status_code == 403

    # Business-aware routing + task snapshot + task override cannot escalate
    assert client.post(f"/v31/businesses/{bid}/tools/policy", headers=h("tok-admin"), json={"tool_id": echo_tool_id, "policy": "allow"}).status_code == 200
    route = client.post(
        "/v31/mcp/route",
        headers=h("tok-write"),
        json={
            "project_id": pid, "worktree_id": wid, "business_id": bid,
            "required_capabilities": ["echo_cap"],
            "allow_write": True,
            "include_server_ids": ["non-bound-server-id"],
        },
    )
    assert route.status_code == 200
    rj = route.json()
    assert rj["status"] == "ready"
    assert any(t["tool_name"] == "echo_tool" for t in rj["selected_tools"])
    assert any(e["reason"] == "task_override_cannot_escalate_unbound_server" for e in rj["excluded_reasons"])

    # Schema change & tool removal detection triggers re-approval
    assert client.post(f"/v31/mcp/servers/{srv_id}/stop", headers=h("tok-admin")).status_code == 200
    upd = client.put(f"/v31/mcp/servers/{srv_id}", headers=h("tok-admin"), json={"args": [FIXTURE_MCP, "v2_schema_change"]})
    assert upd.status_code == 200
    assert upd.json()["reapproval_required"] is True
    assert client.post(f"/v3/mcp/servers/{srv_id}/approval", headers=h("tok-admin"), json={"decision": "approve"}).status_code == 200
    disc2 = client.post(f"/v31/mcp/servers/{srv_id}/discover", headers=h("tok-admin")).json()
    assert "echo_tool" in disc2["changed"]
    assert "temp_tool" in disc2["removed"]
    assert disc2["reapproval_triggered"] is True

    # Monitoring & invocation history
    mon = client.get("/v31/mcp/monitoring", headers=h("tok-read")).json()
    assert mon["invocations_total"] >= 4
    invs = client.get(f"/v31/mcp/invocations?business_id={bid}", headers=h("tok-read")).json()["items"]
    assert len(invs) >= 4
    assert client.post(f"/v31/mcp/servers/{srv_id}/stop", headers=h("tok-admin")).status_code == 200


def test_p105_p106_tool_packs_and_e2e_workflows(client, tmp_path: Path):
    # Register local packs into gateway
    cnt = register_all_local_packs(client.app.state.v31_mcp_gateway)
    assert cnt >= 20

    # P4 Shared pack: safe filesystem blocks .env and ..
    (tmp_path / "hello.txt").write_text("line1\nline2\n", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=123\n", encoding="utf-8")
    assert "line1" in fs_read_bounded({"approved_root": str(tmp_path), "rel_path": "hello.txt"})["content"]
    with pytest.raises(PermissionError):
        fs_read_bounded({"approved_root": str(tmp_path), "rel_path": ".env"})
    with pytest.raises(PermissionError):
        fs_read_bounded({"approved_root": str(tmp_path), "rel_path": "../secret.txt"})
    assert "java" in doc_lookup({"topic": "java"})["official_sources"]

    # P6 Game pack: binary GLB 2.0 inspector on synthetic valid GLB
    gltf_json = json.dumps({
        "asset": {"version": "2.0", "generator": "v31-test"},
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "mode": 4}]}],
        "accessors": [{"count": 3, "min": [0, 0, 0], "max": [1, 2, 3]}],
        "materials": [{"name": "Mat1"}],
        "skins": [{"joints": [0, 1]}],
        "animations": [{"name": "Walk", "channels": [{}], "samplers": [{"input": 0}]}],
    }).encode("utf-8")
    pad = (4 - (len(gltf_json) % 4)) % 4
    gltf_json += b" " * pad
    glb_bytes = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(gltf_json)) + struct.pack("<II", len(gltf_json), 0x4E4F534A) + gltf_json
    glb_file = tmp_path / "puppy.glb"
    glb_file.write_bytes(glb_bytes)
    ginfo = glb_inspect({"file_path": str(glb_file)})
    assert ginfo["valid"] is True
    assert ginfo["mesh_count"] == 1
    assert ginfo["vertex_count"] == 3
    assert ginfo["triangle_count"] == 1
    assert ginfo["bone_count"] == 2
    assert ginfo["animations"][0]["name"] == "Walk"

    # P6 Image PNG inspector
    png_hdr = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", 64, 32, 8, 6, 0, 0, 0)
    png_file = tmp_path / "icon.png"
    png_file.write_bytes(png_hdr)
    img_info = image_inspect({"file_path": str(png_file)})
    assert img_info["width"] == 64 and img_info["height"] == 32 and img_info["has_alpha"] is True

    # P10.5 Technical Architect E2E (isolated SQLite test DB + OpenAPI + Diagram + ADR)
    arch_db = tmp_path / "test_bank.db"
    c = sqlite3.connect(str(arch_db))
    c.execute("CREATE TABLE accounts (id INTEGER PRIMARY KEY, code TEXT NOT NULL)")
    c.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, account_id INTEGER REFERENCES accounts(id))")
    c.commit()
    c.close()
    schema = db_inspect_schema({"engine": "sqlite", "db_path": str(arch_db)})
    assert schema["table_count"] == 2
    assert db_inspect_schema({"engine": "oracle"})["status"] == "not_configured"
    with pytest.raises(PermissionError):
        db_inspect_schema({"engine": "oracle", "host": "prod-bank-core.internal"})
    with pytest.raises(PermissionError):
        db_validate_readonly_query({"sql": "DROP TABLE accounts"})

    spec_v1 = {"openapi": "3.0.3", "info": {"title": "Payment API", "version": "1.0"}, "paths": {"/v1/pay": {"post": {"responses": {"200": {}}}}}}
    spec_v2 = {"openapi": "3.0.3", "info": {"title": "Payment API", "version": "2.0"}, "paths": {}}
    assert openapi_inspect({"spec": spec_v1})["endpoint_count"] == 1
    diff = openapi_diff_breaking({"old_spec": spec_v1, "new_spec": spec_v2})
    assert diff["compatible"] is False and diff["breaking_changes_count"] == 1
    with pytest.raises(PermissionError):
        http_api_test({"url": "https://evil.example.com/api", "allowed_domains": ["127.0.0.1"]})
    diag = diagram_validate_and_save({"kind": "mermaid", "source": "sequenceDiagram\n  Client->>PaymentService: POST /v1/pay", "output_path": str(tmp_path / "seq.mmd")})
    assert diag["valid"] is True and diag["cloud_upload"] is False

    # P10.6 Software Developer E2E (Java/Spring + Maven + JUnit + Security + Log)
    jdir = tmp_path / "java_app"
    (jdir / "src" / "main" / "java" / "com" / "demo").mkdir(parents=True)
    (jdir / "src" / "main" / "java" / "com" / "demo" / "OrderService.java").write_text(
        "package com.demo;\nimport org.springframework.stereotype.Service;\n@Service\npublic class OrderService {\n  public String createOrder(int id) { return \"ok\"; }\n}\n",
        encoding="utf-8",
    )
    (jdir / "pom.xml").write_text(
        "<project><groupId>com.demo</groupId><artifactId>order-svc</artifactId><version>1.0.0</version><dependencies><dependency><groupId>org.junit.jupiter</groupId><artifactId>junit-jupiter</artifactId><version>5.10.0</version><scope>test</scope></dependency></dependencies></project>",
        encoding="utf-8",
    )
    jres = java_semantic_analyze({"project_path": str(jdir)})
    assert jres["files_scanned"] == 1
    assert jres["spring_components"][0]["name"] == "OrderService"
    mres = maven_inspect_pom({"pom_path": str(jdir / "pom.xml")})
    assert mres["artifactId"] == "order-svc" and mres["dependency_count"] == 1

    rep_dir = tmp_path / "surefire"
    rep_dir.mkdir()
    (rep_dir / "TEST-OrderTest.xml").write_text('<testsuite tests="2" failures="0" errors="0" skipped="0"><testcase classname="OrderTest" name="t1"/><testcase classname="OrderTest" name="t2"/></testsuite>', encoding="utf-8")
    jrep = junit_parse_reports({"report_dir": str(rep_dir)})
    assert jrep["all_passed"] is True and jrep["passed"] == 2

    sec = security_scan_local({"project_path": str(jdir)})
    assert sec["offline_only"] is True and sec["findings_count"] == 0
    lres = log_analyze({"log_text": "ERROR correlation_id=corr-991 NullPointerException: missing order\nsecret=sk-1234567890abcdef1234567890"})
    assert "corr-991" in lres["correlation_ids"]
    assert "NullPointerException" in lres["exception_groups"]
    assert lres["secrets_redacted"] is True

    # P3.5 Agent Activation (never mutates global config)
    clients = inspect_agent_clients()
    assert len(clients) >= 5
    cfg = generate_session_mcp_config(
        client="claude_code",
        selected_servers=[{"name": "ai-memory-bridge", "transport": "streamable_http", "endpoint_or_cmd": "http://127.0.0.1:8082/mcp"}],
        output_path=tmp_path / "session_mcp.json",
    )
    assert (tmp_path / "session_mcp.json").is_file()
    assert cfg["activated_live"] is False
