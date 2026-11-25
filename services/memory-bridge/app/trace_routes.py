"""Scoped, authenticated execution trace ingestion and read API."""
import csv
import io
import json
import os
import hmac
from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from .trace_schema import TraceEvent
from .trace_metrics import summarize
from .trace_summary import (
    build_group_summaries,
    build_project_summary,
    build_telemetry_inspector,
    compute_global_sequence_map,
    enrich_trace_items,
)
from .security import authenticate_token, extract_bearer_token, require_any_scope, require_scope, redact_payload, compute_payload_hash
from .errors import BridgeError

def create_trace_router(db, cfg):
    router = APIRouter(prefix="/v2/traces", tags=["execution traces"])

    def principal(request: Request):
        return authenticate_token(extract_bearer_token(request.headers.get("Authorization")), cfg)

    def reader(p=Depends(principal)):
        require_scope(p, "read")
        return p

    def writer(request: Request):
        token = extract_bearer_token(request.headers.get("Authorization"))
        dedicated = os.environ.get("BRIDGE_TRACE_TOKEN")
        if token and dedicated and hmac.compare_digest(token,dedicated):
            return "trace_only"
        p = authenticate_token(token,cfg)
        require_any_scope(p,"write","planner","runner","worker")
        return p

    def check_scope(conn, project, worktree=None, plan_id=None, task_id=None, attempt_id=None, session_id=None):
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
            raise BridgeError(404, "project_not_found", "Project not found")
        if worktree and not conn.execute("SELECT 1 FROM worktrees WHERE id=? AND project_id=?", (worktree, project)).fetchone():
            raise BridgeError(404, "worktree_not_found", "Worktree not found")
        if plan_id and not conn.execute("SELECT 1 FROM v2_plans WHERE id=? AND project_id=?", (plan_id, project)).fetchone():
            raise BridgeError(404, "plan_not_found", "Plan not found in project scope")
        if task_id:
            trow = conn.execute("SELECT plan_id FROM v2_tasks WHERE id=? AND project_id=?", (task_id, project)).fetchone()
            if not trow:
                raise BridgeError(404, "task_not_found", "Task not found in project scope")
            if plan_id and trow["plan_id"] != plan_id:
                raise BridgeError(422, "scope_mismatch", "Task does not belong to specified plan")
        if attempt_id:
            arow = conn.execute(
                "SELECT a.task_id, t.plan_id FROM v2_task_attempts a JOIN v2_tasks t ON t.id=a.task_id WHERE a.id=? AND t.project_id=?",
                (attempt_id, project),
            ).fetchone()
            if not arow:
                raise BridgeError(404, "attempt_not_found", "Attempt not found in project scope")
            if task_id and arow["task_id"] != task_id:
                raise BridgeError(422, "scope_mismatch", "Attempt does not belong to specified task")
            if plan_id and arow["plan_id"] != plan_id:
                raise BridgeError(422, "scope_mismatch", "Attempt does not belong to specified plan")
        if session_id and not conn.execute("SELECT 1 FROM v2_sessions WHERE id=? AND project_id=?", (session_id, project)).fetchone():
            raise BridgeError(404, "session_not_found", "Session not found in project scope")

    def decode(row):
        item = dict(row)
        item.pop("payload_hash", None)
        item["metadata_json"] = json.loads(item["metadata_json"])
        for key in ("used_memory","used_mcp","used_llm"):
            if item[key] is not None:
                item[key] = bool(item[key])
        return item

    @router.post("", status_code=201)
    def ingest(req: TraceEvent, request: Request, p=Depends(writer)):
        if req.step_type == "session_usage":
            raise BridgeError(403,"trace_role","Session usage is projected from V2 usage evidence")
        data = redact_payload(req.model_dump(mode="json"))
        # Normalize UTC representation consistently for lexicographic time filtering.
        for key in ("started_at","ended_at"):
            val = getattr(req,key)
            data[key] = val.isoformat(timespec="microseconds") if val else None
        project, worktree = data["project_id"], data["worktree_id"]
        with db.write_tx() as conn:
            check_scope(conn, project, worktree)
            if getattr(p,"role",None) == "worker":
                if data["actor_id"] != "hermes" or data["step_type"] not in ("llm_call","local_rule") or any(data[k] is not None for k in ("plan_id","task_id","attempt_id","session_id","parent_trace_id")):
                    raise BridgeError(403,"trace_role","Worker trace types only")
                event_id = data["metadata_json"].get("evidence_ref")
                if not event_id or not conn.execute("SELECT 1 FROM events WHERE id=? AND project_id=? AND worktree_id=? AND approved=1",(event_id,project,worktree)).fetchone():
                    raise BridgeError(403,"trace_role","Approved event required")
            if p == "trace_only":
                # Adapter may correlate only to an existing parent in its registered scope.
                if data["step_type"] not in ("mcp_tool_call","local_rule") or data["actor_id"] != "codebase-adapter":
                    raise BridgeError(403,"trace_role","Adapter trace types only")
                if any(data[k] is not None for k in ("parent_trace_id","plan_id","task_id","attempt_id","session_id","input_tokens","output_tokens","cache_read_tokens","cache_write_tokens","reasoning_tokens","cost_usd")):
                    raise BridgeError(403,"trace_role","Adapter cannot supply linked records or usage")
                hint = request.headers.get("X-Trace-Parent")
                if hint:
                    parent = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND project_id=? AND worktree_id=?", (hint,project,worktree)).fetchone()
                    if parent:
                        data["parent_trace_id"] = parent["trace_id"]
                        data["root_trace_id"] = parent["root_trace_id"]
                        for key in ("plan_id","task_id","attempt_id","session_id"):
                            data[key] = parent[key]
            for field, table in (("plan_id","v2_plans"),("task_id","v2_tasks"),("session_id","v2_sessions")):
                if data[field]:
                    record = conn.execute(f"SELECT * FROM {table} WHERE id=? AND project_id=? AND worktree_id=?", (data[field],project,worktree)).fetchone()
                    if not record:
                        raise BridgeError(422,"trace_scope","Linked record outside trace scope")
                    for key in ("plan_id","task_id","attempt_id"):
                        if key in record.keys() and data[key] and record[key] != data[key]:
                            raise BridgeError(422,"trace_link","Inconsistent linked record")
            if data["attempt_id"]:
                record = conn.execute("SELECT a.task_id,t.plan_id FROM v2_task_attempts a JOIN v2_tasks t ON t.id=a.task_id WHERE a.id=? AND t.project_id=? AND t.worktree_id=?", (data["attempt_id"],project,worktree)).fetchone()
                if not record or any(data[k] and data[k]!=record[k] for k in ("task_id","plan_id")):
                    raise BridgeError(422,"trace_scope","Attempt outside trace scope")
            if data["parent_trace_id"]:
                parent = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=?", (data["parent_trace_id"],)).fetchone()
                if not parent or any(parent[k]!=data[k] for k in ("root_trace_id","project_id","worktree_id")):
                    raise BridgeError(422,"trace_parent","Parent must exist in same root and scope")
                for key in ("plan_id","task_id","attempt_id","session_id"):
                    if parent[key] and parent[key]!=data[key]:
                        raise BridgeError(422,"trace_parent","Parent context mismatch")
            data["metadata_json"]["source"] = str(data["metadata_json"]["source"])
            digest = compute_payload_hash(data)
            old = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=?", (data["trace_id"],)).fetchone()
            if old:
                if old["payload_hash"] == digest:
                    return decode(old)
                # Only allow a running span to finish, with unchanged identity/context.
                immutable = ("trace_id","parent_trace_id","root_trace_id","project_id","worktree_id","plan_id","task_id","attempt_id","session_id","step_type","step_name","actor_type","actor_id","tool_name","provider","model_id","started_at")
                if old["status"]!="running" or data["status"]=="running" or any(old[k]!=data[k] for k in immutable):
                    raise BridgeError(409,"trace_conflict","Trace identity or terminal evidence cannot change")
            data["metadata_json"] = json.dumps(data["metadata_json"],ensure_ascii=False)
            data["payload_hash"] = digest
            if old:
                cols = [k for k in data if k!="trace_id"]
                conn.execute("UPDATE v22_execution_traces SET "+",".join(k+"=?" for k in cols)+" WHERE trace_id=?", [data[k] for k in cols]+[data["trace_id"]])
            else:
                cols = list(data)
                conn.execute("INSERT INTO v22_execution_traces ("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")", [data[k] for k in cols])
            return decode(conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=?", (data["trace_id"],)).fetchone())

    @router.get("/scope-options")
    def scope_options(
        project_id: UUID,
        plan_id: UUID | None = None,
        task_id: UUID | None = None,
        p=Depends(reader),
    ):
        """P6A cascading scope options: Project -> Plan -> Task -> Attempt (real IDs, titles, status, timestamps)."""
        project = str(project_id)
        with db.read_conn() as conn:
            check_scope(conn, project, plan_id=str(plan_id) if plan_id else None, task_id=str(task_id) if task_id else None)
            plans = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, objective, status, created_at, updated_at FROM v2_plans WHERE project_id=? ORDER BY updated_at DESC LIMIT 100",
                    (project,),
                ).fetchall()
            ]
            t_sql = "SELECT id, plan_id, task_key, title, state, assigned_agent, created_at, updated_at FROM v2_tasks WHERE project_id=?"
            t_args = [project]
            if plan_id:
                t_sql += " AND plan_id=?"
                t_args.append(str(plan_id))
            t_sql += " ORDER BY created_at DESC LIMIT 200"
            tasks = [dict(r) for r in conn.execute(t_sql, t_args).fetchall()]

            a_sql = "SELECT a.id, a.task_id, t.plan_id, a.attempt_number, a.status, a.started_at, a.ended_at FROM v2_task_attempts a JOIN v2_tasks t ON t.id=a.task_id WHERE t.project_id=?"
            a_args = [project]
            if task_id:
                a_sql += " AND a.task_id=?"
                a_args.append(str(task_id))
            elif plan_id:
                a_sql += " AND t.plan_id=?"
                a_args.append(str(plan_id))
            a_sql += " ORDER BY a.started_at DESC LIMIT 200"
            attempts = [dict(r) for r in conn.execute(a_sql, a_args).fetchall()]
            return {"project_id": project, "plans": plans, "tasks": tasks, "attempts": attempts}

    @router.get("")
    def list_traces(project_id: UUID, worktree_id: UUID | None=None, session_id: UUID | None=None, plan_id: UUID | None=None, task_id: UUID | None=None, attempt_id: UUID | None=None, include_descendants: bool=False, root_trace_id: UUID | None=None, actor_id: str | None=None, model_id: str | None=None, status: str | None=None, from_time: datetime | None=None, to_time: datetime | None=None, limit: int=Query(100,ge=1,le=500), offset: int=Query(0,ge=0), search: str | None=Query(None,max_length=200), sort: str=Query("time_asc", pattern=r"^(time_asc|time_desc|duration_desc|status)$"), p=Depends(reader)):
        project = str(project_id)
        filters = {"project_id":project,"worktree_id":worktree_id,"session_id":session_id,"plan_id":plan_id,"attempt_id":attempt_id,"root_trace_id":root_trace_id,"actor_id":actor_id,"model_id":model_id,"status":status}
        if task_id is not None and not include_descendants:
            filters["task_id"] = task_id
        clauses, args = [], []
        for k,v in filters.items():
            if v is not None:
                clauses.append(k+"=?")
                args.append(str(v))
        if task_id is not None and include_descendants:
            # Explicit parent-child hierarchy ONLY (never misclassify DAG dependencies!)
            clauses.append(
                "(task_id=? OR trace_id IN ("
                "WITH RECURSIVE desc_t(tid) AS ("
                "  SELECT trace_id FROM v22_execution_traces WHERE project_id=? AND task_id=? "
                "  UNION ALL "
                "  SELECT c.trace_id FROM v22_execution_traces c JOIN desc_t d ON c.parent_trace_id = d.tid WHERE c.project_id=?"
                ") SELECT tid FROM desc_t))"
            )
            args.extend([str(task_id), project, str(task_id), project])
        for value, operator in ((from_time,">="),(to_time,"<=")):
            if value is not None:
                if value.utcoffset() is None:
                    raise BridgeError(422,"trace_time","Timezone required")
                clauses.append("started_at"+operator+"?")
                args.append(value.astimezone(timezone.utc).isoformat(timespec="microseconds"))
        if search:
            clauses.append("(instr(lower(step_name),lower(?))>0 OR instr(lower(coalesce(actor_id,'')),lower(?))>0 OR instr(lower(coalesce(tool_name,'')),lower(?))>0 OR instr(lower(step_type),lower(?))>0 OR instr(lower(coalesce(model_id,'')),lower(?))>0)")
            args.extend([search]*5)
        order = {"time_asc":"started_at,trace_id","time_desc":"started_at DESC,trace_id","duration_desc":"duration_ms IS NULL,duration_ms DESC,trace_id","status":"status,started_at,trace_id"}[sort]
        where = " AND ".join(clauses)
        with db.read_conn() as conn:
            check_scope(
                conn,
                project,
                str(worktree_id) if worktree_id else None,
                plan_id=str(plan_id) if plan_id else None,
                task_id=str(task_id) if task_id else None,
                attempt_id=str(attempt_id) if attempt_id else None,
                session_id=str(session_id) if session_id else None,
            )
            total = conn.execute("SELECT COUNT(*) FROM v22_execution_traces WHERE "+where,args).fetchone()[0]
            rows = conn.execute("SELECT * FROM v22_execution_traces WHERE "+where+" ORDER BY "+order+" LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
            raw_items = [decode(r) for r in rows]
            # Bound aggregate scans; label partial results explicitly.
            sample_rows = conn.execute("SELECT * FROM v22_execution_traces WHERE "+where+" ORDER BY started_at,trace_id LIMIT 5000",args).fetchall()
            sample = [decode(r) for r in sample_rows]
            metrics = summarize(sample)
            metrics["partial"] = total > len(sample)

            seq_map, ing_map, tie_ts = compute_global_sequence_map(conn, project)
            summary, planned_steps, dep_edges = build_project_summary(
                conn,
                project_id=project,
                worktree_id=str(worktree_id) if worktree_id else None,
                plan_id=str(plan_id) if plan_id else None,
                session_id=str(session_id) if session_id else None,
                rows=sample,
                metrics=metrics,
            )
            enriched_sample = enrich_trace_items(sample, seq_map, ing_map, tie_ts, dep_edges)
            items = enrich_trace_items(raw_items, seq_map, ing_map, tie_ts, dep_edges)
            for it in items:
                if not it.get("task_id") and not it.get("plan_id"):
                    it["task_link_label"] = "Chưa liên kết nhiệm vụ"
            unlinked_count = conn.execute("SELECT COUNT(*) FROM v22_execution_traces WHERE project_id=? AND task_id IS NULL AND plan_id IS NULL", (project,)).fetchone()[0]
            groups = build_group_summaries(enriched_sample)
            return {
                "items": items,
                "total": total,
                "next_offset": offset + limit if offset + limit < total else None,
                "metrics": metrics,
                "summary": summary,
                "groups": groups,
                "planned_steps": planned_steps,
                "scope": {
                    "project_id": project,
                    "worktree_id": str(worktree_id) if worktree_id else None,
                    "plan_id": str(plan_id) if plan_id else None,
                    "task_id": str(task_id) if task_id else None,
                    "attempt_id": str(attempt_id) if attempt_id else None,
                    "include_descendants": include_descendants,
                },
                "unlinked_project_traces": unlinked_count,
            }

    @router.get("/export")
    def export_traces(
        project_id: UUID,
        worktree_id: UUID | None = None,
        session_id: UUID | None = None,
        plan_id: UUID | None = None,
        task_id: UUID | None = None,
        attempt_id: UUID | None = None,
        include_descendants: bool = False,
        status: str | None = None,
        fmt: str = Query("json", alias="format", pattern=r"^(json|csv)$"),
        limit: int = Query(500, ge=1, le=2000),
        p=Depends(reader),
    ):
        project = str(project_id)
        filters = {"project_id": project, "worktree_id": worktree_id, "session_id": session_id, "plan_id": plan_id, "attempt_id": attempt_id, "status": status}
        if task_id is not None and not include_descendants:
            filters["task_id"] = task_id
        clauses, args = [], []
        for k, v in filters.items():
            if v is not None:
                clauses.append(k + "=?")
                args.append(str(v))
        if task_id is not None and include_descendants:
            clauses.append(
                "(task_id=? OR trace_id IN ("
                "WITH RECURSIVE desc_t(tid) AS ("
                "  SELECT trace_id FROM v22_execution_traces WHERE project_id=? AND task_id=? "
                "  UNION ALL "
                "  SELECT c.trace_id FROM v22_execution_traces c JOIN desc_t d ON c.parent_trace_id = d.tid WHERE c.project_id=?"
                ") SELECT tid FROM desc_t))"
            )
            args.extend([str(task_id), project, str(task_id), project])
        where = " AND ".join(clauses)
        with db.read_conn() as conn:
            check_scope(
                conn,
                project,
                str(worktree_id) if worktree_id else None,
                plan_id=str(plan_id) if plan_id else None,
                task_id=str(task_id) if task_id else None,
                attempt_id=str(attempt_id) if attempt_id else None,
                session_id=str(session_id) if session_id else None,
            )
            rows = conn.execute("SELECT * FROM v22_execution_traces WHERE " + where + " ORDER BY started_at,trace_id LIMIT ?", args + [limit]).fetchall()
            raw_items = [decode(r) for r in rows]
            seq_map, ing_map, tie_ts = compute_global_sequence_map(conn, project)
            items = enrich_trace_items(raw_items, seq_map, ing_map, tie_ts, {})
        if fmt == "csv":
            buf = io.StringIO()
            cols = ["execution_sequence", "trace_id", "step_name", "step_type", "lane", "actor_id", "provider", "model_id", "status", "started_at", "ended_at", "duration_ms", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "token_status"]
            writer_csv = csv.DictWriter(buf, fieldnames=cols)
            writer_csv.writeheader()
            for it in items:
                writer_csv.writerow({c: it.get(c) for c in cols})
            return Response(content=buf.getvalue(), media_type="text/csv; charset=utf-8")
        return {"project_id": project, "count": len(items), "items": items}

    @router.get("/telemetry/inspector")
    def telemetry_inspector(project_id: UUID, worktree_id: UUID | None = None, p=Depends(reader)):
        project = str(project_id)
        with db.read_conn() as conn:
            check_scope(conn, project, str(worktree_id) if worktree_id else None)
            return build_telemetry_inspector(conn, project, str(worktree_id) if worktree_id else None)

    @router.get("/{trace_id}")
    def detail(trace_id: UUID, project_id: UUID, p=Depends(reader)):
        with db.read_conn() as conn:
            row = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND project_id=?", (str(trace_id),str(project_id))).fetchone()
            if not row:
                raise BridgeError(404,"trace_not_found","Trace not found")
            return decode(row)
    return router
