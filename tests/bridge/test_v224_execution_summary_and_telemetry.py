"""V2.2.4 backend regression tests: global execution sequence, ties, multi-level summary, export & telemetry inspector."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def _trace(pid: str, wid: str, *, tid=None, parent=None, root=None, sid=None, step_type="agent_step", step_name="Step", actor_type="agent", actor_id="lead", status="succeeded", started=None, ended=None, meta=None, **extra):
    tid = tid or str(uuid.uuid4())
    root = root or (tid if not parent else root)
    body = {
        "trace_id": tid,
        "parent_trace_id": parent,
        "root_trace_id": root,
        "project_id": pid,
        "worktree_id": wid,
        "session_id": sid,
        "step_type": step_type,
        "step_name": step_name,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "status": status,
        "started_at": started,
        "ended_at": ended,
        "metadata_json": {"source": "test_v224", **(meta or {})},
        **extra,
    }
    return body


def test_v224_sequence_ties_summaries_export_and_inspector(client):
    pid, wid = _setup_project(client)
    t0 = datetime(2026, 11, 25, 8, 0, 0, tzinfo=timezone.utc)

    # Register a session
    sid = str(uuid.uuid4())
    assert client.post(
        "/v2/sessions",
        headers=h("tok-write"),
        json=dict(session_id=sid, project_id=pid, worktree_id=wid, role="manual_cli", provider="agy", model_id="gemini-4-neon-medium", status="running"),
    ).status_code == 201

    root_id = str(uuid.uuid4())
    tie_a = str(uuid.uuid4())
    tie_b = str(uuid.uuid4())
    retry_id = str(uuid.uuid4())
    late_id = str(uuid.uuid4())

    # 1. Root task (t0)
    assert client.post("/v2/traces", headers=h("tok-write"), json=_trace(
        pid, wid, tid=root_id, root=root_id, sid=sid, step_type="user_task", step_name="Root V2.2.4",
        actor_type="agent", actor_id="lead", status="running", started=t0.isoformat(), used_llm=True,
        model_id="gemini-4-neon-medium", input_summary="Upgrade execution map",
        meta={"source": "antigravity_ide_manual_session", "requested_model": "gemini-4-neon-medium"},
    )).status_code == 201

    # 2 & 3. Two parallel steps with identical timestamp (t0 + 2s)
    t2 = (t0 + timedelta(seconds=2)).isoformat()
    t3 = (t0 + timedelta(seconds=3)).isoformat()
    assert client.post("/v2/traces", headers=h("tok-write"), json=_trace(
        pid, wid, tid=tie_a, parent=root_id, root=root_id, sid=sid, step_type="ai_memory", step_name="Search Memory",
        actor_type="memory", actor_id="ai-memory-bridge", status="succeeded", started=t2, ended=t3,
        used_memory=True, used_mcp=True, used_llm=False, output_summary="Found 5 verified records",
        meta={"source": "bridge.search_memory", "why": "Lookup V2.2.3 decisions"},
    )).status_code == 201

    assert client.post("/v2/traces", headers=h("tok-write"), json=_trace(
        pid, wid, tid=tie_b, parent=root_id, root=root_id, sid=sid, step_type="code_search", step_name="Search Code",
        actor_type="mcp", actor_id="ai-memory-codebase", status="failed", started=t2, ended=t3,
        used_mcp=True, used_llm=False, error_code="http_503", error_summary="Codebase unavailable",
        meta={"source": "codebase.search_code"},
    )).status_code == 201

    # 4. Retry of tie_b (t0 + 5s)
    assert client.post("/v2/traces", headers=h("tok-write"), json=_trace(
        pid, wid, tid=retry_id, parent=root_id, root=root_id, sid=sid, step_type="code_search", step_name="Search Code Retry",
        actor_type="mcp", actor_id="ai-memory-codebase", status="succeeded",
        started=(t0 + timedelta(seconds=5)).isoformat(), ended=(t0 + timedelta(seconds=6)).isoformat(),
        used_mcp=True, used_llm=False, output_summary="Returned 2 symbols",
        meta={"source": "codebase.search_code", "retry_of": tie_b},
    )).status_code == 201

    # 5. Late-arriving trace with earlier timestamp (t0 + 1s)
    assert client.post("/v2/traces", headers=h("tok-write"), json=_trace(
        pid, wid, tid=late_id, parent=root_id, root=root_id, sid=sid, step_type="qa_test", step_name="Early QA",
        actor_type="qa", actor_id="pytest", status="succeeded",
        started=(t0 + timedelta(seconds=1)).isoformat(), ended=(t0 + timedelta(seconds=1, milliseconds=500)).isoformat(),
        used_llm=False, output_summary="34 passed",
        meta={"source": "runner.qa"},
    )).status_code == 201

    # Query full list
    r = client.get("/v2/traces", params={"project_id": pid, "sort": "time_asc"}, headers=h("tok-read"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["total"] == 5
    by_id = {it["trace_id"]: it for it in data["items"]}

    # Global sequence reflects observed started_at, NOT ingestion order:
    # #1 root (t0), #2 late_id (t0+1s), #3/#4 tie_a & tie_b (t0+2s), #5 retry_id (t0+5s)
    assert by_id[root_id]["execution_sequence"] == 1
    assert by_id[late_id]["execution_sequence"] == 2
    assert by_id[late_id]["late_arrival"] is True
    assert {by_id[tie_a]["execution_sequence"], by_id[tie_b]["execution_sequence"]} == {3, 4}
    assert by_id[tie_a]["timestamp_tie"] is True
    assert by_id[tie_b]["timestamp_tie"] is True
    assert by_id[retry_id]["execution_sequence"] == 5
    assert by_id[retry_id]["relationships"]["retry_of"] == tie_b

    # Pagination & filter MUST NOT renumber execution_sequence
    r_filt = client.get("/v2/traces", params={"project_id": pid, "status": "failed"}, headers=h("tok-read"))
    assert r_filt.status_code == 200
    assert len(r_filt.json()["items"]) == 1
    assert r_filt.json()["items"][0]["execution_sequence"] == by_id[tie_b]["execution_sequence"]

    # Token status & model provenance checks
    assert by_id[tie_a]["token_status"] == "not_applicable"
    assert by_id[root_id]["token_status"] == "pending"
    assert by_id[root_id]["model_provenance"]["requested_model"] == "gemini-4-neon-medium"
    assert by_id[root_id]["model_provenance"]["client_declared_model"] == "gemini-4-neon-medium"
    assert by_id[root_id]["model_provenance"]["provider_reported_model"] is None
    assert by_id[tie_a]["data_source_kind"] == "automatically_observed"
    assert by_id[root_id]["data_source_kind"] == "manually_reported"

    # Multi-level summary checks (no fabricated progress_pct without plan)
    summary = data["summary"]
    assert summary["observed_steps"] == 5
    assert summary["progress_pct"] is None
    assert summary["overall_status"] == "running"
    assert summary["status_counts"]["failed"] == 1

    groups = {g["lane"]: g for g in data["groups"]}
    assert groups["ai_memory"]["steps"] == 1
    assert groups["codebase"]["steps"] == 2
    assert groups["qa"]["steps"] == 1

    # Export JSON & CSV
    exp_json = client.get("/v2/traces/export", params={"project_id": pid, "format": "json"}, headers=h("tok-read"))
    assert exp_json.status_code == 200 and exp_json.json()["count"] == 5
    exp_csv = client.get("/v2/traces/export", params={"project_id": pid, "format": "csv"}, headers=h("tok-read"))
    assert exp_csv.status_code == 200 and "execution_sequence,trace_id" in exp_csv.text

    # Telemetry inspector
    insp = client.get("/v2/traces/telemetry/inspector", params={"project_id": pid}, headers=h("tok-read"))
    assert insp.status_code == 200
    insp_data = insp.json()
    assert len(insp_data["client_capabilities"]) >= 4
    assert any(s["session_id"] == sid and s["telemetry_status"] == "pending" for s in insp_data["sessions"])
