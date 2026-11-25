"""V3.2 (SK32) additive SQLite migration v9 (Centralized Skill Management + P6A trace index) and strict Pydantic schemas."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

V32_SKILL_SQL = """
CREATE TABLE IF NOT EXISTS sk32_skills (
    id TEXT PRIMARY KEY,
    namespace TEXT NOT NULL DEFAULT 'global',
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    owner TEXT NOT NULL DEFAULT 'admin',
    lifecycle TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle IN ('active', 'archived')),
    active_version_id TEXT,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    is_starter INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(namespace, slug)
);
CREATE INDEX IF NOT EXISTS idx_sk32_skills_ns_life ON sk32_skills(namespace, lifecycle, updated_at DESC);

CREATE TABLE IF NOT EXISTS sk32_skill_versions (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES sk32_skills(id) ON DELETE CASCADE,
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    markdown_body TEXT NOT NULL,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL CHECK(state IN ('draft', 'review', 'published', 'retired')),
    author TEXT NOT NULL,
    approved_by TEXT,
    approved_at TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(skill_id, version)
);
CREATE INDEX IF NOT EXISTS idx_sk32_versions_skill ON sk32_skill_versions(skill_id, state, created_at DESC);

CREATE TABLE IF NOT EXISTS sk32_skill_bindings (
    id TEXT PRIMARY KEY,
    scope_type TEXT NOT NULL CHECK(scope_type IN ('global', 'business', 'project', 'agent_role')),
    scope_id TEXT NOT NULL,
    skill_id TEXT NOT NULL REFERENCES sk32_skills(id) ON DELETE CASCADE,
    mode TEXT NOT NULL CHECK(mode IN ('enabled', 'disabled', 'inherit')),
    version_constraint TEXT NOT NULL DEFAULT 'active',
    priority INTEGER NOT NULL DEFAULT 100,
    required INTEGER NOT NULL DEFAULT 0,
    mandatory INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(scope_type, scope_id, skill_id)
);
CREATE INDEX IF NOT EXISTS idx_sk32_bindings_scope ON sk32_skill_bindings(scope_type, scope_id, priority);
CREATE INDEX IF NOT EXISTS idx_sk32_bindings_skill ON sk32_skill_bindings(skill_id);

CREATE TABLE IF NOT EXISTS sk32_skill_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    worktree_id TEXT,
    business_id TEXT,
    plan_id TEXT,
    task_id TEXT,
    attempt_id TEXT,
    session_id TEXT,
    trace_id TEXT,
    skill_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    version_str TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    selection_reason TEXT NOT NULL,
    adapter TEXT NOT NULL DEFAULT 'resolver',
    status TEXT NOT NULL CHECK(status IN ('selected', 'delivered', 'loaded', 'executed', 'blocked', 'unknown')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    bytes_count INTEGER NOT NULL DEFAULT 0,
    input_tokens INTEGER,
    measurement_kind TEXT NOT NULL DEFAULT 'unavailable' CHECK(measurement_kind IN ('measured', 'estimated', 'unavailable')),
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sk32_runs_scope ON sk32_skill_runs(project_id, plan_id, task_id, attempt_id, created_at DESC);

CREATE TABLE IF NOT EXISTS sk32_skill_candidates (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT,
    target_skill_id TEXT REFERENCES sk32_skills(id) ON DELETE SET NULL,
    memory_ids_json TEXT NOT NULL,
    proposed_title TEXT NOT NULL,
    proposed_slug TEXT NOT NULL,
    proposed_manifest_json TEXT NOT NULL,
    proposed_markdown TEXT NOT NULL,
    diff_summary TEXT NOT NULL DEFAULT '',
    validation_evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    reviewer TEXT,
    review_note TEXT,
    published_version_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sk32_candidates_proj ON sk32_skill_candidates(project_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS sk32_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_v22_trace_plan_task_attempt ON v22_execution_traces(project_id, plan_id, task_id, attempt_id, started_at);
"""


class StrictSK32Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SkillManifest(StrictSK32Model):
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    version: str = Field(default="1.0.0", min_length=1, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=25)
    applicable_capabilities: list[str] = Field(default_factory=list, max_length=25)
    required_mcp_ids: list[str] = Field(default_factory=list, max_length=15)
    optional_mcp_ids: list[str] = Field(default_factory=list, max_length=15)
    required_tool_names: list[str] = Field(default_factory=list, max_length=20)
    permissions: list[str] = Field(default_factory=list, max_length=15)
    references: list[str] = Field(default_factory=list, max_length=20)


class SkillAttachment(StrictSK32Model):
    rel_path: str = Field(min_length=1, max_length=240)
    kind: Literal["reference", "asset", "script"] = "reference"
    content: str = Field(default="", max_length=32000)


class SkillCreateRequest(StrictSK32Model):
    namespace: str = Field(default="global", min_length=1, max_length=80)
    slug: str = Field(min_length=2, max_length=100)
    title: str = Field(min_length=2, max_length=140)
    description: str = Field(default="", max_length=1000)
    manifest: SkillManifest
    markdown_body: str = Field(min_length=10, max_length=48000)
    attachments: list[SkillAttachment] = Field(default_factory=list, max_length=10)
    provenance: dict[str, Any] = Field(default_factory=dict)


class SkillDraftUpdateRequest(StrictSK32Model):
    version_id: str | None = None
    version: str = Field(default="1.0.1", min_length=1, max_length=40)
    title: str | None = Field(default=None, min_length=2, max_length=140)
    description: str | None = Field(default=None, max_length=1000)
    manifest: SkillManifest
    markdown_body: str = Field(min_length=10, max_length=48000)
    attachments: list[SkillAttachment] = Field(default_factory=list, max_length=10)
    expected_updated_at: str | None = None


class SkillPublishRequest(StrictSK32Model):
    version_id: str = Field(min_length=1, max_length=64)
    set_active: bool = True


class SkillRollbackRequest(StrictSK32Model):
    version_id: str = Field(min_length=1, max_length=64)


class SkillImportRequest(StrictSK32Model):
    namespace: str = Field(default="imported", min_length=1, max_length=80)
    slug: str = Field(min_length=2, max_length=100)
    title: str = Field(min_length=2, max_length=140)
    source: str = Field(min_length=2, max_length=300)
    license: str = Field(min_length=2, max_length=120)
    manifest: SkillManifest
    markdown_body: str = Field(min_length=10, max_length=48000)
    attachments: list[SkillAttachment] = Field(default_factory=list, max_length=10)
    preview_only: bool = True
    approved: bool = False


class SkillBindingUpsertRequest(StrictSK32Model):
    scope_type: Literal["global", "business", "project", "agent_role"]
    scope_id: str = Field(min_length=1, max_length=128)
    skill_id: str = Field(min_length=1, max_length=64)
    mode: Literal["enabled", "disabled", "inherit"] = "enabled"
    version_constraint: str = Field(default="active", max_length=64)
    priority: int = Field(default=100, ge=1, le=1000)
    required: bool = False
    mandatory: bool = False


class SkillResolveRequest(StrictSK32Model):
    project_id: UUID
    worktree_id: UUID | None = None
    business_id: str | None = Field(default=None, max_length=64)
    agent_role: str | None = Field(default=None, max_length=80)
    plan_id: UUID | None = None
    task_id: str | None = Field(default=None, max_length=128)
    attempt_id: str | None = Field(default=None, max_length=128)
    session_id: UUID | None = None
    trace_id: UUID | None = None
    required_capabilities: list[str] = Field(default_factory=list, max_length=25)
    requested_skill_slugs: list[str] = Field(default_factory=list, max_length=20)
    max_chars: int = Field(default=16000, ge=500, le=64000)
    adapter: str = Field(default="resolver", max_length=64)
    record_run: bool = True


class SkillRunStatusUpdateRequest(StrictSK32Model):
    status: Literal["selected", "delivered", "loaded", "executed", "blocked", "unknown"]
    adapter: str | None = Field(default=None, max_length=64)
    evidence: dict[str, Any] = Field(default_factory=dict)
    input_tokens: int | None = Field(default=None, ge=0)
    measurement_kind: Literal["measured", "estimated", "unavailable"] = "unavailable"


class SkillCandidateProposeRequest(StrictSK32Model):
    project_id: UUID
    worktree_id: UUID | None = None
    target_skill_id: str | None = None
    memory_ids: list[UUID] = Field(min_length=1, max_length=10)
    proposed_slug: str = Field(min_length=2, max_length=100)
    proposed_title: str = Field(min_length=2, max_length=140)
    tags: list[str] = Field(default_factory=list, max_length=15)


class SkillCandidateReviewRequest(StrictSK32Model):
    decision: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)
    version: str = Field(default="1.0.0", max_length=40)
