"""Frozen V2 shared Pydantic contracts and provider-neutral Protocols.

Owned by Lead (`lead/v2-orchestrator`). Agent A (catalog/routing/forecast),
Agent B (bridge storage/Vietnamese dashboard), and Agent C (planner/host runner)
MUST import and conform to these types without inventing incompatible fields.
"""
from __future__ import annotations

from typing import Any, Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictV2Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Enums / Literals ---
AuthMode = Literal["api_key", "subscription_cli", "oauth", "unknown"]
ModelStatus = Literal["candidate", "verified", "deprecated", "unavailable"]
TaskType = Literal[
    "architecture",
    "complex_reasoning",
    "implementation",
    "ui",
    "debugging",
    "testing",
    "review",
    "documentation",
    "routine",
]
ComplexityTier = Literal["XS", "S", "M", "L", "XL"]
MeasurementKind = Literal["measured", "estimated", "unavailable"]
ConfidenceTier = Literal["high", "medium", "low", "cold_start", "uncalibrated"]
BrainRole = Literal[
    "tier0_rules",
    "tier1_light",
    "tier2_primary",
    "tier3_advanced",
    "secondary_fallback",
]


# --- Model Catalog & Routing ---
class ModelRecord(StrictV2Model):
    provider: str = Field(min_length=1, max_length=64)
    model_id: str = Field(min_length=1, max_length=128)
    cli_family: str | None = Field(default=None, max_length=64)
    cli_version: str | None = Field(default=None, max_length=64)
    auth_mode: AuthMode = "unknown"
    status: ModelStatus = "candidate"
    context_limit: int | None = Field(default=None, ge=1)
    output_limit: int | None = Field(default=None, ge=1)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    price_input_per_1m: float | None = Field(default=None, ge=0.0)
    price_output_per_1m: float | None = Field(default=None, ge=0.0)
    price_cache_read_per_1m: float | None = Field(default=None, ge=0.0)
    price_cache_write_per_1m: float | None = Field(default=None, ge=0.0)
    currency: str | None = Field(default="USD", max_length=8)
    effective_date: str | None = None
    source_url: str | None = None
    verified_at: str | None = None
    expires_at: str | None = None


class RoutingRecommendation(StrictV2Model):
    task_type: TaskType
    complexity: ComplexityTier
    provider: str
    model_id: str
    effort: str | None = None
    auth_mode: AuthMode
    rationale: str
    evidence_kind: Literal["observed", "synthetic_benchmark", "hypothesis"]
    cold_start: bool = False
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


# --- Task Features, Usage, Quotas & Forecasts ---
class PreExecutionFeatures(StrictV2Model):
    rubric_version: str = "v2.0"
    task_type: TaskType
    complexity: ComplexityTier
    language: str | None = None
    file_count: int = Field(default=0, ge=0)
    total_file_bytes: int = Field(default=0, ge=0)
    expected_edits: int = Field(default=0, ge=0)
    expected_tests: int = Field(default=0, ge=0)
    dependency_count: int = Field(default=0, ge=0)
    context_pack_tokens: int | None = Field(default=None, ge=0)
    context_pack_is_approx: bool = True
    uncertainty_level: Literal["low", "medium", "high"] = "medium"


class TokenUsageCounters(StrictV2Model):
    """Distinct counters; unknown values MUST be None (never 0)."""

    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)
    reasoning_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)
    measurement_kind: MeasurementKind = "unavailable"
    counter_mode: Literal["delta", "cumulative"] = "delta"
    source: str = "unknown"
    observed_at: str | None = None


class QuotaSnapshot(StrictV2Model):
    provider: str
    auth_mode: AuthMode
    account_bucket: str | None = None
    quota_kind: Literal["subscription_quota", "api_budget", "rate_limit", "context_window"]
    unit: str
    numerator: float | None = Field(default=None, ge=0.0)
    denominator: float | None = Field(default=None, gt=0.0)
    window_label: str | None = None
    reset_at: str | None = None
    source: str
    confounded: bool = False
    observed_at: str


class TaskForecast(StrictV2Model):
    task_key: str
    provider: str
    model_id: str
    effort: str | None = None
    auth_mode: AuthMode
    complexity: ComplexityTier
    confidence: ConfidenceTier
    sample_count: int = Field(ge=0)
    input_tokens_p50: int | None = Field(default=None, ge=0)
    input_tokens_p90: int | None = Field(default=None, ge=0)
    output_tokens_p50: int | None = Field(default=None, ge=0)
    output_tokens_p90: int | None = Field(default=None, ge=0)
    cache_read_tokens_p50: int | None = Field(default=None, ge=0)
    peak_context_tokens_p90: int | None = Field(default=None, ge=0)
    cost_usd_p50: float | None = Field(default=None, ge=0.0)
    cost_usd_p90: float | None = Field(default=None, ge=0.0)
    quota_impact_pct: float | None = Field(default=None, ge=0.0)
    quota_unit: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)


# --- Plan, Approval & Runner Contracts ---
class PlanTaskSpec(StrictV2Model):
    task_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=4000)
    dependencies: list[str] = Field(default_factory=list)
    owned_paths: list[str] = Field(default_factory=list)
    acceptance_checks: list[str] = Field(default_factory=list)
    features: PreExecutionFeatures
    assigned_agent: str
    assigned_provider: str
    assigned_model_id: str
    assigned_effort: str | None = None
    auth_mode: AuthMode = "subscription_cli"
    budget_input_tokens: int | None = Field(default=None, ge=1)
    budget_output_tokens: int | None = Field(default=None, ge=1)
    budget_cost_usd: float | None = Field(default=None, ge=0.0)
    max_retries: int = Field(default=2, ge=0, le=5)


class PlanCreateRequest(StrictV2Model):
    project_id: UUID
    worktree_id: UUID
    objective: str = Field(min_length=1, max_length=4000)
    catalog_snapshot_id: str | None = None
    tasks: list[PlanTaskSpec] = Field(min_length=1, max_length=50)
    forecast_id: str | None = None
    planner_session_id: str | None = None


class PlanApproveRequest(StrictV2Model):
    revision_id: UUID
    revision_hash: str = Field(min_length=16, max_length=128)
    actor: str = Field(min_length=1, max_length=128)
    mode: Literal["manual", "auto_scoped"] = "manual"
    auto_expires_at: str | None = None
    reason: str | None = Field(default=None, max_length=500)


# --- Provider-Neutral Adaptive Brain Protocol (ADAPTIVE-BRAIN-V2.md) ---
class BrainRequest(StrictV2Model):
    project_id: UUID
    worktree_id: UUID
    role: BrainRole
    objective: str = Field(min_length=1, max_length=4000)
    context_pack: dict[str, Any] = Field(default_factory=dict)
    max_output_tokens: int = Field(default=1024, ge=64, le=8192)
    timeout_seconds: float = Field(default=30.0, ge=1.0, le=180.0)


class BrainResponse(StrictV2Model):
    provider: str
    model_id: str
    effort: str | None = None
    auth_mode: AuthMode
    upstream_session_id: str | None = None
    finish_status: Literal["ok", "invalid_json", "timeout", "unavailable", "budget_exceeded", "error"]
    structured_output: dict[str, Any] = Field(default_factory=dict)
    usage: TokenUsageCounters
    error_code: str | None = None
    error_message: str | None = None


class BrainAdapter(Protocol):
    """Provider-neutral interface for planning/review brains (rules, CLI, or opt-in API)."""

    def discover_capabilities(self) -> list[ModelRecord]:
        ...

    def invoke(self, request: BrainRequest) -> BrainResponse:
        ...
