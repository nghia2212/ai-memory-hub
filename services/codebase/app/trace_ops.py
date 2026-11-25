"""Best-effort scoped trace emission for real Codebase adapter operations."""
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from uuid import uuid4
import logging
import os
import httpx

_scope = ContextVar("codebase_trace_scope", default=None)
log = logging.getLogger(__name__)

async def emit(name, started, error=None, *, mcp=True, parent=None):
    scope = _scope.get()
    url = os.environ.get("BRIDGE_URL")
    token = os.environ.get("BRIDGE_TRACE_TOKEN")
    if not scope or not url or not token:
        return
    ident = str(uuid4())
    payload = dict(trace_id=ident, root_trace_id=ident, project_id=scope[0], worktree_id=scope[1],
                   step_type="mcp_tool_call" if mcp else "local_rule", step_name=name,
                   actor_type="mcp" if mcp else "system", actor_id="codebase-adapter",
                   tool_name=name if mcp else None, used_mcp=mcp, used_memory=False,
                   used_llm=False if not mcp else None,
                   started_at=started.isoformat(), ended_at=datetime.now(timezone.utc).isoformat(),
                   status="failed" if error else "succeeded", error_code=type(error).__name__ if error else None,
                   metadata_json={"source":"codebase.stdio.tools/call" if mcp else "codebase.local",
                                  "why":"Actual upstream MCP invocation" if mcp else "Local directory traversal"})
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.post(url.rstrip("/")+"/v2/traces", json=payload,
                                  headers={"Authorization":"Bearer "+token})
            r.raise_for_status()
    except Exception:
        log.warning("Codebase trace delivery failed: %s", name)

def scoped(fn):
    sig = signature(fn)
    @wraps(fn)
    async def wrapper(self,*args,**kwargs):
        bound = sig.bind(self,*args,**kwargs).arguments
        req = bound.get("req")
        pid = getattr(req,"project_id",None) if req else bound.get("project_id")
        wid = getattr(req,"worktree_id",None) if req else bound.get("worktree_id")
        token = _scope.set((str(pid),str(wid)) if pid and wid else None)
        try:
            return await fn(self,*args,**kwargs)
        finally:
            _scope.reset(token)
    return wrapper

async def observe_rpc(name, call):
    started = datetime.now(timezone.utc)
    error = None
    try:
        return await call()
    except Exception as exc:
        error = exc
        raise
    finally:
        await emit(name,started,error)
