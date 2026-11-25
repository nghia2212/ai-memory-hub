"""V2.2 execution trace contract and additive SQLite migration."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

TRACE_SQL = """
CREATE TABLE IF NOT EXISTS v22_execution_traces (
 trace_id TEXT PRIMARY KEY,
 parent_trace_id TEXT REFERENCES v22_execution_traces(trace_id),
 root_trace_id TEXT NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
 worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
 plan_id TEXT, task_id TEXT, attempt_id TEXT, session_id TEXT,
 step_type TEXT NOT NULL, step_name TEXT NOT NULL,
 actor_type TEXT NOT NULL, actor_id TEXT,
 tool_name TEXT, provider TEXT, model_id TEXT,
 used_memory INTEGER, used_mcp INTEGER, used_llm INTEGER,
 status TEXT NOT NULL, started_at TEXT NOT NULL, ended_at TEXT,
 duration_ms INTEGER, input_summary TEXT, output_summary TEXT,
 error_code TEXT, error_summary TEXT,
 input_tokens INTEGER, output_tokens INTEGER, cache_read_tokens INTEGER,
 cache_write_tokens INTEGER, reasoning_tokens INTEGER, cost_usd REAL,
 metadata_json TEXT NOT NULL,
 payload_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v22_trace_scope ON v22_execution_traces(project_id, worktree_id, started_at);
CREATE INDEX IF NOT EXISTS idx_v22_trace_root ON v22_execution_traces(root_trace_id);
CREATE INDEX IF NOT EXISTS idx_v22_trace_session ON v22_execution_traces(project_id, session_id);
"""

class TraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    trace_id: UUID
    parent_trace_id: UUID | None = None
    root_trace_id: UUID
    project_id: UUID
    worktree_id: UUID
    plan_id: UUID | None = None
    task_id: UUID | None = None
    attempt_id: UUID | None = None
    session_id: UUID | None = None
    step_type: Literal["user_task","planner","agent_step","mcp_tool_call","ai_memory","llm_call","code_search","qa_test","error","handoff","save_memory","local_rule"]
    step_name: str = Field(min_length=1,max_length=200)
    actor_type: Literal["user","planner","memory","mcp","agent","model","qa","system"]
    actor_id: str | None = Field(default=None,max_length=200)
    tool_name: str | None = Field(default=None,max_length=200)
    provider: str | None = Field(default=None,max_length=100)
    model_id: str | None = Field(default=None,max_length=200)
    used_memory: bool | None = None
    used_mcp: bool | None = None
    used_llm: bool | None = None
    status: Literal["running","succeeded","failed","blocked","cancelled","unknown","unsupported"]
    started_at: datetime
    ended_at: datetime | None = None
    duration_ms: int | None = Field(default=None,ge=0)
    input_summary: str | None = Field(default=None,max_length=4000)
    output_summary: str | None = Field(default=None,max_length=4000)
    error_code: str | None = Field(default=None,max_length=200)
    error_summary: str | None = Field(default=None,max_length=2000)
    input_tokens: int | None = Field(default=None,ge=0)
    output_tokens: int | None = Field(default=None,ge=0)
    cache_read_tokens: int | None = Field(default=None,ge=0)
    cache_write_tokens: int | None = Field(default=None,ge=0)
    reasoning_tokens: int | None = Field(default=None,ge=0)
    cost_usd: float | None = Field(default=None,ge=0)
    # Only explicit, bounded evidence references; never arbitrary raw CLI output.
    metadata_json: dict[str, str | int | bool | None] = Field(default_factory=dict,max_length=20)

    @model_validator(mode="after")
    def validate_trace(self):
        for field in ("started_at","ended_at"):
            value = getattr(self,field)
            if value is not None:
                if value.utcoffset() is None:
                    raise ValueError("Timezone required")
                setattr(self,field,value.astimezone(timezone.utc))
        if self.ended_at is not None:
            if self.ended_at < self.started_at:
                raise ValueError("End precedes start")
            measured = int((self.ended_at-self.started_at).total_seconds()*1000)
            if self.duration_ms is None:
                self.duration_ms = measured
        if self.parent_trace_id is None and self.root_trace_id != self.trace_id:
            raise ValueError("Root trace must refer to itself")
        if self.parent_trace_id == self.trace_id or (self.parent_trace_id and self.root_trace_id == self.trace_id):
            raise ValueError("Cyclic trace")
        if self.status == "running" and self.ended_at is not None:
            raise ValueError("Running trace cannot have end time")
        allowed = {"evidence_ref","source","why","retry_of","memory_id","handoff_id","test_evidence_ref","code_path","coverage"}
        if set(self.metadata_json) - allowed or any(len(str(v)) > 1000 for v in self.metadata_json.values()):
            raise ValueError("Unsupported or oversized evidence metadata")
        if not self.metadata_json.get("source"):
            raise ValueError("Evidence source required")
        return self
