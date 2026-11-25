"""V3 additive SQLite migrations (v5 Dynamic MCP Registry, v6 Conversation History + FTS5) and strict Pydantic schemas."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

V3_MCP_SQL = """
CREATE TABLE IF NOT EXISTS v3_mcp_servers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL CHECK(transport IN ('streamable_http', 'stdio')),
    endpoint_or_cmd TEXT NOT NULL,
    version TEXT NOT NULL DEFAULT 'unknown',
    trust_tier TEXT NOT NULL CHECK(trust_tier IN ('builtin', 'approved_local', 'disabled')),
    approval_state TEXT NOT NULL CHECK(approval_state IN ('approved', 'pending_approval', 'revoked')),
    allowed_project_ids_json TEXT NOT NULL DEFAULT '[]',
    health_status TEXT NOT NULL DEFAULT 'unknown',
    last_latency_ms INTEGER,
    last_checked_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v3_mcp_tools (
    id TEXT PRIMARY KEY,
    server_id TEXT NOT NULL REFERENCES v3_mcp_servers(id) ON DELETE CASCADE,
    tool_name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    input_schema_hash TEXT NOT NULL DEFAULT '',
    capability_tags_json TEXT NOT NULL DEFAULT '[]',
    side_effect_level TEXT NOT NULL CHECK(side_effect_level IN ('read_only', 'write_scoped', 'restricted')),
    requires_approval INTEGER NOT NULL DEFAULT 0,
    discovered_at TEXT NOT NULL,
    UNIQUE(server_id, tool_name)
);
CREATE INDEX IF NOT EXISTS idx_v3_mcp_tools_server ON v3_mcp_tools(server_id);

CREATE TABLE IF NOT EXISTS v3_mcp_audit (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    worktree_id TEXT,
    session_id TEXT,
    trace_id TEXT,
    server_id TEXT,
    tool_name TEXT,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    latency_ms INTEGER,
    error_code TEXT,
    actor TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v3_mcp_audit_time ON v3_mcp_audit(created_at DESC);
"""

V3_CONV_SQL = """
CREATE TABLE IF NOT EXISTS v3_conversations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    client_type TEXT NOT NULL CHECK(client_type IN ('antigravity', 'claude', 'codex', 'gemini', 'manual_import')),
    external_conversation_id TEXT NOT NULL,
    session_id TEXT,
    plan_id TEXT,
    task_id TEXT,
    provider TEXT,
    model_id TEXT,
    title TEXT NOT NULL,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_id, worktree_id, client_type, external_conversation_id)
);
CREATE INDEX IF NOT EXISTS idx_v3_conv_scope ON v3_conversations(project_id, worktree_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS v3_conversation_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES v3_conversations(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    seq_no INTEGER NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
    visible_text TEXT NOT NULL,
    model_id TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, content_hash)
);
CREATE INDEX IF NOT EXISTS idx_v3_conv_msg_conv ON v3_conversation_messages(conversation_id, seq_no ASC);

CREATE VIRTUAL TABLE IF NOT EXISTS v3_conversation_messages_fts USING fts5(
    message_id UNINDEXED,
    conversation_id UNINDEXED,
    project_id UNINDEXED,
    worktree_id UNINDEXED,
    role UNINDEXED,
    visible_text,
    tokenize='unicode61'
);
"""


class StrictV3Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class McpToolSpec(StrictV3Model):
    tool_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=500)
    input_schema_hash: str = Field(default="", max_length=128)
    capability_tags: list[str] = Field(default_factory=list, max_length=20)
    side_effect_level: Literal["read_only", "write_scoped", "restricted"] = "read_only"
    requires_approval: bool = False


class McpServerRegisterRequest(StrictV3Model):
    name: str = Field(min_length=1, max_length=128)
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    endpoint_or_cmd: str = Field(min_length=1, max_length=500)
    version: str = Field(default="unknown", max_length=64)
    allowed_project_ids: list[str] = Field(default_factory=list, max_length=50)
    tools: list[McpToolSpec] = Field(default_factory=list, max_length=100)


class McpApprovalRequest(StrictV3Model):
    decision: Literal["approve", "revoke"]
    allowed_project_ids: list[str] | None = None
    actor: str = Field(default="admin", min_length=1, max_length=128)


class McpRouteRequest(StrictV3Model):
    project_id: UUID
    worktree_id: UUID | None = None
    required_capabilities: list[str] = Field(min_length=1, max_length=20)
    allow_write: bool = False


class ConversationMessageIn(StrictV3Model):
    seq_no: int = Field(ge=0)
    role: Literal["user", "assistant"]
    visible_text: str = Field(min_length=1, max_length=16000)
    content_hash: str = Field(min_length=8, max_length=128)
    created_at: str = Field(min_length=1, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ConversationIngestRequest(StrictV3Model):
    project_id: UUID
    worktree_id: UUID
    client_type: Literal["antigravity", "claude", "codex", "gemini", "manual_import"]
    external_conversation_id: str = Field(min_length=1, max_length=200)
    session_id: UUID | None = None
    plan_id: UUID | None = None
    task_id: UUID | None = None
    provider: str | None = Field(default=None, max_length=64)
    model_id: str | None = Field(default=None, max_length=128)
    title: str = Field(min_length=1, max_length=240)
    messages: list[ConversationMessageIn] = Field(min_length=1, max_length=500)
    provenance: dict[str, Any] = Field(default_factory=dict)


class ConversationPromoteRequest(StrictV3Model):
    project_id: UUID
    message_id: UUID
    title: str = Field(min_length=3, max_length=200)
    actor: str = Field(default="admin", min_length=1, max_length=128)
