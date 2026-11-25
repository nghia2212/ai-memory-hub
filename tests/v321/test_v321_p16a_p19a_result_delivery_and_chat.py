"""V3.2.1 (MCP321) P16A Result Delivery & P19A AI Chat Workspace Acceptance Tests."""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from services.orchestrator.result_parser import parse_agent_stdout


def test_p16a_zero_dependency_result_parser_all_formats():
    """Verify P16A parser handles plain text, Markdown, JSON, JSONL, malformed JSON, empty/missing,
    runner errors, secret redaction, and strips private <think> blocks without pydantic."""
    # 1. Valid JSON envelope (agy / claude)
    r_json = parse_agent_stdout(
        cli_family="agy",
        stdout='{"result": "Xin chào! Tôi là trợ lý AI của hệ thống.", "usage": {"input_tokens": 120, "output_tokens": 45}}',
    )
    assert r_json["result_delivery_status"] == "delivered"
    assert "Xin chào!" in r_json["final_answer"]
    assert r_json["usage"]["input_tokens"] == 120
    assert r_json["usage"]["output_tokens"] == 45

    # 2. Markdown + code block + private <think> + secret redaction
    r_md = parse_agent_stdout(
        cli_family="agy",
        stdout="<think>private chain of thought</think>\n# Giải thích mã nguồn\n```python\nprint('ok')\n```\ntoken=sk-1234567890abcdef1234567890",
    )
    assert r_md["result_delivery_status"] == "delivered"
    assert r_md["content_type"] == "text/markdown"
    assert "private chain of thought" not in r_md["final_answer"]
    assert "sk-1234567890abcdef1234567890" not in r_md["final_answer"]
    assert "[REDACTED]" in r_md["final_answer"]

    # 3. Malformed JSON with legitimate text preserved
    r_bad_json = parse_agent_stdout(cli_family="agy", stdout="{broken json but valid answer text: Xin chào")
    assert r_bad_json["result_delivery_status"] == "delivered"
    assert "Xin chào" in r_bad_json["final_answer"]

    # 4. Codex JSONL
    jsonl = (
        '{"type":"item.completed","item":{"type":"agent_message","text":"Phân tích dự án hoàn tất."}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":300,"output_tokens":80}}\n'
    )
    r_codex = parse_agent_stdout(cli_family="codex", stdout=jsonl)
    assert r_codex["result_delivery_status"] == "delivered"
    assert "Phân tích dự án hoàn tất." in r_codex["final_answer"]
    assert r_codex["usage"]["input_tokens"] == 300

    # 5. Empty stdout -> MUST be 'missing', NEVER fake success or 'No structured result available.'!
    r_empty = parse_agent_stdout(cli_family="agy", stdout="   ", exit_code=0)
    assert r_empty["result_delivery_status"] == "missing"
    assert r_empty["final_answer"] == ""

    # 6. Non-zero exit -> 'failed'
    r_fail = parse_agent_stdout(cli_family="agy", stdout="", stderr="CLI crashed", exit_code=1)
    assert r_fail["result_delivery_status"] == "failed"


def test_p19a_ai_chat_workspace_modes_followup_isolation_and_budget(client):
    pid, wid = _setup_project(client)
    pid2, _ = _setup_project(client, name="Isolated Project", identity="git:isolated-chat")
    biz = client.get("/v31/businesses", headers=h("tok-read")).json()["items"][0]
    bid = biz["id"]

    # 1. Create conversation in Chat mode
    conv = client.post(
        "/v321/chat/conversations",
        headers=h("tok-admin"),
        json={"project_id": pid, "worktree_id": wid, "business_id": bid, "mode": "chat"},
    ).json()
    cid = conv["id"]

    # 2. Chat mode: "Xin chào, bạn là ai?" -> NO Plan/DAG created, immediate delivered answer
    plans_before = len(client.get(f"/v2/plans?project_id={pid}", headers=h("tok-read")).json()["items"])
    m1 = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Xin chào, bạn là ai?",
            "mode": "chat",
            "idempotency_key": "msg-hello-1",
            "mock_script": [{"content": "Xin chào! Tôi là trợ lý AI của AI Memory Hub (Mock fixture).", "usage": {"input_tokens": 80, "output_tokens": 30}}],
        },
    ).json()
    assert m1["idempotent_replay"] is False
    assert m1["assistant_message"]["delivery_status"] == "delivered"
    assert "Xin chào!" in m1["assistant_message"]["content"]
    plans_after_chat = len(client.get(f"/v2/plans?project_id={pid}", headers=h("tok-read")).json()["items"])
    assert plans_after_chat == plans_before  # Chat mode does NOT force Plan/DAG!

    # Idempotent retry MUST replay without calling AI again
    m1_dup = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={"project_id": pid, "content": "Xin chào, bạn là ai?", "mode": "chat", "idempotency_key": "msg-hello-1"},
    ).json()
    assert m1_dup["idempotent_replay"] is True
    assert m1_dup["assistant_message"]["id"] == m1["assistant_message"]["id"]

    # 3. Follow-up question uses persisted conversation history!
    m2 = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Tiếp tục câu hỏi trước đó: hãy giới thiệu về bạn ngắn gọn.",
            "mode": "chat",
            "mock_script": [{"content": "Tiếp nối câu trước: tôi hỗ trợ Memory, MCP và Skill trong AI Memory Hub.", "usage": {"input_tokens": 140, "output_tokens": 40}}],
        },
    ).json()
    assert m2["assistant_message"]["delivery_status"] == "delivered"
    assert m2["assistant_message"]["sources"]["history_turns"] >= 2

    # 4. Cross-project isolation check -> 404
    r_cross = client.get(f"/v321/chat/conversations/{cid}?project_id={pid2}", headers=h("tok-read"))
    assert r_cross.status_code == 404

    # 5. Task mode: runs scoped Hub-managed API Agent & returns handoff + trace
    m3 = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Phân tích dự án và giải thích một đoạn mã nguồn.",
            "mode": "task",
            "mock_script": [{"content": "## Phân tích dự án\n```python\ndef ok(): return True\n```", "usage": {"input_tokens": 220, "output_tokens": 90}}],
        },
    ).json()
    assert m3["assistant_message"]["mode"] == "task"
    assert m3["assistant_message"]["delivery_status"] == "delivered"
    assert m3["assistant_message"]["run_id"] is not None
    assert m3["assistant_message"]["sources"]["handoff_id"] is not None

    # 6. Plan & DAG mode: creates real v2_plans in REVIEW_REQUIRED
    m4 = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={
            "project_id": pid,
            "content": "Thực hiện nhiệm vụ lập trình nhiều bước cho module thanh toán",
            "mode": "plan_dag",
        },
    ).json()
    assert m4["assistant_message"]["mode"] == "plan_dag"
    assert m4["assistant_message"]["plan_id"] is not None
    assert len(client.get(f"/v2/plans?project_id={pid}", headers=h("tok-read")).json()["items"]) == plans_before + 1

    # 7. Budget exceeded in Chat mode -> 403
    r_b = client.post(
        f"/v321/chat/conversations/{cid}/messages",
        headers=h("tok-admin"),
        json={"project_id": pid, "content": "Test budget block", "mode": "chat", "max_budget_usd": 0.0001},
    )
    assert r_b.status_code == 403

    # 8. Durable retrieval & export (persists across refresh/restart)
    full = client.get(f"/v321/chat/conversations/{cid}?project_id={pid}", headers=h("tok-read")).json()
    assert len(full["messages"]) >= 8
    exp = client.get(f"/v321/chat/conversations/{cid}/export?project_id={pid}", headers=h("tok-read")).json()
    assert exp["schema"] == "ai-memory-hub-v321-chat-export"
