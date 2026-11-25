"""Session usage trace projection, using stored V2 telemetry as canonical evidence.

Never turn a session total into an invented individual model invocation.
"""
from datetime import datetime, timezone
import json
from .trace_lifecycle import trace_key, _utc, _insert
from .security import compute_payload_hash

FIELDS = ("input_tokens","output_tokens","cache_read_tokens","cache_write_tokens","reasoning_tokens","cost_usd")

def sync_usage_trace(conn, session_id):
    sess = conn.execute("SELECT * FROM v2_sessions WHERE id=?", (session_id,)).fetchone()
    if not sess:
        return
    rows = conn.execute("SELECT * FROM v2_usage_events WHERE session_id=? ORDER BY sequence_no, observed_at, id", (session_id,)).fetchall()
    if not rows:
        return
    ident = trace_key("session_usage", session_id)
    attempt = trace_key("attempt",sess["attempt_id"]) if sess["attempt_id"] else None
    parent = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND project_id=? AND worktree_id=? AND session_id=?", (attempt,sess["project_id"],sess["worktree_id"],session_id)).fetchone() if attempt else None
    # Only measured counters contribute to observed totals. A later unmeasured
    # cumulative snapshot invalidates the corresponding prior measured counter.
    counters = {k:None for k in FIELDS}
    coverage = {k:False for k in FIELDS}
    for row in rows:
        for k in FIELDS:
            value = row[k]
            if row["measurement_kind"] != "measured":
                if value is not None or row["counter_mode"] == "cumulative":
                    counters[k] = None
                    coverage[k] = True
                continue
            if row["counter_mode"] == "cumulative":
                counters[k] = value
                coverage[k] = value is None
            elif value is not None:
                if not coverage[k]:
                    counters[k] = (counters[k] or 0) + value
            else:
                coverage[k] = True
                counters[k] = None
    local = sess["provider"]=="local" or sess["model_id"]=="deterministic-rules"
    if local:
        counters = {k:None for k in FIELDS}
    measured = any(v is not None for v in counters.values())
    status = "unsupported" if not measured and not local else "unknown"
    meta = {"source":"bridge.v2_usage_events","why":"Session aggregate only; individual inference count, actual model identity and inference latency not established",
            "coverage":"measured counters; incomplete fields remain unknown" if measured else "unsupported or unmeasured counters",
            "usage_scope":"session_aggregate","measurement_kind":"measured" if measured else "unavailable",
            "evidence_ref":"v2_sessions/"+session_id}
    # observed_at is a report timestamp, not a model start/end timestamp.
    observed = []
    for row in rows:
        try:
            value = datetime.fromisoformat(row["observed_at"])
            if value.utcoffset() is not None:
                observed.append(value.astimezone(timezone.utc))
        except (ValueError,TypeError):
            pass
    at = max(observed).isoformat(timespec="microseconds") if observed else _utc(sess["started_at"])
    old = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=?", (ident,)).fetchone()
    if not old:
        _insert(conn,trace_id=ident,root_trace_id=parent["root_trace_id"] if parent else ident,
                parent_trace_id=parent["trace_id"] if parent else None,
                project_id=sess["project_id"],worktree_id=sess["worktree_id"],
                plan_id=sess["plan_id"],task_id=sess["task_id"],attempt_id=sess["attempt_id"],session_id=session_id,
                step_type="session_usage",step_name="session_usage_report",actor_type="agent",
                provider=sess["provider"],model_id=None,used_llm=False if local else None,
                status=status,started_at=at,metadata_json=meta,**counters)
        return
    data = dict(old)
    data.pop("payload_hash",None)
    data.update(counters,status=status,metadata_json=meta)
    for k in ("used_memory","used_mcp","used_llm"):
        if data[k] is not None:
            data[k] = bool(data[k])
    cols = [*FIELDS,"status","metadata_json","payload_hash"]
    data["payload_hash"] = compute_payload_hash(data)
    data["metadata_json"] = json.dumps(meta,ensure_ascii=False)
    conn.execute("UPDATE v22_execution_traces SET "+",".join(k+"=?" for k in cols)+" WHERE trace_id=?", [data[k] for k in cols]+[ident])
