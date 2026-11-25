"""V3.1 additive business workspace & Dynamic MCP Gateway schema and strict Pydantic models.

Note:
- Migration 7 (`BUSINESS_SQL`) is preserved untouched from commit 7b31a95d for backward compatibility.
- Migration 8 (`V31_EXTENSIONS_SQL`) is strictly additive for full Business Workspace policies,
  MCP bindings, MCP Gateway extensions, invocation audit, policy snapshots, and Asset Registry.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# Migration 7 (preserved from 7b31a95d44b9ce4f8e5971879fac19e45e7b9475)
BUSINESS_SQL = """
CREATE TABLE IF NOT EXISTS v31_business_profiles (
 id TEXT PRIMARY KEY, name TEXT NOT NULL COLLATE NOCASE UNIQUE,
 description TEXT NOT NULL DEFAULT '', archived INTEGER NOT NULL DEFAULT 0,
 revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS v31_business_projects (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 project_id TEXT NOT NULL REFERENCES projects(id),
 PRIMARY KEY(business_id,project_id)
);
CREATE TABLE IF NOT EXISTS v31_business_tools (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 tool_id TEXT NOT NULL REFERENCES v3_mcp_tools(id),
 policy TEXT NOT NULL CHECK(policy IN ('allow','approval','deny')),
 PRIMARY KEY(business_id,tool_id)
);
CREATE TABLE IF NOT EXISTS v31_business_versions (
 business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
 revision INTEGER NOT NULL, snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(business_id,revision)
);
"""

# Migration 8 (strictly additive V3.1 extensions)
V31_EXTENSIONS_SQL = """
ALTER TABLE v31_business_profiles ADD COLUMN slug TEXT;
ALTER TABLE v31_business_profiles ADD COLUMN icon TEXT NOT NULL DEFAULT 'briefcase';
ALTER TABLE v31_business_profiles ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1;
ALTER TABLE v31_business_profiles ADD COLUMN template_key TEXT;
ALTER TABLE v31_business_profiles ADD COLUMN agent_instructions TEXT NOT NULL DEFAULT '';
ALTER TABLE v31_business_profiles ADD COLUMN model_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE v31_business_profiles ADD COLUMN budget_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE v31_business_profiles ADD COLUMN memory_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE v31_business_profiles ADD COLUMN workflow_policy_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE v31_business_profiles ADD COLUMN resource_policy_json TEXT NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX IF NOT EXISTS idx_v31_business_slug ON v31_business_profiles(slug) WHERE slug IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_v31_business_state ON v31_business_profiles(archived, enabled, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_v31_business_projects_proj ON v31_business_projects(project_id);
CREATE INDEX IF NOT EXISTS idx_v31_business_tools_biz ON v31_business_tools(business_id);

CREATE TABLE IF NOT EXISTS v31_business_mcp_bindings (
    business_id TEXT NOT NULL REFERENCES v31_business_profiles(id) ON DELETE CASCADE,
    server_id TEXT NOT NULL REFERENCES v3_mcp_servers(id) ON DELETE CASCADE,
    enabled INTEGER NOT NULL DEFAULT 1,
    required INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 100,
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(business_id, server_id)
);
CREATE INDEX IF NOT EXISTS idx_v31_biz_mcp_server ON v31_business_mcp_bindings(server_id);

CREATE TABLE IF NOT EXISTS v31_business_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v31_business_templates (
    id TEXT PRIMARY KEY,
    template_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    icon TEXT NOT NULL DEFAULT 'briefcase',
    is_builtin INTEGER NOT NULL DEFAULT 0,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v31_mcp_server_ext (
    server_id TEXT PRIMARY KEY REFERENCES v3_mcp_servers(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    adapter_transport TEXT NOT NULL DEFAULT 'streamable_http' CHECK(adapter_transport IN ('stdio', 'streamable_http', 'native_cli', 'rest_api', 'local_tool')),
    executable TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '[]',
    working_dir TEXT NOT NULL DEFAULT '',
    env_allowlist_json TEXT NOT NULL DEFAULT '[]',
    secret_refs_json TEXT NOT NULL DEFAULT '[]',
    capability_tags_json TEXT NOT NULL DEFAULT '[]',
    timeout_sec INTEGER NOT NULL DEFAULT 30,
    concurrency_limit INTEGER NOT NULL DEFAULT 4,
    ram_limit_mb INTEGER NOT NULL DEFAULT 512,
    cpu_limit_pct INTEGER NOT NULL DEFAULT 50,
    activation_policy TEXT NOT NULL DEFAULT 'on_demand' CHECK(activation_policy IN ('on_demand', 'persistent', 'per_session', 'shared')),
    enabled INTEGER NOT NULL DEFAULT 1,
    archived INTEGER NOT NULL DEFAULT 0,
    protocol_version TEXT NOT NULL DEFAULT '',
    last_error_code TEXT,
    last_error_message TEXT,
    last_success_at TEXT,
    process_pid INTEGER,
    process_status TEXT NOT NULL DEFAULT 'stopped',
    invocation_count INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failure_count INTEGER NOT NULL DEFAULT 0,
    total_latency_ms INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v31_mcp_tool_ext (
    tool_id TEXT PRIMARY KEY REFERENCES v3_mcp_tools(id) ON DELETE CASCADE,
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    output_schema_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    removed INTEGER NOT NULL DEFAULT 0,
    schema_changed_at TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v31_mcp_invocations (
    id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    correlation_id TEXT,
    project_id TEXT,
    worktree_id TEXT,
    business_id TEXT,
    session_id TEXT,
    trace_id TEXT,
    server_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    capability TEXT,
    actor TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('planned', 'selected', 'activated', 'invoked', 'completed', 'failed', 'denied', 'unsupported', 'approval_required', 'timeout')),
    input_summary TEXT NOT NULL DEFAULT '',
    output_summary TEXT NOT NULL DEFAULT '',
    error_code TEXT,
    error_message TEXT,
    latency_ms INTEGER,
    approval_ref TEXT,
    idempotency_key TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_v31_invocations_time ON v31_mcp_invocations(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_v31_invocations_scope ON v31_mcp_invocations(business_id, project_id, session_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_v31_invocations_idem ON v31_mcp_invocations(server_id, tool_name, idempotency_key) WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS v31_task_policy_snapshots (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    session_id TEXT,
    project_id TEXT NOT NULL,
    worktree_id TEXT,
    business_id TEXT NOT NULL REFERENCES v31_business_profiles(id),
    config_revision INTEGER NOT NULL,
    effective_policy_json TEXT NOT NULL,
    selected_servers_json TEXT NOT NULL,
    selected_tools_json TEXT NOT NULL,
    excluded_reasons_json TEXT NOT NULL DEFAULT '[]',
    model_policy_json TEXT NOT NULL DEFAULT '{}',
    resource_policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v31_snapshots_scope ON v31_task_policy_snapshots(project_id, business_id, created_at DESC);

CREATE TABLE IF NOT EXISTS v31_asset_registry (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source TEXT NOT NULL DEFAULT 'local',
    license TEXT NOT NULL DEFAULT 'unknown',
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    format TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT '1.0',
    dependencies_json TEXT NOT NULL DEFAULT '[]',
    thumbnail_path TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    approval_state TEXT NOT NULL DEFAULT 'pending' CHECK(approval_state IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v31_assets_proj ON v31_asset_registry(project_id, format);
"""


class StrictV31Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


# --- Business Workspace Schemas ---

class BusinessCreateRequest(StrictV31Model):
    name: str = Field(min_length=2, max_length=120)
    slug: str | None = Field(default=None, max_length=120)
    description: str = Field(default="", max_length=1000)
    icon: str = Field(default="briefcase", max_length=40)
    enabled: bool = True
    template_key: str | None = Field(default=None, max_length=80)
    agent_instructions: str = Field(default="", max_length=16000)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    budget_policy: dict[str, Any] = Field(default_factory=dict)
    memory_policy: dict[str, Any] = Field(default_factory=dict)
    workflow_policy: dict[str, Any] = Field(default_factory=dict)
    resource_policy: dict[str, Any] = Field(default_factory=dict)
    project_ids: list[str] = Field(default_factory=list, max_length=100)


class BusinessUpdateRequest(StrictV31Model):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    slug: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=1000)
    icon: str | None = Field(default=None, max_length=40)
    enabled: bool | None = None
    agent_instructions: str | None = Field(default=None, max_length=16000)
    model_policy: dict[str, Any] | None = None
    budget_policy: dict[str, Any] | None = None
    memory_policy: dict[str, Any] | None = None
    workflow_policy: dict[str, Any] | None = None
    resource_policy: dict[str, Any] | None = None


class BusinessDuplicateRequest(StrictV31Model):
    new_name: str = Field(min_length=2, max_length=120)


class BusinessProjectLinkRequest(StrictV31Model):
    project_id: str = Field(min_length=1, max_length=64)


class BusinessMcpBindRequest(StrictV31Model):
    server_id: str = Field(min_length=1, max_length=128)
    enabled: bool = True
    required: bool = False
    priority: int = Field(default=100, ge=1, le=1000)
    activation_mode: Literal["required", "on_demand", "manual"] = "on_demand"
    config: dict[str, Any] = Field(default_factory=dict)


class BusinessToolPolicyRequest(StrictV31Model):
    tool_id: str = Field(min_length=1, max_length=128)
    policy: Literal["allow", "approval", "deny"]


class BusinessImportRequest(StrictV31Model):
    payload: dict[str, Any]
    new_name: str | None = Field(default=None, max_length=120)


class BusinessFromTemplateRequest(StrictV31Model):
    template_key: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=120)


class CustomTemplateCreateRequest(StrictV31Model):
    template_key: str = Field(min_length=2, max_length=80)
    name: str = Field(min_length=2, max_length=120)
    description: str = Field(default="", max_length=1000)
    icon: str = Field(default="briefcase", max_length=40)
    payload: dict[str, Any] = Field(default_factory=dict)


# --- Dynamic MCP Gateway Schemas ---

class McpGatewayServerCreateRequest(StrictV31Model):
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=1000)
    adapter_transport: Literal["stdio", "streamable_http", "native_cli", "rest_api", "local_tool"] = "streamable_http"
    endpoint: str = Field(default="", max_length=500)
    executable: str = Field(default="", max_length=300)
    args: list[str] = Field(default_factory=list, max_length=64)
    working_dir: str = Field(default="", max_length=400)
    env_allowlist: list[str] = Field(default_factory=list, max_length=40)
    secret_refs: list[str] = Field(default_factory=list, max_length=20)
    version: str = Field(default="unknown", max_length=64)
    capability_tags: list[str] = Field(default_factory=list, max_length=30)
    timeout_sec: int = Field(default=30, ge=1, le=300)
    concurrency_limit: int = Field(default=4, ge=1, le=32)
    ram_limit_mb: int = Field(default=512, ge=32, le=8192)
    cpu_limit_pct: int = Field(default=50, ge=5, le=100)
    activation_policy: Literal["on_demand", "persistent", "per_session", "shared"] = "on_demand"
    trust_tier: Literal["approved_local", "disabled"] = "approved_local"
    allowed_project_ids: list[str] = Field(default_factory=list, max_length=50)


class McpGatewayServerUpdateRequest(StrictV31Model):
    description: str | None = Field(default=None, max_length=1000)
    endpoint: str | None = Field(default=None, max_length=500)
    executable: str | None = Field(default=None, max_length=300)
    args: list[str] | None = Field(default=None, max_length=64)
    working_dir: str | None = Field(default=None, max_length=400)
    env_allowlist: list[str] | None = Field(default=None, max_length=40)
    secret_refs: list[str] | None = Field(default=None, max_length=20)
    version: str | None = Field(default=None, max_length=64)
    capability_tags: list[str] | None = Field(default=None, max_length=30)
    timeout_sec: int | None = Field(default=None, ge=1, le=300)
    concurrency_limit: int | None = Field(default=None, ge=1, le=32)
    ram_limit_mb: int | None = Field(default=None, ge=32, le=8192)
    cpu_limit_pct: int | None = Field(default=None, ge=5, le=100)
    activation_policy: Literal["on_demand", "persistent", "per_session", "shared"] | None = None
    enabled: bool | None = None
    allowed_project_ids: list[str] | None = None


class McpInvokeRequest(StrictV31Model):
    project_id: UUID
    worktree_id: UUID | None = None
    business_id: str = Field(min_length=1, max_length=64)
    session_id: UUID | None = None
    trace_id: UUID | None = None
    server_id: str = Field(min_length=1, max_length=128)
    tool_name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] = Field(default_factory=dict)
    capability: str | None = Field(default=None, max_length=80)
    approval_ref: str | None = Field(default=None, max_length=128)
    idempotency_key: str | None = Field(default=None, max_length=128)
    correlation_id: str | None = Field(default=None, max_length=128)
    timeout_sec: int | None = Field(default=None, ge=1, le=300)
    max_output_chars: int = Field(default=16000, ge=256, le=64000)


class McpBusinessRouteRequest(StrictV31Model):
    project_id: UUID
    worktree_id: UUID | None = None
    business_id: str = Field(min_length=1, max_length=64)
    task_id: str | None = Field(default=None, max_length=128)
    session_id: UUID | None = None
    task_objective: str | None = Field(default=None, max_length=2000)
    required_capabilities: list[str] = Field(default_factory=list, max_length=25)
    allow_write: bool = False
    routing_mode: Literal["automatic", "manual"] = "automatic"
    include_server_ids: list[str] = Field(default_factory=list, max_length=20)
    exclude_server_ids: list[str] = Field(default_factory=list, max_length=20)
    required_server_ids: list[str] = Field(default_factory=list, max_length=10)
    max_tools: int = Field(default=12, ge=1, le=30)


class AssetRegisterRequest(StrictV31Model):
    project_id: UUID
    source: str = Field(default="local", max_length=200)
    license: str = Field(default="unknown", max_length=120)
    file_path: str = Field(min_length=1, max_length=500)
    file_hash: str = Field(min_length=8, max_length=128)
    format: str = Field(min_length=1, max_length=40)
    version: str = Field(default="1.0", max_length=40)
    dependencies: list[str] = Field(default_factory=list, max_length=50)
    thumbnail_path: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
    approval_state: Literal["pending", "approved", "rejected"] = "pending"
