"""Trace each actual Hermes provider attempt without retaining prompt or response text."""
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4
import math
import logging

context = ContextVar("hermes_trace_context", default=None)
log = logging.getLogger(__name__)

def now():
    return datetime.now(timezone.utc)

def counter(value):
    return value if type(value) is int and value >= 0 else None

def emit(provider, requested, started, *, response=None, error=None, retry_of=None, local=False):
    ctx = context.get()
    ident = str(uuid4())
    if not ctx:
        return ident
    bridge, event = ctx
    if not hasattr(bridge,"post_trace"):
        return ident
    response = response if isinstance(response,dict) else {}
    usage = response.get("usage")
    usage = usage if isinstance(usage,dict) else {}
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details,dict) else {}
    completion = usage.get("completion_tokens_details")
    completion = completion if isinstance(completion,dict) else {}
    cost = usage.get("cost_usd")
    if type(cost) not in (int,float) or not math.isfinite(cost) or cost < 0:
        cost = None
    model = response.get("model")
    meta = {"source":"hermes.provider","why":"Observed local summarization" if local else "Observed HTTP provider attempt; latency includes transport",
            "requested_model":str(requested)[:200],"usage_scope":"model_call","measurement_kind":"measured" if usage else "unavailable",
            "evidence_ref":str(event.get("event_id") or event.get("id") or "")[:1000]}
    if retry_of:
        meta["retry_of"] = retry_of
    payload = dict(trace_id=ident,root_trace_id=ident,project_id=event.get("project_id"),worktree_id=event.get("worktree_id"),
                   step_type="local_rule" if local else "llm_call",step_name="hermes_summarize",actor_type="system" if local else "model",
                   actor_id="hermes",provider=provider,model_id=model[:200] if isinstance(model,str) else None,
                   used_llm=False if local else True,used_mcp=False,used_memory=False,
                   status="failed" if error else "succeeded",started_at=started.isoformat(),ended_at=now().isoformat(),
                   error_code=type(error).__name__ if error else None,
                   input_tokens=counter(usage.get("prompt_tokens")),output_tokens=counter(usage.get("completion_tokens")),
                   cache_read_tokens=counter(usage.get("prompt_cache_hit_tokens",details.get("cached_tokens"))),
                   reasoning_tokens=counter(completion.get("reasoning_tokens")),cost_usd=cost,metadata_json=meta)
    try:
        bridge.post_trace(payload)
    except Exception:
        log.warning("Hermes trace delivery failed")
    return ident
