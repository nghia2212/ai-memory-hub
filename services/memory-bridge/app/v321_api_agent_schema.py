"""V3.2.1 (MCP321) P14-P20 additive SQLite migration v12 & strict Pydantic models for Hub-managed API Agent."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

V321_API_AGENT_SQL = """
CREATE TABLE IF NOT EXISTS mcp321_provider_profiles (
    id TEXT PRIMARY KEY,
    provider_key TEXT NOT NULL CHECK(provider_key IN ('deepseek', 'gemini', 'qwen_local', 'openai', 'anthropic', 'mock')),
    display_name TEXT NOT NULL UNIQUE,
    billing_mode TEXT NOT NULL CHECK(billing_mode IN ('metered_api', 'local_compute', 'subscription_ide', 'mock')),
    credential_env_ref TEXT NOT NULL DEFAULT '',
    endpoint_url TEXT NOT NULL DEFAULT '',
    endpoint_allowlist_json TEXT NOT NULL DEFAULT '[]',
    selected_model TEXT NOT NULL DEFAULT '',
    thinking_level TEXT NOT NULL DEFAULT 'none' CHECK(thinking_level IN ('none', 'low', 'medium', 'high', 'max')),
    max_output_tokens INTEGER NOT NULL DEFAULT 2048,
    pricing_version TEXT NOT NULL DEFAULT 'unverified',
    pricing_json TEXT NOT NULL DEFAULT '{}',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    default_business_ids_json TEXT NOT NULL DEFAULT '[]',
    default_project_ids_json TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_project_egress_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    allow_external_paid_api INTEGER NOT NULL DEFAULT 0,
    allowed_provider_keys_json TEXT NOT NULL DEFAULT '["mock", "qwen_local"]',
    allow_redacted_only INTEGER NOT NULL DEFAULT 1,
    max_task_budget_usd REAL NOT NULL DEFAULT 0.0,
    max_daily_budget_usd REAL NOT NULL DEFAULT 0.0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_agent_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
    plan_id TEXT,
    task_id TEXT,
    attempt_id TEXT,
    session_id TEXT,
    root_trace_id TEXT,
    execution_mode TEXT NOT NULL CHECK(execution_mode IN ('external', 'api_agent', 'hybrid', 'local')),
    provider_profile_id TEXT REFERENCES mcp321_provider_profiles(id),
    requested_model TEXT NOT NULL DEFAULT '',
    thinking_level TEXT NOT NULL DEFAULT 'none',
    write_mode TEXT NOT NULL DEFAULT 'read_only' CHECK(write_mode IN ('read_only', 'worktree_patch')),
    status TEXT NOT NULL CHECK(status IN ('pending_approval', 'running', 'completed', 'failed', 'cancelled', 'stopped_budget', 'blocked')),
    objective TEXT NOT NULL,
    max_rounds INTEGER NOT NULL DEFAULT 4,
    max_calls INTEGER NOT NULL DEFAULT 6,
    max_budget_usd REAL NOT NULL DEFAULT 0.0,
    reserved_budget_usd REAL NOT NULL DEFAULT 0.0,
    spent_budget_usd REAL NOT NULL DEFAULT 0.0,
    uncertain_budget_usd REAL NOT NULL DEFAULT 0.0,
    rounds_used INTEGER NOT NULL DEFAULT 0,
    calls_used INTEGER NOT NULL DEFAULT 0,
    context_preview_json TEXT NOT NULL DEFAULT '{}',
    result_summary TEXT NOT NULL DEFAULT '',
    patch_diff TEXT NOT NULL DEFAULT '',
    handoff_id TEXT,
    stop_reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_mcp321_runs_proj ON mcp321_agent_runs(project_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp321_provider_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES mcp321_agent_runs(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    session_id TEXT,
    trace_id TEXT,
    idempotency_key TEXT NOT NULL,
    provider_key TEXT NOT NULL,
    requested_model TEXT NOT NULL,
    provider_reported_model TEXT,
    provider_request_id TEXT,
    thinking_level TEXT NOT NULL DEFAULT 'none',
    status TEXT NOT NULL CHECK(status IN ('reserved', 'completed', 'failed', 'cancelled', 'uncertain')),
    reserved_cost_usd REAL NOT NULL DEFAULT 0.0,
    estimated_cost_usd REAL,
    input_tokens INTEGER,
    cached_input_tokens INTEGER,
    cache_write_tokens INTEGER,
    uncached_input_tokens INTEGER,
    output_tokens INTEGER,
    reasoning_tokens INTEGER,
    visible_output_tokens INTEGER,
    total_tokens INTEGER,
    reasoning_included_in_output INTEGER NOT NULL DEFAULT 1,
    measurement_source TEXT NOT NULL DEFAULT 'unavailable',
    measurement_quality TEXT NOT NULL DEFAULT 'unavailable' CHECK(measurement_quality IN ('measured', 'estimated', 'unavailable')),
    finish_reason TEXT,
    error_code TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(run_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_preq_run ON mcp321_provider_requests(run_id, created_at ASC);

CREATE TABLE IF NOT EXISTS mcp321_hybrid_stages (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    stage_order INTEGER NOT NULL,
    stage_name TEXT NOT NULL,
    agent_kind TEXT NOT NULL CHECK(agent_kind IN ('external', 'api_agent', 'local')),
    actor_label TEXT NOT NULL,
    provider_profile_id TEXT REFERENCES mcp321_provider_profiles(id),
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'running', 'awaiting_external', 'completed', 'failed', 'blocked')),
    linked_run_id TEXT REFERENCES mcp321_agent_runs(id) ON DELETE SET NULL,
    linked_session_id TEXT,
    handoff_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(plan_id, stage_order)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_hybrid_plan ON mcp321_hybrid_stages(plan_id, stage_order ASC);
"""


class StrictApiAgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProviderProfileUpsertRequest(StrictApiAgentModel):
    provider_key: Literal["deepseek", "gemini", "qwen_local", "openai", "anthropic", "mock"]
    display_name: str = Field(min_length=2, max_length=120)
    billing_mode: Literal["metered_api", "local_compute", "subscription_ide", "mock"] = "metered_api"
    credential_env_ref: str = Field(default="", max_length=80)
    endpoint_url: str = Field(default="", max_length=300)
    endpoint_allowlist: list[str] = Field(default_factory=list, max_length=20)
    selected_model: str = Field(default="", max_length=120)
    thinking_level: Literal["none", "low", "medium", "high", "max"] = "none"
    max_output_tokens: int = Field(default=2048, ge=64, le=65536)
    pricing_version: str = Field(default="unverified", max_length=64)
    pricing: dict[str, float] = Field(default_factory=dict)
    default_business_ids: list[str] = Field(default_factory=list, max_length=30)
    default_project_ids: list[str] = Field(default_factory=list, max_length=50)
    enabled: bool = False


class ProviderProfilePatchRequest(StrictApiAgentModel):
    credential_env_ref: str | None = Field(default=None, max_length=80)
    endpoint_url: str | None = Field(default=None, max_length=300)
    selected_model: str | None = Field(default=None, max_length=120)
    thinking_level: Literal["none", "low", "medium", "high", "max"] | None = None
    max_output_tokens: int | None = Field(default=None, ge=64, le=65536)
    pricing_version: str | None = Field(default=None, max_length=64)
    pricing: dict[str, float] | None = None
    default_business_ids: list[str] | None = None
    default_project_ids: list[str] | None = None
    enabled: bool | None = None


class ProviderConnectionTestRequest(StrictApiAgentModel):
    allow_paid_probe: bool = False


class ProjectEgressPolicyRequest(StrictApiAgentModel):
    allow_external_paid_api: bool = False
    allowed_provider_keys: list[str] = Field(default_factory=lambda: ["mock", "qwen_local"], max_length=10)
    allow_redacted_only: bool = True
    max_task_budget_usd: float = Field(default=0.0, ge=0.0, le=100.0)
    max_daily_budget_usd: float = Field(default=0.0, ge=0.0, le=500.0)


class AgentRunPreviewRequest(StrictApiAgentModel):
    project_id: UUID
    worktree_id: UUID | None = None
    business_id: str = Field(min_length=1, max_length=64)
    execution_mode: Literal["external", "api_agent", "hybrid", "local"] = "api_agent"
    provider_profile_id: str | None = Field(default=None, max_length=80)
    requested_model: str | None = Field(default=None, max_length=120)
    thinking_level: Literal["none", "low", "medium", "high", "max"] = "none"
    write_mode: Literal["read_only", "worktree_patch"] = "read_only"
    objective: str = Field(min_length=3, max_length=8000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    max_rounds: int = Field(default=3, ge=1, le=10)
    max_calls: int = Field(default=4, ge=1, le=15)
    max_budget_usd: float = Field(default=0.0, ge=0.0, le=50.0)


class AgentRunCreateRequest(AgentRunPreviewRequest):
    plan_id: str | None = Field(default=None, max_length=64)
    task_id: str | None = Field(default=None, max_length=64)
    egress_consent_confirmed: bool = False
    execute_now: bool = True
    mock_script: list[dict[str, Any]] | None = None


class HybridStageIn(StrictApiAgentModel):
    stage_order: int = Field(ge=1, le=20)
    stage_name: str = Field(min_length=2, max_length=160)
    agent_kind: Literal["external", "api_agent", "local"]
    actor_label: str = Field(min_length=2, max_length=120)
    provider_profile_id: str | None = Field(default=None, max_length=80)
    handoff_summary: str = Field(default="", max_length=2000)


class HybridPlanCreateRequest(StrictApiAgentModel):
    project_id: UUID
    plan_id: str = Field(min_length=2, max_length=64)
    stages: list[HybridStageIn] = Field(min_length=1, max_length=20)


class HybridStageUpdateRequest(StrictApiAgentModel):
    status: Literal["pending", "running", "awaiting_external", "completed", "failed", "blocked"]
    linked_run_id: str | None = None
    linked_session_id: str | None = None
    handoff_summary: str | None = Field(default=None, max_length=4000)


# Migration 13 (strictly additive): P19A AI Chat Workspace durable conversations & messages
V321_CHAT_WORKSPACE_SQL = """
CREATE TABLE IF NOT EXISTS mcp321_chat_conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
    title TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'chat' CHECK(mode IN ('chat', 'task', 'plan_dag')),
    provider_profile_id TEXT REFERENCES mcp321_provider_profiles(id),
    model_id TEXT NOT NULL DEFAULT '',
    archived INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp321_chat_conv_proj ON mcp321_chat_conversations(project_id, archived, updated_at DESC);

CREATE TABLE IF NOT EXISTS mcp321_chat_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES mcp321_chat_conversations(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'text/markdown' CHECK(content_type IN ('text/plain', 'text/markdown', 'application/json')),
    delivery_status TEXT NOT NULL DEFAULT 'delivered' CHECK(delivery_status IN ('pending', 'delivered', 'missing', 'failed', 'not_applicable')),
    mode TEXT NOT NULL DEFAULT 'chat',
    provider_key TEXT,
    model_id TEXT,
    thinking_level TEXT,
    run_id TEXT,
    plan_id TEXT,
    trace_id TEXT,
    idempotency_key TEXT,
    usage_json TEXT NOT NULL DEFAULT '{}',
    sources_json TEXT NOT NULL DEFAULT '{}',
    truncated INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_chat_msg_conv ON mcp321_chat_messages(conversation_id, created_at ASC);
"""


class ChatConversationCreateRequest(StrictApiAgentModel):
    project_id: UUID
    worktree_id: UUID | None = None
    business_id: str = Field(min_length=1, max_length=64)
    title: str = Field(default="Cuộc trò chuyện mới", min_length=1, max_length=200)
    mode: Literal["chat", "task", "plan_dag"] = "chat"
    execution_source: str | None = Field(default=None, max_length=64)
    provider_profile_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=120)


class ChatMessageSendRequest(StrictApiAgentModel):
    project_id: UUID
    content: str = Field(min_length=1, max_length=16000)
    mode: Literal["chat", "task", "plan_dag"] | None = None
    execution_source: str | None = Field(default=None, max_length=64)
    provider_profile_id: str | None = Field(default=None, max_length=80)
    model_id: str | None = Field(default=None, max_length=120)
    thinking_level: Literal["none", "low", "medium", "high", "max"] = "none"
    max_budget_usd: float = Field(default=0.05, ge=0.0, le=50.0)
    allow_tools: bool = False
    egress_consent_confirmed: bool = False
    manual_handoff: bool = False
    idempotency_key: str | None = Field(default=None, max_length=128)
    mock_script: list[dict[str, Any]] | None = None


# Migration 14 (strictly additive): P19A.1/P19A.2 Subscription Agents, Controls, Project Defaults & Manual Handoffs
V321_SUBSCRIPTION_AGENT_SQL = """
CREATE TABLE IF NOT EXISTS mcp321_agent_controls (
    source_key TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    disabled_reason TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_model_controls (
    source_key TEXT NOT NULL,
    model_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    disabled_reason TEXT NOT NULL DEFAULT '',
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(source_key, model_id)
);

CREATE TABLE IF NOT EXISTS mcp321_project_chat_defaults (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    business_id TEXT NOT NULL DEFAULT '',
    execution_source TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'task' CHECK(mode IN ('chat', 'task', 'plan_dag')),
    allow_paid_api_fallback INTEGER NOT NULL DEFAULT 0,
    updated_by TEXT NOT NULL DEFAULT 'admin',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_chat_handoffs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES mcp321_chat_conversations(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    source_key TEXT NOT NULL,
    model_id TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'task',
    prompt_package TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'awaiting_external' CHECK(status IN ('awaiting_external', 'imported', 'cancelled')),
    external_plan_id TEXT,
    external_session_id TEXT,
    provider_reported_model TEXT,
    result_content TEXT NOT NULL DEFAULT '',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mcp321_chat_handoffs_conv ON mcp321_chat_handoffs(conversation_id, created_at DESC);

ALTER TABLE mcp321_chat_conversations ADD COLUMN execution_source TEXT NOT NULL DEFAULT '';
ALTER TABLE mcp321_chat_conversations ADD COLUMN external_session_id TEXT NOT NULL DEFAULT '';
"""


class AgentControlUpdateRequest(StrictApiAgentModel):
    enabled: bool
    disabled_reason: str = Field(default="", max_length=300)


class ModelControlUpdateRequest(StrictApiAgentModel):
    source_key: str = Field(min_length=2, max_length=64)
    model_id: str = Field(min_length=1, max_length=120)
    enabled: bool
    disabled_reason: str = Field(default="", max_length=300)


class ProjectChatDefaultsRequest(StrictApiAgentModel):
    business_id: str = Field(default="", max_length=64)
    execution_source: str = Field(default="", max_length=64)
    model_id: str = Field(default="", max_length=120)
    mode: Literal["chat", "task", "plan_dag"] = "task"
    allow_paid_api_fallback: bool = False


class ChatHandoffImportRequest(StrictApiAgentModel):
    project_id: UUID
    result_content: str = Field(min_length=1, max_length=16000)
    external_plan_id: str | None = Field(default=None, max_length=120)
    external_session_id: str | None = Field(default=None, max_length=120)
    provider_reported_model: str | None = Field(default=None, max_length=120)
    evidence: dict[str, Any] = Field(default_factory=dict)


