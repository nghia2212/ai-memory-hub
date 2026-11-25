"""Observed Memory Bridge operations; no prompt/body/source code is persisted."""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from uuid import UUID, uuid4
import logging
from .trace_lifecycle import _insert
from .errors import BridgeError

trace_parent = ContextVar("trace_parent", default=None)
trace_transport = ContextVar("trace_transport", default="http")
log = logging.getLogger(__name__)

def observe_operation(name, kind, *, memory):
    def decorate(fn):
        sig = signature(fn)
        @wraps(fn)
        def wrapped(self, *args, **kwargs):
            bound = sig.bind(self, *args, **kwargs).arguments
            req = bound.get("req")
            pid = getattr(req, "project_id", None) if req else bound.get("project_id")
            wid = getattr(req, "worktree_id", None) if req else bound.get("worktree_id")
            if not pid or not wid:
                return fn(self, *args, **kwargs)
            pid, wid = str(pid), str(wid)
            started = datetime.now(timezone.utc)
            result, error = None, None
            try:
                result = fn(self, *args, **kwargs)
                return result
            except Exception as exc:
                error = exc
                raise
            finally:
                try:
                    with self.db.write_tx() as conn:
                        if conn.execute("SELECT 1 FROM worktrees WHERE id=? AND project_id=?", (wid,pid)).fetchone():
                            ident = str(uuid4())
                            parent = None
                            candidate = trace_parent.get()
                            if candidate:
                                try:
                                    candidate = str(UUID(candidate))
                                except (ValueError,TypeError):
                                    candidate = None
                                if candidate:
                                    parent = conn.execute("SELECT * FROM v22_execution_traces WHERE trace_id=? AND project_id=? AND worktree_id=?", (candidate,pid,wid)).fetchone()
                            actual_name = name
                            if name == "save_memory":
                                actual_name = {"decision":"save_decision","experience":"save_lesson"}.get(getattr(req,"type",None),name)
                            transport = trace_transport.get()
                            mcp = transport == "mcp"
                            data = result[1] if isinstance(result,tuple) else result
                            meta = {"source":"bridge."+actual_name,"why":"Observed "+transport+" operation"}
                            summary = None
                            if isinstance(data,dict):
                                for key in ("memory_id","handoff_id"):
                                    if data.get(key):
                                        meta[key] = str(data[key])[:1000]
                                if isinstance(data.get("items"),list):
                                    summary = "Returned "+str(len(data["items"]))+" items"
                                if data.get("status") and name == "reindex":
                                    summary = "Index request status: "+str(data["status"])[:100]
                            # Reindex request acceptance is not proof that indexing finished.
                            status = "failed" if error else ("unknown" if name=="reindex" else "succeeded")
                            _insert(conn, trace_id=ident, root_trace_id=parent["root_trace_id"] if parent else ident,
                                    parent_trace_id=parent["trace_id"] if parent else None,
                                    project_id=pid, worktree_id=wid,
                                    **{k:parent[k] if parent else None for k in ("plan_id","task_id","attempt_id","session_id")},
                                    step_type=kind, step_name=actual_name, actor_type="memory" if memory else ("mcp" if mcp else "system"),
                                    tool_name=actual_name if mcp else None, used_memory=memory,
                                    used_mcp=True if mcp else (False if memory else None), used_llm=False if memory else None,
                                    status=status, started_at=started, ended_at=datetime.now(timezone.utc),
                                    output_summary=summary, error_code=(error.code if isinstance(error,BridgeError) else type(error).__name__) if error else None,
                                    metadata_json=meta)
                except Exception:
                    # Trace persistence must not change a completed operation's outcome.
                    log.warning("Trace persistence failed for %s",name)
        return wrapped
    return decorate
