"""Bounded CLI execution evidence; session usage is recorded separately."""
from datetime import datetime, timezone
from uuid import UUID, uuid5
import json

NS = UUID("52c766ac-2c6b-4d30-9d9e-0c9d308d450c")

def build_execution_trace(job, started, ended, *, local=False, status, error_code=None, stdout=""):
    attempt = job.get("attempt_id")
    if not attempt or not job.get("session_id"):
        return None
    parent = str(uuid5(NS,"attempt:"+str(attempt)))
    ident = str(uuid5(NS,"cli_execution:"+str(attempt)))
    reported = None
    # Only a model explicitly reported in structured CLI output is actual identity.
    try:
        data = json.loads(stdout)
        if isinstance(data,dict) and isinstance(data.get("model"),str):
            reported = data["model"][:200]
    except (ValueError,TypeError):
        pass
    return dict(trace_id=ident,root_trace_id=ident,project_id=job["project_id"],
                worktree_id=job["worktree_id"],step_type="local_rule" if local else "agent_step",
                step_name="deterministic_rules" if local else "cli_execution",
                actor_type="system" if local else "agent",provider=job.get("provider"),
                model_id=reported,used_llm=False if local else None,
                started_at=started.isoformat(),ended_at=ended.isoformat(),status=status,error_code=error_code,
                metadata_json={"source":"host_runner.process","why":"Observed local rule" if local else "CLI execution observed; individual model calls and inference latency unsupported",
                               "requested_model":str(job.get("model_id") or "unknown")[:200],
                               "coverage":"CLI wall time only","evidence_ref":"v2_sessions/"+str(job["session_id"])},
                )
