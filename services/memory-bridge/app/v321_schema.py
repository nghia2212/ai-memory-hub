"""V3.2.1 (MCP321) additive SQLite migration v10: MCP Candidate Catalog & External Plan/Task Sync (Monitor-Only)."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

V321_MCP321_SQL = """
CREATE TABLE IF NOT EXISTS mcp321_catalog_entries (
    id TEXT PRIMARY KEY,
    catalog_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('common', 'game', 'architect', 'developer', 'builtin_pack')),
    description TEXT NOT NULL DEFAULT '',
    source_repo TEXT NOT NULL,
    version TEXT NOT NULL,
    license TEXT NOT NULL,
    install_method TEXT NOT NULL,
    transport TEXT NOT NULL CHECK(transport IN ('stdio', 'streamable_http', 'native_cli', 'rest_api', 'local_tool')),
    default_endpoint_or_cmd TEXT NOT NULL DEFAULT '',
    default_args_json TEXT NOT NULL DEFAULT '[]',
    capabilities_json TEXT NOT NULL DEFAULT '[]',
    auth_requirements_json TEXT NOT NULL DEFAULT '[]',
    network_destinations_json TEXT NOT NULL DEFAULT '[]',
    risk_level TEXT NOT NULL DEFAULT 'low' CHECK(risk_level IN ('low', 'medium', 'high')),
    resource_estimate TEXT NOT NULL DEFAULT 'low',
    recommended_businesses_json TEXT NOT NULL DEFAULT '[]',
    verified_source INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_external_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    source_client TEXT NOT NULL CHECK(source_client IN ('antigravity', 'claude', 'codex', 'gemini', 'session_monitor')),
    source_instance TEXT NOT NULL DEFAULT 'local',
    external_plan_id TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL DEFAULT '',
    source_status TEXT NOT NULL DEFAULT 'unknown',
    normalized_status TEXT NOT NULL DEFAULT 'unknown' CHECK(normalized_status IN ('active', 'completed', 'failed', 'cancelled', 'paused', 'unknown')),
    revision_no INTEGER NOT NULL DEFAULT 1,
    revision_hash TEXT NOT NULL,
    is_monitor_only INTEGER NOT NULL DEFAULT 1,
    linked_session_id TEXT,
    linked_root_trace_id TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    deleted_tombstone INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, source_client, external_plan_id)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_ext_plans_proj ON mcp321_external_plans(project_id, source_client, deleted_tombstone, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp321_external_tasks (
    id TEXT PRIMARY KEY,
    external_plan_id TEXT NOT NULL REFERENCES mcp321_external_plans(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    external_task_key TEXT NOT NULL,
    title TEXT NOT NULL,
    parent_task_id TEXT,
    source_status TEXT NOT NULL DEFAULT 'unknown',
    normalized_status TEXT NOT NULL DEFAULT 'unknown' CHECK(normalized_status IN ('pending', 'running', 'completed', 'failed', 'blocked', 'unknown')),
    revision_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(external_plan_id, external_task_key)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_ext_tasks_plan ON mcp321_external_tasks(external_plan_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp321_external_plan_revisions (
    id TEXT PRIMARY KEY,
    external_plan_id TEXT NOT NULL REFERENCES mcp321_external_plans(id) ON DELETE CASCADE,
    revision_no INTEGER NOT NULL,
    revision_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(external_plan_id, revision_no)
);

CREATE TABLE IF NOT EXISTS mcp321_trace_associations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    external_plan_id TEXT NOT NULL REFERENCES mcp321_external_plans(id) ON DELETE CASCADE,
    external_task_id TEXT REFERENCES mcp321_external_tasks(id) ON DELETE SET NULL,
    trace_id TEXT,
    session_id TEXT,
    actor TEXT NOT NULL,
    evidence_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp321_assoc_plan ON mcp321_trace_associations(project_id, external_plan_id);

CREATE TABLE IF NOT EXISTS mcp321_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class StrictMCP321Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CatalogRegisterRequest(StrictMCP321Model):
    catalog_key: str = Field(min_length=2, max_length=100)
    server_name: str | None = Field(default=None, max_length=128)
    endpoint: str | None = Field(default=None, max_length=500)
    executable: str | None = Field(default=None, max_length=300)
    args: list[str] | None = None
    allowed_project_ids: list[str] = Field(default_factory=list, max_length=50)
    preview_only: bool = True
    approve_immediately: bool = False


class ExternalTaskIn(StrictMCP321Model):
    external_task_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    parent_task_id: str | None = None
    source_status: str = Field(default="unknown", max_length=64)
    normalized_status: Literal["pending", "running", "completed", "failed", "blocked", "unknown"] = "unknown"


class ExternalPlanIngestRequest(StrictMCP321Model):
    project_id: UUID
    worktree_id: UUID | None = None
    source_client: Literal["antigravity", "claude", "codex", "gemini", "session_monitor"]
    source_instance: str = Field(default="local", max_length=120)
    external_plan_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(default="", max_length=16000)
    source_status: str = Field(default="unknown", max_length=64)
    normalized_status: Literal["active", "completed", "failed", "cancelled", "paused", "unknown"] = "unknown"
    linked_session_id: UUID | None = None
    linked_root_trace_id: UUID | None = None
    tasks: list[ExternalTaskIn] = Field(default_factory=list, max_length=100)
    provenance: dict[str, Any] = Field(default_factory=dict)
    consent_confirmed: bool = True


class SessionToMonitoringPlanRequest(StrictMCP321Model):
    project_id: UUID
    session_id: UUID
    source_client: Literal["antigravity", "claude", "codex", "gemini", "session_monitor"] = "antigravity"
    title: str = Field(min_length=2, max_length=240)
    objective: str = Field(default="", max_length=8000)
    root_trace_id: UUID | None = None
    consent_confirmed: bool = True


class TraceAssociationRequest(StrictMCP321Model):
    project_id: UUID
    external_plan_id: str = Field(min_length=1, max_length=64)
    external_task_id: str | None = None
    trace_id: UUID | None = None
    session_id: UUID | None = None
    evidence_note: str = Field(default="", max_length=400)
