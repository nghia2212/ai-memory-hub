"""V2 Pydantic schemas importing frozen Lead contracts from services/orchestrator/contracts.py."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

_HERE = Path(__file__).resolve()
for _candidate in (_HERE.parents[1], *(_HERE.parents)):
    if (_candidate / "services" / "orchestrator" / "contracts.py").is_file():
        if str(_candidate) not in sys.path:
            sys.path.insert(0, str(_candidate))
        break

from services.orchestrator.contracts import (  # noqa: E402
    AuthMode,
    BrainPolicyConfig,
    BrainRole,
    BrainRoleConfig,
    BrainTokenAccounting,
    ComplexityTier,
    ConfidenceTier,
    MeasurementKind,
    ModelRecord,
    ModelStatus,
    PlanApproveRequest,
    PlanCreateRequest,
    PlanTaskSpec,
    PreExecutionFeatures,
    QuotaSnapshot,
    RoutingRecommendation,
    StrictV2Model,
    TaskForecast,
    TaskType,
    TokenUsageCounters,
)


class CatalogRefreshRequest(StrictV2Model):
    version: int = Field(default=1, ge=1)
    source_urls: list[str] = Field(default_factory=list, max_length=20)
    models: list[ModelRecord] = Field(default_factory=list, max_length=200)
    expires_at: str | None = None
    is_stale: bool = False


class EvaluationCreateRequest(StrictV2Model):
    project_id: UUID | None = None
    model_id: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    auth_mode: AuthMode = "subscription_cli"
    effort: str | None = Field(default=None, max_length=32)
    task_type: TaskType
    complexity: ComplexityTier
    evidence_kind: Literal["observed", "synthetic_benchmark", "hypothesis"] = "observed"
    correctness_score: float | None = Field(default=None, ge=0.0, le=1.0)
    acceptance_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    avg_latency_ms: float | None = Field(default=None, ge=0.0)
    avg_effective_cost_usd: float | None = Field(default=None, ge=0.0)
    failure_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    sample_count: int = Field(default=1, ge=0)
    provenance: dict[str, Any] = Field(default_factory=dict)


class RoutingRecommendRequest(StrictV2Model):
    project_id: UUID
    task_type: TaskType
    complexity: ComplexityTier
    allowed_auth_modes: list[AuthMode] | None = None
    max_cost_usd: float | None = Field(default=None, ge=0.0)


class PolicyUpdateRequest(StrictV2Model):
    project_id: UUID
    approval_mode: Literal["REVIEW_REQUIRED", "AUTO_SCOPED"] = "REVIEW_REQUIRED"
    auto_expires_at: str | None = None
    auto_persist_across_restart: bool = False
    kill_switch: bool = False
    max_concurrency: int = Field(default=2, ge=1, le=8)
    max_task_cost_usd: float | None = Field(default=None, ge=0.0)
    max_daily_cost_usd: float | None = Field(default=None, ge=0.0)
    max_daily_planner_cost_usd: float | None = Field(default=None, ge=0.0)
    allow_paid_api_fallback: bool = False
    allowed_auth_modes: list[AuthMode] = Field(default_factory=lambda: ["subscription_cli"])
    retention_days: int = Field(default=180, ge=1, le=3650)
    allow_cross_project_aggregates: bool = False
    actor: str = Field(default="admin", min_length=1, max_length=128)


class BrainConfigUpdateRequest(BrainPolicyConfig):
    actor: str = Field(default="admin", min_length=1, max_length=128)


class PlanRevisionUpdateRequest(StrictV2Model):
    project_id: UUID
    tasks: list[PlanTaskSpec] = Field(min_length=1, max_length=50)
    forecast_id: str | None = None
    planner_session_id: str | None = None
    created_by: str = Field(default="admin", min_length=1, max_length=128)


class PlanLifecycleRequest(StrictV2Model):
    project_id: UUID
    actor: str = Field(default="admin", min_length=1, max_length=128)
    reason: str | None = Field(default=None, max_length=500)


class JobClaimRequest(StrictV2Model):
    plan_id: UUID | None = None
    project_id: UUID
    worktree_id: UUID
    runner_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class JobHeartbeatRequest(StrictV2Model):
    project_id: UUID
    lease_id: UUID
    runner_id: str = Field(min_length=1, max_length=128)
    lease_seconds: int = Field(default=300, ge=30, le=3600)
    peak_context_tokens: int | None = Field(default=None, ge=0)


class JobEventRequest(StrictV2Model):
    project_id: UUID
    lease_id: UUID
    kind: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)


class JobCheckpointRequest(StrictV2Model):
    project_id: UUID
    lease_id: UUID
    handoff_summary: str = Field(min_length=1, max_length=4000)
    peak_context_tokens: int | None = Field(default=None, ge=0)
    idempotency_key: str = Field(min_length=1, max_length=128)


class JobCompleteRequest(StrictV2Model):
    project_id: UUID
    lease_id: UUID
    outcome: Literal["succeeded", "failed", "cancelled", "budget_exceeded"]
    accepted: bool | None = None
    tests_passed: bool | None = None
    duration_ms: int | None = Field(default=None, ge=0)
    actual_changed_files: int | None = Field(default=None, ge=0)
    peak_context_tokens: int | None = Field(default=None, ge=0)
    handoff_summary: str | None = Field(default=None, max_length=4000)
    error_code: str | None = Field(default=None, max_length=64)
    error_summary: str | None = Field(default=None, max_length=1000)


class SessionUpsertRequest(StrictV2Model):
    session_id: UUID | None = None
    project_id: UUID
    worktree_id: UUID
    plan_id: UUID | None = None
    plan_revision_id: UUID | None = None
    task_id: UUID | None = None
    attempt_id: UUID | None = None
    parent_session_id: UUID | None = None
    role: Literal["planner", "executor", "reviewer", "manual_cli"]
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    cli_version: str | None = Field(default=None, max_length=64)
    reasoning_effort: str | None = Field(default=None, max_length=32)
    auth_mode: AuthMode = "subscription_cli"
    account_bucket: str | None = Field(default=None, max_length=64)
    upstream_session_id: str | None = Field(default=None, max_length=128)
    git_head: str | None = Field(default=None, max_length=64)
    dirty_fingerprint: str | None = Field(default=None, max_length=128)
    status: Literal["running", "completed", "checkpointed", "failed", "cancelled", "partial"] = "running"
    unlinked: bool = False
    incomplete_coverage: bool = False
    features: PreExecutionFeatures | None = None


class UsageEventCreateRequest(StrictV2Model):
    session_id: UUID
    project_id: UUID
    worktree_id: UUID
    source_event_id: str = Field(min_length=1, max_length=128)
    sequence_no: int = Field(default=0, ge=0)
    pricing_version: str | None = Field(default=None, max_length=64)
    usage: TokenUsageCounters


class QuotaSnapshotCreateRequest(QuotaSnapshot):
    pass


class ForecastComputeRequest(StrictV2Model):
    project_id: UUID
    worktree_id: UUID
    plan_id: UUID | None = None
    plan_revision_id: UUID | None = None
    tasks: list[PlanTaskSpec] = Field(min_length=1, max_length=50)
    persist: bool = True
