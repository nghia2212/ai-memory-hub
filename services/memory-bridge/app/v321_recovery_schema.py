"""V3.2.1 P16B — Additive SQLite Migration v15 & Strict Pydantic Schemas for Persistent Task & Auto Recovery."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

V321_P16B_RECOVERY_SQL = """
CREATE TABLE IF NOT EXISTS mcp321_recovery_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    recovery_mode TEXT NOT NULL DEFAULT 'hybrid' CHECK(recovery_mode IN ('manual', 'automatic', 'hybrid')),
    auto_resume_readonly INTEGER NOT NULL DEFAULT 1,
    auto_reconnect_alive_runner INTEGER NOT NULL DEFAULT 1,
    require_review_on_dirty_git INTEGER NOT NULL DEFAULT 1,
    require_review_on_uncertain_api INTEGER NOT NULL DEFAULT 1,
    updated_by TEXT NOT NULL DEFAULT 'system',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mcp321_task_checkpoints (
    id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('v2_task', 'agent_run', 'chat_task')),
    entity_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    plan_id TEXT,
    revision_id TEXT,
    task_id TEXT,
    attempt_id TEXT,
    session_id TEXT,
    runner_id TEXT,
    runner_pid INTEGER,
    checkpoint_version INTEGER NOT NULL DEFAULT 1,
    idempotency_key TEXT,
    execution_state TEXT NOT NULL DEFAULT 'running'
        CHECK(execution_state IN ('queued', 'running', 'interrupted', 'reconciling', 'recovery_required', 'paused', 'completed', 'canceled')),
    delivery_state TEXT NOT NULL DEFAULT 'pending'
        CHECK(delivery_state IN ('pending', 'delivered', 'missing', 'failed')),
    recovery_state TEXT NOT NULL DEFAULT 'none'
        CHECK(recovery_state IN ('none', 'reconciling', 'ready_auto_resume', 'awaiting_reconnect', 'recovery_required', 'resumed', 'paused', 'canceled')),
    completed_steps_json TEXT NOT NULL DEFAULT '[]',
    current_step_json TEXT NOT NULL DEFAULT '{}',
    pending_steps_json TEXT NOT NULL DEFAULT '[]',
    git_state_json TEXT NOT NULL DEFAULT '{}',
    test_results_json TEXT NOT NULL DEFAULT '{}',
    context_refs_json TEXT NOT NULL DEFAULT '{}',
    budget_json TEXT NOT NULL DEFAULT '{}',
    interruption_reason TEXT NOT NULL DEFAULT '',
    fencing_token INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(entity_kind, entity_id, checkpoint_version),
    UNIQUE(entity_kind, entity_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_ckpt_entity ON mcp321_task_checkpoints(entity_kind, entity_id, checkpoint_version DESC);
CREATE INDEX IF NOT EXISTS idx_mcp321_ckpt_proj ON mcp321_task_checkpoints(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS mcp321_recovery_queue (
    id TEXT PRIMARY KEY,
    entity_kind TEXT NOT NULL CHECK(entity_kind IN ('v2_task', 'agent_run', 'chat_task')),
    entity_id TEXT NOT NULL,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE SET NULL,
    plan_id TEXT,
    task_id TEXT,
    attempt_id TEXT,
    session_id TEXT,
    runner_id TEXT,
    runner_pid INTEGER,
    agent_source TEXT NOT NULL DEFAULT '',
    model_id TEXT NOT NULL DEFAULT '',
    recovery_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK(recovery_status IN ('pending_review', 'auto_resumable', 'reconnected', 'resumed', 'paused', 'canceled', 'resolved')),
    old_agent_status TEXT NOT NULL DEFAULT 'unknown'
        CHECK(old_agent_status IN ('alive', 'stopped', 'unknown', 'unreachable')),
    interruption_reason TEXT NOT NULL DEFAULT '',
    safety_classification TEXT NOT NULL DEFAULT 'needs_reconciliation'
        CHECK(safety_classification IN ('safe_readonly', 'needs_reconciliation', 'uncertain_api_inflight', 'dirty_worktree_edit', 'quota_exhausted', 'result_undelivered', 'network_interrupted')),
    latest_checkpoint_id TEXT REFERENCES mcp321_task_checkpoints(id) ON DELETE SET NULL,
    completed_steps_json TEXT NOT NULL DEFAULT '[]',
    pending_steps_json TEXT NOT NULL DEFAULT '[]',
    git_state_json TEXT NOT NULL DEFAULT '{}',
    budget_json TEXT NOT NULL DEFAULT '{}',
    delivery_state TEXT NOT NULL DEFAULT 'pending',
    fencing_token INTEGER NOT NULL DEFAULT 1,
    recommended_action TEXT NOT NULL DEFAULT 'review'
        CHECK(recommended_action IN ('resume', 'reconnect', 'deliver_result', 'review', 'change_agent', 'wait_quota')),
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(entity_kind, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_mcp321_recq_proj ON mcp321_recovery_queue(project_id, recovery_status, updated_at DESC);
"""


class StrictRecoveryModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class RecoveryPolicyUpdateRequest(StrictRecoveryModel):
    recovery_mode: Literal["manual", "automatic", "hybrid"] = "hybrid"
    auto_resume_readonly: bool = True
    auto_reconnect_alive_runner: bool = True
    require_review_on_dirty_git: bool = True
    require_review_on_uncertain_api: bool = True


class TaskCheckpointCreateRequest(StrictRecoveryModel):
    entity_kind: Literal["v2_task", "agent_run", "chat_task"]
    entity_id: str = Field(min_length=2, max_length=120)
    project_id: UUID
    worktree_id: UUID | None = None
    plan_id: str | None = Field(default=None, max_length=120)
    revision_id: str | None = Field(default=None, max_length=120)
    task_id: str | None = Field(default=None, max_length=120)
    attempt_id: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=120)
    runner_id: str | None = Field(default=None, max_length=120)
    runner_pid: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = Field(default=None, max_length=128)
    execution_state: Literal["queued", "running", "interrupted", "reconciling", "recovery_required", "paused", "completed", "canceled"] = "running"
    delivery_state: Literal["pending", "delivered", "missing", "failed"] = "pending"
    recovery_state: Literal["none", "reconciling", "ready_auto_resume", "awaiting_reconnect", "recovery_required", "resumed", "paused", "canceled"] = "none"
    completed_steps: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    current_step: dict[str, Any] = Field(default_factory=dict)
    pending_steps: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    git_state: dict[str, Any] = Field(default_factory=dict)
    test_results: dict[str, Any] = Field(default_factory=dict)
    context_refs: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any] = Field(default_factory=dict)
    interruption_reason: str = Field(default="", max_length=500)
    fencing_token: int | None = Field(default=None, ge=1)


class RecoveryResumeRequest(StrictRecoveryModel):
    project_id: UUID
    confirm_uncertain_retry: bool = False
    confirm_dirty_worktree: bool = False
    note: str = Field(default="", max_length=500)


class RecoveryActionRequest(StrictRecoveryModel):
    project_id: UUID
    note: str = Field(default="", max_length=500)


class RecoveryChangeAgentRequest(StrictRecoveryModel):
    project_id: UUID
    execution_source: str = Field(min_length=2, max_length=64)
    model_id: str = Field(min_length=1, max_length=120)
    allow_paid_api_consent: bool = False
    note: str = Field(default="", max_length=500)


class RecoveryScanRequest(StrictRecoveryModel):
    project_id: UUID | None = None
    check_host_runner: bool = True
    apply_auto_policy: bool = True
