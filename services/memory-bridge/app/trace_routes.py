"""Scoped, authenticated execution trace ingestion and read API."""
import json
import os
import hmac
from uuid import UUID
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Query, Request
from .trace_schema import TraceEvent
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
        require_any_scope(p,"write","planner","runner")
        return p

    def check_scope(conn, project, worktree=None):
        if not conn.execute("SELECT 1 FROM projects WHERE id=?", (project,)).fetchone():
            raise BridgeError(404, "project_not_found", "Project not found")
        if worktree and not conn.execute("SELECT 1 FROM worktrees WHERE id=? AND project_id=?", (worktree, project)).fetchone():
            raise BridgeError(404, "worktree_not_found", "Worktree not found")

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

    @router.get("")
    def list_traces(project_id: UUID, worktree_id: UUID | None=None, session_id: UUID | None=None, plan_id: UUID | None=None, task_id: UUID | None=None, root_trace_id: UUID | None=None, actor_id: str | None=None, model_id: str | None=None, status: str | None=None, from_time: datetime | None=None, to_time: datetime | None=None, limit: int=Query(100,ge=1,le=500), offset: int=Query(0,ge=0), p=Depends(reader)):
        project = str(project_id)
        filters = {"project_id":project,"worktree_id":worktree_id,"session_id":session_id,"plan_id":plan_id,"task_id":task_id,"root_trace_id":root_trace_id,"actor_id":actor_id,"model_id":model_id,"status":status}
        clauses, args = [], []
        for k,v in filters.items():
            if v is not None:
                clauses.append(k+"=?")
                args.append(str(v))
        for value, operator in ((from_time,">="),(to_time,"<=")):
            if value is not None:
                if value.utcoffset() is None:
                    raise BridgeError(422,"trace_time","Timezone required")
                clauses.append("started_at"+operator+"?")
                args.append(value.astimezone(timezone.utc).isoformat(timespec="microseconds"))
        where = " AND ".join(clauses)
        with db.read_conn() as conn:
            check_scope(conn,project,str(worktree_id) if worktree_id else None)
            total = conn.execute("SELECT COUNT(*) FROM v22_execution_traces WHERE "+where,args).fetchone()[0]
            rows = conn.execute("SELECT * FROM v22_execution_traces WHERE "+where+" ORDER BY started_at,trace_id LIMIT ? OFFSET ?",args+[limit,offset]).fetchall()
            return {"items":[decode(r) for r in rows],"total":total,"next_offset":offset+limit if offset+limit<total else None}

    @router.get("/{trace_id}")
    def detail(trace_id: UUID, project_id: UUID, p=Depends(reader)):
        with db.read_conn() as conn:
            row = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND project_id=?", (str(trace_id),str(project_id))).fetchone()
            if not row:
                raise BridgeError(404,"trace_not_found","Trace not found")
            return decode(row)
    return router
