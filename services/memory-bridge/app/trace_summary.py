"""V2.2.4 deterministic execution sequence, multi-level summaries, relationships and telemetry coverage (0 LLM)."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

LANE_ORDER: tuple[str, ...] = (
    "user",
    "planner",
    "ai_memory",
    "mcp",
    "codebase",
    "agent",
    "model",
    "qa",
    "git",
    "system",
    "error",
)

LANE_LABELS: dict[str, str] = {
    "user": "User",
    "planner": "Planner",
    "ai_memory": "AI Memory",
    "mcp": "MCP",
    "codebase": "Codebase",
    "agent": "Agent",
    "model": "Model / LLM",
    "qa": "QA / Test",
    "git": "Git Delivery",
    "system": "Local / System",
    "error": "Error / Blocker",
}

NON_LLM_STEP_TYPES = frozenset({"ai_memory", "save_memory", "code_search", "local_rule", "handoff", "qa_test"})


def _short(text: str | None, limit: int = 180) -> str | None:
    if not text:
        return None
    s = " ".join(str(text).split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def classify_lane(row: dict[str, Any]) -> str:
    st = row.get("step_type") or ""
    at = row.get("actor_type") or ""
    name_l = str(row.get("step_name") or "").lower()
    if st == "error":
        return "error"
    if st == "code_search":
        return "codebase"
    if st in {"ai_memory", "save_memory", "handoff"}:
        return "ai_memory"
    if st == "mcp_tool_call":
        return "mcp"
    if st in {"llm_call", "session_usage"}:
        return "model"
    if st == "qa_test" or at == "qa":
        return "qa"
    if st == "planner" or at == "planner":
        return "planner"
    if st == "user_task" and at == "user":
        return "user"
    if any(k in name_l for k in ("git ", "commit", "push", "draft pr", "pull request")) and at in {"system", "agent"}:
        return "git"
    if at == "memory":
        return "ai_memory"
    if at == "mcp":
        return "mcp"
    if at == "model":
        return "model"
    if at == "agent":
        return "agent"
    if at == "user":
        return "user"
    return "system"


def classify_token_status(row: dict[str, Any]) -> str:
    meta = row.get("metadata_json") or {}
    mk = str(meta.get("measurement_kind") or "").lower()
    has_val = any(
        row.get(k) is not None
        for k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens")
    )
    if has_val and mk in {"measured", ""}:
        return "measured"
    if mk == "estimated":
        return "estimated"
    if mk == "collection_error" or str(row.get("error_code") or "").startswith("telemetry_"):
        return "collection_error"
    if row.get("status") == "running" and row.get("used_llm") is True:
        return "pending"
    if row.get("used_llm") is False or (row.get("step_type") in NON_LLM_STEP_TYPES and row.get("used_llm") is not True):
        return "not_applicable"
    if row.get("status") == "unsupported" or mk in {"unsupported", "unavailable"}:
        return "unsupported"
    return "unknown"


def extract_model_provenance(row: dict[str, Any]) -> dict[str, str | None]:
    meta = row.get("metadata_json") or {}
    requested = meta.get("requested_model")
    reported = meta.get("reported_model")
    # Never treat agent-declared model_id as provider-verified reported_model.
    declared = row.get("model_id")
    return {
        "requested_model": str(requested) if requested else None,
        "client_declared_model": str(declared) if declared else None,
        "provider_reported_model": str(reported) if reported else None,
    }


def classify_data_source(row: dict[str, Any]) -> str:
    src = str((row.get("metadata_json") or {}).get("source") or "").lower()
    if src.startswith(("bridge.", "codebase.", "adapter.")):
        return "automatically_observed"
    if any(k in src for k in ("collector", "cli", "hermes", "runner")):
        return "client_reported"
    if any(k in src for k in ("manual", "antigravity_ide", "ide")):
        return "manually_reported"
    if src:
        return "derived"
    return "unknown"


def build_step_summary(row: dict[str, Any], token_status: str) -> dict[str, Any]:
    meta = row.get("metadata_json") or {}
    purpose = _short(meta.get("why") or row.get("input_summary"))
    if row.get("status") in ("failed", "blocked"):
        result = _short(row.get("error_summary") or row.get("error_code") or row.get("output_summary"))
    else:
        result = _short(row.get("output_summary"))
    return {
        "purpose": purpose,
        "result": result,
        "token_status": token_status,
        "source": meta.get("source"),
    }


def compute_global_sequence_map(conn: Any, project_id: str) -> tuple[dict[str, int], dict[str, int], set[str]]:
    """Compute stable project-wide execution sequence independent of pagination or active filters.

    Returns:
      - seq_map: trace_id -> 1-based execution_sequence ordered by (started_at, trace_id)
      - ingestion_map: trace_id -> 1-based rowid ingestion order
      - tie_timestamps: set of started_at values shared by >= 2 traces
    """
    all_rows = conn.execute(
        "SELECT rowid, trace_id, started_at FROM v22_execution_traces WHERE project_id = ? ORDER BY started_at ASC, trace_id ASC",
        (project_id,),
    ).fetchall()
    seq_map: dict[str, int] = {}
    ts_counts: dict[str, int] = defaultdict(int)
    for idx, r in enumerate(all_rows, start=1):
        seq_map[r["trace_id"]] = idx
        ts_counts[r["started_at"]] += 1
    tie_ts = {ts for ts, cnt in ts_counts.items() if cnt > 1}

    by_ingest = sorted(all_rows, key=lambda r: r["rowid"])
    ingestion_map = {r["trace_id"]: idx for idx, r in enumerate(by_ingest, start=1)}
    return seq_map, ingestion_map, tie_ts


def enrich_trace_items(
    items: list[dict[str, Any]],
    seq_map: dict[str, int],
    ingestion_map: dict[str, int],
    tie_timestamps: set[str],
    dep_edges_by_task: dict[str, list[str]],
) -> list[dict[str, Any]]:
    task_to_traces: dict[str, list[str]] = defaultdict(list)
    for r in items:
        if r.get("task_id"):
            task_to_traces[r["task_id"]].append(r["trace_id"])

    enriched: list[dict[str, Any]] = []
    for r in items:
        tid = r["trace_id"]
        seq = seq_map.get(tid)
        ing = ingestion_map.get(tid)
        ts_tie = r.get("started_at") in tie_timestamps
        late_arrival = bool(seq is not None and ing is not None and ing > seq + 1)
        lane = classify_lane(r)
        tok_status = classify_token_status(r)
        model_prov = extract_model_provenance(r)
        src_kind = classify_data_source(r)
        meta = r.get("metadata_json") or {}

        dep_trace_ids: list[str] = []
        if r.get("task_id") and r["task_id"] in dep_edges_by_task:
            for dep_task_id in dep_edges_by_task[r["task_id"]]:
                dep_trace_ids.extend(task_to_traces.get(dep_task_id, []))

        item = dict(r)
        item["execution_sequence"] = seq
        item["ingestion_sequence"] = ing
        item["order_basis"] = "observed_started_at"
        item["timestamp_tie"] = ts_tie
        item["late_arrival"] = late_arrival
        item["lane"] = lane
        item["token_status"] = tok_status
        item["model_provenance"] = model_prov
        item["data_source_kind"] = src_kind
        item["step_summary"] = build_step_summary(r, tok_status)
        item["relationships"] = {
            "parent_trace_id": r.get("parent_trace_id"),
            "retry_of": meta.get("retry_of"),
            "dependency_trace_ids": sorted(set(dep_trace_ids)),
            "dependency_task_ids": dep_edges_by_task.get(r.get("task_id") or "", []),
        }
        enriched.append(item)
    return enriched


def build_group_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_lane: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_lane[r.get("lane") or classify_lane(r)].append(r)

    groups: list[dict[str, Any]] = []
    for lane in LANE_ORDER:
        lrows = by_lane.get(lane, [])
        durs = [r["duration_ms"] for r in lrows if r.get("duration_ms") is not None]
        latest = max(lrows, key=lambda r: (r.get("started_at") or "", r.get("trace_id") or "")) if lrows else None
        latest_res = None
        if latest:
            ss = latest.get("step_summary") or {}
            latest_res = ss.get("result") or latest.get("step_name")

        tools = sorted({str(r["tool_name"]) for r in lrows if r.get("tool_name")})[:8]
        models = sorted({str(r["model_id"]) for r in lrows if r.get("model_id")})[:6]
        groups.append(
            {
                "lane": lane,
                "label": LANE_LABELS[lane],
                "steps": len(lrows),
                "succeeded": sum(1 for r in lrows if r.get("status") == "succeeded"),
                "failed": sum(1 for r in lrows if r.get("status") == "failed"),
                "blocked": sum(1 for r in lrows if r.get("status") == "blocked"),
                "running": sum(1 for r in lrows if r.get("status") == "running"),
                "retries": sum(1 for r in lrows if (r.get("metadata_json") or {}).get("retry_of")),
                "avg_latency_ms": round(sum(durs) / len(durs), 1) if durs else None,
                "max_latency_ms": max(durs) if durs else None,
                "tools": tools,
                "models": models,
                "latest_result": latest_res,
                "latest_at": latest.get("started_at") if latest else None,
            }
        )
    return groups


def build_project_summary(
    conn: Any,
    *,
    project_id: str,
    worktree_id: str | None,
    plan_id: str | None,
    session_id: str | None,
    rows: list[dict[str, Any]],
    metrics: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, list[str]]]:
    proj = conn.execute("SELECT id, name FROM projects WHERE id = ?", (project_id,)).fetchone()
    wt = (
        conn.execute("SELECT id, branch, head_commit, mount_status FROM worktrees WHERE id = ? AND project_id = ?", (worktree_id, project_id)).fetchone()
        if worktree_id
        else None
    )

    # Load planned tasks & verified DAG dependencies if plan_id is scoped (or latest active plan).
    planned_steps: list[dict[str, Any]] = []
    dep_edges_by_task: dict[str, list[str]] = defaultdict(list)
    observed_task_ids = {r["task_id"] for r in rows if r.get("task_id")}
    target_plan_id = plan_id
    plan_objective = None
    if target_plan_id:
        plan_row = conn.execute("SELECT id, objective, current_revision_id, status FROM v2_plans WHERE id = ? AND project_id = ?", (target_plan_id, project_id)).fetchone()
        if plan_row:
            plan_objective = _short(plan_row["objective"], 240)
            rev_id = plan_row["current_revision_id"]
            if rev_id:
                tasks = conn.execute(
                    "SELECT id, task_key, title, state, assigned_provider, assigned_model_id FROM v2_tasks WHERE revision_id = ? ORDER BY rowid ASC",
                    (rev_id,),
                ).fetchall()
                deps = conn.execute(
                    "SELECT d.task_id, d.depends_on_task_id FROM v2_task_dependencies d JOIN v2_tasks t ON t.id = d.task_id WHERE t.revision_id = ?",
                    (rev_id,),
                ).fetchall()
                for d in deps:
                    dep_edges_by_task[d["task_id"]].append(d["depends_on_task_id"])
                for idx, t in enumerate(tasks, start=1):
                    if t["id"] not in observed_task_ids:
                        planned_steps.append(
                            {
                                "task_id": t["id"],
                                "task_key": t["task_key"],
                                "planned_order": idx,
                                "title": t["title"],
                                "status": "planned",
                                "order_basis": "planned_dag",
                                "assigned_provider": t["assigned_provider"],
                                "assigned_model_id": t["assigned_model_id"],
                                "depends_on_task_ids": dep_edges_by_task.get(t["id"], []),
                            }
                        )

    running_steps = [r for r in rows if r.get("status") == "running"]
    completed_steps = [r for r in rows if r.get("status") == "succeeded"]
    failed_steps = [r for r in rows if r.get("status") == "failed"]
    blocked_steps = [r for r in rows if r.get("status") == "blocked"]

    if running_steps:
        overall_status = "running"
    elif blocked_steps:
        overall_status = "blocked"
    elif failed_steps:
        overall_status = "failed"
    elif completed_steps:
        overall_status = "succeeded"
    else:
        overall_status = "empty"

    latest_running = running_steps[-1] if running_steps else None
    latest_completed = completed_steps[-1] if completed_steps else None
    latest_any = rows[-1] if rows else None

    # Only compute progress % when a reliable planned task denominator exists.
    planned_total = (len(observed_task_ids) + len(planned_steps)) if target_plan_id else None
    progress_pct = None
    if planned_total and planned_total > 0:
        done_tasks = {r["task_id"] for r in completed_steps if r.get("task_id")}
        progress_pct = round(len(done_tasks) * 100.0 / planned_total, 1)

    token_status_counts: dict[str, int] = defaultdict(int)
    source_kind_counts: dict[str, int] = defaultdict(int)
    for r in rows:
        token_status_counts[r.get("token_status") or classify_token_status(r)] += 1
        source_kind_counts[r.get("data_source_kind") or classify_data_source(r)] += 1

    active_agents = sorted({str(r["actor_id"]) for r in rows if r.get("actor_id")})[:10]
    models_seen = sorted({str(r["model_id"]) for r in rows if r.get("model_id")})[:10]
    started_at = min((r["started_at"] for r in rows if r.get("started_at")), default=None)

    summary = {
        "project_id": project_id,
        "project_name": proj["name"] if proj else project_id,
        "worktree_id": worktree_id,
        "worktree_branch": wt["branch"] if wt else None,
        "worktree_head": wt["head_commit"] if wt else None,
        "plan_id": plan_id,
        "session_id": session_id,
        "objective": plan_objective or (_short(rows[0].get("input_summary"), 240) if rows else None),
        "overall_status": overall_status,
        "active_agents": active_agents,
        "models": models_seen,
        "current_step": latest_running["step_name"] if latest_running else None,
        "latest_completed_step": latest_completed["step_name"] if latest_completed else None,
        "latest_result": (
            (latest_any.get("step_summary") or {}).get("result") or latest_any.get("output_summary")
            if latest_any
            else None
        ),
        "observed_steps": len(rows),
        "planned_steps_count": planned_total,
        "progress_pct": progress_pct,
        "status_counts": {
            "succeeded": len(completed_steps),
            "running": len(running_steps),
            "pending_planned": len(planned_steps),
            "failed": len(failed_steps),
            "blocked": len(blocked_steps),
        },
        "started_at": started_at,
        "wall_span_ms": metrics.get("wall_span_ms"),
        "observed_union_ms": metrics.get("observed_union_ms"),
        "summed_step_ms": metrics.get("summed_step_ms"),
        "memory_ops": metrics.get("used_memory", 0),
        "mcp_ops": metrics.get("used_mcp", 0),
        "llm_calls": metrics.get("llm_calls", 0),
        "tokens": {
            "input_tokens": metrics.get("input_tokens"),
            "output_tokens": metrics.get("output_tokens"),
            "cache_read_tokens": metrics.get("cache_read_tokens"),
            "cache_write_tokens": metrics.get("cache_write_tokens"),
            "reasoning_tokens": metrics.get("reasoning_tokens"),
            "cost_usd": metrics.get("cost_usd"),
        },
        "token_status_counts": dict(token_status_counts),
        "data_source_counts": dict(source_kind_counts),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return summary, planned_steps, dict(dep_edges_by_task)


def build_telemetry_inspector(conn: Any, project_id: str, worktree_id: str | None = None) -> dict[str, Any]:
    """Explain exactly why tokens are measured, unsupported, or unknown per session/client."""
    params: list[Any] = [project_id]
    clause = "s.project_id = ?"
    if worktree_id:
        clause += " AND s.worktree_id = ?"
        params.append(worktree_id)

    sessions = conn.execute(
        f"""
        SELECT s.id, s.provider, s.model_id, s.cli_version, s.auth_mode, s.role, s.status,
               s.upstream_session_id, s.started_at, s.ended_at,
               (SELECT COUNT(*) FROM v2_usage_events u WHERE u.session_id = s.id) AS usage_event_count,
               (SELECT MAX(u.observed_at) FROM v2_usage_events u WHERE u.session_id = s.id) AS last_usage_at,
               (SELECT GROUP_CONCAT(DISTINCT u.source) FROM v2_usage_events u WHERE u.session_id = s.id) AS usage_sources,
               (SELECT GROUP_CONCAT(DISTINCT u.measurement_kind) FROM v2_usage_events u WHERE u.session_id = s.id) AS measurement_kinds,
               (SELECT GROUP_CONCAT(DISTINCT u.counter_mode) FROM v2_usage_events u WHERE u.session_id = s.id) AS counter_modes
        FROM v2_sessions s
        WHERE {clause}
        ORDER BY s.started_at DESC
        LIMIT 50
        """,
        params,
    ).fetchall()

    client_capabilities = [
        {
            "client": "Antigravity IDE",
            "provider": "agy",
            "source_path": "~/.gemini/antigravity/brain/<conv-id>/.system_generated/logs/transcript.jsonl",
            "supported_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "model", "created_at"],
            "unsupported_fields": ["cache_write_tokens", "reasoning_tokens", "cost_usd", "provider_reported_model"],
            "counter_mode": "delta",
            "collector_mode": "opt_in_local_host_sync",
        },
        {
            "client": "Claude Code",
            "provider": "claude",
            "source_path": "~/.claude/projects/<slug>/*.jsonl",
            "supported_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "model"],
            "unsupported_fields": ["api_billing_cost_on_subscription"],
            "counter_mode": "delta",
            "collector_mode": "opt_in_local_host_sync",
        },
        {
            "client": "Codex CLI",
            "provider": "codex",
            "source_path": "~/.codex/sessions/**/rollout-*.jsonl",
            "supported_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "model"],
            "unsupported_fields": ["api_billing_cost_on_subscription"],
            "counter_mode": "cumulative",
            "collector_mode": "opt_in_local_host_sync",
        },
        {
            "client": "Gemini CLI",
            "provider": "gemini",
            "source_path": "~/.gemini/tmp/",
            "supported_fields": [],
            "unsupported_fields": ["all_fields (CLI not installed on host)"],
            "counter_mode": "unknown",
            "collector_mode": "unsupported",
        },
    ]

    session_items: list[dict[str, Any]] = []
    for s in sessions:
        cnt = int(s["usage_event_count"] or 0)
        mk = s["measurement_kinds"] or "unavailable"
        if cnt == 0:
            if s["role"] == "manual_cli":
                reason = "Phiên IDE/CLI thủ công chưa đồng bộ usage từ file log cục bộ (chạy Usage Collector để nhập metadata token thực tế)."
                guidance = "Chạy `python services/orchestrator/usage_collector.py --sync` (opt-in) để đồng bộ metadata token cho phiên này."
            else:
                reason = "Chưa có bản ghi usage từ runner/CLI."
                guidance = "Kiểm tra runner telemetry hoặc định dạng JSON output của CLI."
            status = "pending" if s["status"] == "running" else "unknown"
        elif "measured" in mk:
            reason = "Đã thu thập counter đo được thực tế."
            guidance = "Dữ liệu đang được đối chiếu vào session_usage projection."
            status = "measured"
        else:
            reason = f"Client chỉ báo cáo trạng thái '{mk}', không cung cấp số token đo được."
            guidance = "Không tự ước lượng hoặc gán 0."
            status = "unsupported"

        session_items.append(
            {
                "session_id": s["id"],
                "upstream_session_id": s["upstream_session_id"],
                "provider": s["provider"],
                "client_declared_model": s["model_id"],
                "cli_version": s["cli_version"],
                "auth_mode": s["auth_mode"],
                "role": s["role"],
                "session_status": s["status"],
                "telemetry_status": status,
                "usage_event_count": cnt,
                "last_sync_at": s["last_usage_at"],
                "usage_sources": (s["usage_sources"] or "").split(",") if s["usage_sources"] else [],
                "counter_modes": (s["counter_modes"] or "").split(",") if s["counter_modes"] else [],
                "measurement_kinds": mk.split(",") if mk else [],
                "reason": reason,
                "guidance": guidance,
            }
        )

    return {
        "project_id": project_id,
        "worktree_id": worktree_id,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client_capabilities": client_capabilities,
        "sessions": session_items,
    }
