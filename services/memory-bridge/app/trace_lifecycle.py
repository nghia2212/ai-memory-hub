"""Transactional trace instrumentation for persisted V2 planner and runner lifecycle.

Only records observed Bridge operations. A lease is not evidence of model execution.
"""
from datetime import datetime, timezone
from uuid import UUID, uuid5
import json
from .security import redact_payload, compute_payload_hash
from .trace_schema import TraceEvent

_NAMESPACE = UUID("52c766ac-2c6b-4d30-9d9e-0c9d308d450c")

def trace_key(kind, ident):
    return str(uuid5(_NAMESPACE, kind + ":" + str(ident)))

def _utc(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc).isoformat(timespec="microseconds")

def _insert(conn, **kwargs):
    event = TraceEvent(**kwargs)
    data = redact_payload(event.model_dump(mode="json"))
    for key in ("started_at", "ended_at"):
        if data[key]:
            data[key] = _utc(data[key])
    data["payload_hash"] = compute_payload_hash(data)
    data["metadata_json"] = json.dumps(data["metadata_json"], ensure_ascii=False)
    cols = list(data)
    conn.execute("INSERT INTO v22_execution_traces (" + ",".join(cols) + ") VALUES (" + ",".join("?" for _ in cols) + ")", [data[k] for k in cols])

def plan_created(conn, *, plan_id, project_id, worktree_id, actor, started_at, ended_at, task_count):
    root = trace_key("plan", plan_id)
    _insert(conn, trace_id=root, root_trace_id=root, project_id=project_id,
            worktree_id=worktree_id, plan_id=plan_id, step_type="planner",
            step_name="validate_and_persist_plan", actor_type="planner", actor_id=actor,
            used_llm=False, used_mcp=False, used_memory=False, status="succeeded",
            started_at=started_at, ended_at=ended_at,
            output_summary=f"Persisted plan with {task_count} tasks",
            metadata_json={"source":"bridge.create_plan","why":"Validate DAG and persist plan revision; upstream planning is not observed"})

def agent_claimed(conn, *, task, attempt_id, session_id, runner_id, started_at, attempt_number):
    root = trace_key("plan", task["plan_id"])
    ident = trace_key("attempt", attempt_id)
    parent = conn.execute("SELECT trace_id FROM v22_execution_traces WHERE trace_id=? AND project_id=? AND worktree_id=?", (root,task["project_id"],task["worktree_id"])).fetchone()
    # Legacy plans have no observed plan creation trace; do not invent one.
    metadata = {"source":"bridge.claim_job","why":"Runner acquired a lease; actual CLI/model execution not yet verified"}
    if attempt_number > 1:
        previous = conn.execute("SELECT id FROM v2_task_attempts WHERE task_id=? AND attempt_number=?", (task["id"],attempt_number-1)).fetchone()
        if previous:
            prev = trace_key("attempt",previous["id"])
            if conn.execute("SELECT 1 FROM v22_execution_traces WHERE trace_id=?", (prev,)).fetchone():
                metadata["retry_of"] = prev
    _insert(conn, trace_id=ident, root_trace_id=root if parent else ident,
            parent_trace_id=root if parent else None, project_id=task["project_id"],
            worktree_id=task["worktree_id"], plan_id=task["plan_id"], task_id=task["id"],
            attempt_id=attempt_id, session_id=session_id, step_type="agent_step",
            step_name="agent_attempt_lifecycle", actor_type="agent", actor_id=runner_id,
            status="running", started_at=started_at, metadata_json=metadata)

def agent_heartbeat(conn, attempt_id, now):
    # Only heartbeat evidence, no inference about tool or model usage.
    ident = trace_key("attempt", attempt_id)
    row = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND status='running'", (ident,)).fetchone()
    if row:
        data = dict(row)
        metadata = json.loads(data["metadata_json"])
        metadata["coverage"] = "Runner heartbeat observed at " + _utc(now)
        data["metadata_json"] = metadata
        data.pop("payload_hash",None)
        for k in ("used_memory","used_mcp","used_llm"):
            if data[k] is not None:
                data[k] = bool(data[k])
        conn.execute("UPDATE v22_execution_traces SET metadata_json=?, payload_hash=? WHERE trace_id=?", (json.dumps(metadata,ensure_ascii=False),compute_payload_hash(data),ident))

def agent_finished(conn, attempt_id, status, now, error_code=None, error_summary=None):
    ident = trace_key("attempt", attempt_id)
    row = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND status='running'", (ident,)).fetchone()
    if not row:
        return
    data = dict(row)
    data.pop("payload_hash",None)
    data["metadata_json"] = json.loads(data["metadata_json"])
    for k in ("used_memory","used_mcp","used_llm"):
        if data[k] is not None:
            data[k] = bool(data[k])
    data.update(status=status, ended_at=_utc(now),
                duration_ms=max(0,int((datetime.fromisoformat(now)-datetime.fromisoformat(row["started_at"])).total_seconds()*1000)),
                error_code=redact_payload(error_code),error_summary=redact_payload(error_summary))
    conn.execute("UPDATE v22_execution_traces SET status=?, ended_at=?, duration_ms=?, error_code=?, error_summary=?, payload_hash=? WHERE trace_id=?",
                 (data["status"],data["ended_at"],data["duration_ms"],data["error_code"],data["error_summary"],compute_payload_hash(data),ident))
