"""MCP Streamable HTTP handshake & tool invocation tests for Memory Bridge V1."""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

BRIDGE_ROOT = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from app.config import Settings
from app.main import create_app


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def running_bridge(tmp_path: Path):
    port = _free_port()
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "mcp_test.db",
        backup_dir=tmp_path / "backups",
        read_tokens={"tok-read"},
        write_tokens={"tok-write"},
        admin_tokens={"tok-admin"},
        worker_tokens={"tok-worker"},
    )
    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            r = httpx.get(f"{base_url}/healthz", timeout=0.5)
            if r.status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("Bridge server did not become healthy in time")

    yield base_url
    server.should_exit = True
    thread.join(timeout=3)


@pytest.mark.anyio
async def test_mcp_requires_auth_and_completes_handshake(running_bridge: str):
    # Unauthenticated /mcp must return 401
    async with httpx.AsyncClient() as http_client:
        unauth = await http_client.post(f"{running_bridge}/mcp", json={})
        assert unauth.status_code == 401

        # Register project & worktree via REST admin
        p = await http_client.post(
            f"{running_bridge}/v1/projects",
            headers={"Authorization": "Bearer tok-admin"},
            json={"name": "mcp-proj", "canonical_identity": "git:mcp-proj"},
        )
        pid = p.json()["project_id"]
        w = await http_client.post(
            f"{running_bridge}/v1/projects/{pid}/worktrees",
            headers={"Authorization": "Bearer tok-admin"},
            json={"canonical_root": "D:\\repo\\mcp-proj", "branch": "main", "head_commit": "111aaa"},
        )
        wid = w.json()["worktree_id"]

    # Connect via FastMCP StreamableHttpTransport with Bearer token
    transport = StreamableHttpTransport(
        url=f"{running_bridge}/mcp",
        headers={"Authorization": "Bearer tok-write"},
    )
    async with Client(transport) as mcp_client:
        tools = await mcp_client.list_tools()
        tool_names = {t.name for t in tools}
        assert {
            "get_project_context",
            "search_memory",
            "get_handoff",
            "save_task_result",
            "save_decision",
            "search_code",
        }.issubset(tool_names)

        # Save decision (candidate)
        dec = await mcp_client.call_tool(
            "save_decision",
            {
                "project_id": pid,
                "worktree_id": wid,
                "title": "Quyết định dùng SQLite FTS5",
                "summary": "Dùng unicode61 cho truy vấn tiếng Việt",
                "evidence": "docs/INTERFACE-V1.md",
                "idempotency_key": "mcp-dec-1",
            },
        )
        dec_data = json.loads(dec.content[0].text)
        assert dec_data["state"] == "candidate"

        # Save task result (handoff)
        ho = await mcp_client.call_tool(
            "save_task_result",
            {
                "project_id": pid,
                "worktree_id": wid,
                "task_id": "AIH-04",
                "summary": "Hoàn thành MCP handshake",
                "evidence": {"tests": "passed"},
                "idempotency_key": "mcp-ho-1",
            },
        )
        ho_data = json.loads(ho.content[0].text)
        assert ho_data["task_id"] == "AIH-04"

        # Get handoff
        fetched = await mcp_client.call_tool(
            "get_handoff",
            {"project_id": pid, "worktree_id": wid, "task_id": "AIH-04"},
        )
        fetched_data = json.loads(fetched.content[0].text)
        assert len(fetched_data["items"]) == 1

        # Get project context
        ctx = await mcp_client.call_tool(
            "get_project_context",
            {"project_id": pid, "worktree_id": wid},
        )
        ctx_data = json.loads(ctx.content[0].text)
        assert ctx_data["project"]["project_id"] == pid

    # Read-only token CAN read via MCP but MUST NOT execute write tools (save_decision / save_task_result)
    read_transport = StreamableHttpTransport(
        url=f"{running_bridge}/mcp",
        headers={"Authorization": "Bearer tok-read"},
    )
    async with Client(read_transport) as read_client:
        ctx = await read_client.call_tool(
            "get_project_context",
            {"project_id": pid, "worktree_id": wid},
        )
        assert json.loads(ctx.content[0].text)["project"]["project_id"] == pid

        with pytest.raises(Exception):
            await read_client.call_tool(
                "save_decision",
                {
                    "project_id": pid,
                    "worktree_id": wid,
                    "title": "Unauthorized write",
                    "summary": "Should be rejected for read token",
                    "evidence": "test",
                    "idempotency_key": "mcp-read-deny-1",
                },
            )

        with pytest.raises(Exception):
            await read_client.call_tool(
                "save_task_result",
                {
                    "project_id": pid,
                    "worktree_id": wid,
                    "task_id": "DENY-01",
                    "summary": "Should be rejected for read token",
                    "evidence": {"ok": False},
                    "idempotency_key": "mcp-read-deny-2",
                },
            )
