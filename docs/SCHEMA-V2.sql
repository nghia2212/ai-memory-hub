-- AI Memory Hub V2 Frozen SQLite Schema (Migration v3+, Bridge Sole Writer)
-- Requires: PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;

-- 1. Model Catalog & Evaluations
CREATE TABLE IF NOT EXISTS v2_catalog_snapshots (
    id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    source_urls_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    is_stale INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_models (
    id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES v2_catalog_snapshots(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    cli_family TEXT,
    cli_version TEXT,
    auth_mode TEXT NOT NULL, -- api_key | subscription_cli | oauth | unknown
    status TEXT NOT NULL CHECK(status IN ('candidate', 'verified', 'deprecated', 'unavailable')),
    context_limit INTEGER,
    output_limit INTEGER,
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    price_input_per_1m REAL,
    price_output_per_1m REAL,
    price_cache_read_per_1m REAL,
    price_cache_write_per_1m REAL,
    currency TEXT,
    effective_date TEXT,
    source_url TEXT,
    verified_at TEXT,
    expires_at TEXT,
    UNIQUE(snapshot_id, provider, model_id, auth_mode)
);
CREATE INDEX IF NOT EXISTS idx_v2_models_lookup ON v2_models(provider, model_id, status);

CREATE TABLE IF NOT EXISTS v2_model_evaluations (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    model_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    effort TEXT,
    task_type TEXT NOT NULL,
    complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
    evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('observed', 'synthetic_benchmark', 'hypothesis')),
    correctness_score REAL,
    acceptance_rate REAL,
    avg_latency_ms REAL,
    avg_effective_cost_usd REAL,
    failure_rate REAL,
    sample_count INTEGER NOT NULL DEFAULT 0,
    provenance_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_eval_lookup ON v2_model_evaluations(task_type, complexity, model_id);

-- 2. Project Policies & Adaptive Brain Configuration
CREATE TABLE IF NOT EXISTS v2_policies (
    project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    approval_mode TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED' CHECK(approval_mode IN ('REVIEW_REQUIRED', 'AUTO_SCOPED')),
    auto_expires_at TEXT,
    auto_persist_across_restart INTEGER NOT NULL DEFAULT 0,
    kill_switch INTEGER NOT NULL DEFAULT 0,
    max_concurrency INTEGER NOT NULL DEFAULT 2,
    max_task_cost_usd REAL,
    max_daily_cost_usd REAL,
    max_daily_planner_cost_usd REAL,
    allow_paid_api_fallback INTEGER NOT NULL DEFAULT 0,
    allowed_auth_modes_json TEXT NOT NULL DEFAULT '["subscription_cli"]',
    retention_days INTEGER NOT NULL DEFAULT 180,
    allow_cross_project_aggregates INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_brain_roles (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    role_name TEXT NOT NULL CHECK(role_name IN ('tier0_rules', 'tier1_light', 'tier2_primary', 'tier3_advanced', 'secondary_fallback')),
    preferred_label TEXT,
    effective_provider TEXT,
    effective_model_id TEXT,
    effective_effort TEXT,
    auth_mode TEXT,
    availability_status TEXT NOT NULL DEFAULT 'unverified',
    availability_reason TEXT,
    fallback_order_json TEXT NOT NULL DEFAULT '[]',
    max_escalations INTEGER NOT NULL DEFAULT 1,
    last_verified_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, role_name)
);

-- 3. Plans, Revisions, Approvals, DAG Tasks & Leases
CREATE TABLE IF NOT EXISTS v2_plans (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    objective TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('DRAFT', 'REVIEW_REQUIRED', 'APPROVED', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED', 'REJECTED')),
    current_revision_id TEXT,
    approved_revision_id TEXT,
    catalog_snapshot_id TEXT REFERENCES v2_catalog_snapshots(id),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_plans_scope ON v2_plans(project_id, worktree_id, status);

CREATE TABLE IF NOT EXISTS v2_plan_revisions (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
    revision_number INTEGER NOT NULL,
    revision_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    forecast_id TEXT,
    planner_session_id TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(plan_id, revision_number)
);

CREATE TABLE IF NOT EXISTS v2_plan_approvals (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id) ON DELETE CASCADE,
    revision_hash TEXT NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected', 'revoked')),
    actor TEXT NOT NULL,
    mode TEXT NOT NULL CHECK(mode IN ('manual', 'auto_scoped')),
    reason TEXT,
    expires_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_tasks (
    id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
    revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    task_key TEXT NOT NULL,
    title TEXT NOT NULL,
    objective TEXT NOT NULL,
    task_type TEXT NOT NULL,
    complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
    assigned_agent TEXT NOT NULL,
    assigned_provider TEXT NOT NULL,
    assigned_model_id TEXT NOT NULL,
    assigned_effort TEXT,
    auth_mode TEXT NOT NULL,
    owned_paths_json TEXT NOT NULL DEFAULT '[]',
    acceptance_checks_json TEXT NOT NULL DEFAULT '[]',
    budget_input_tokens INTEGER,
    budget_output_tokens INTEGER,
    budget_cost_usd REAL,
    max_retries INTEGER NOT NULL DEFAULT 2,
    state TEXT NOT NULL CHECK(state IN ('queued', 'leased', 'running', 'checkpointed', 'succeeded', 'failed', 'cancelled', 'blocked')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_tasks_plan ON v2_tasks(plan_id, revision_id, state);

CREATE TABLE IF NOT EXISTS v2_task_dependencies (
    task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
    PRIMARY KEY(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS v2_task_attempts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    plan_revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id),
    plan_revision_hash TEXT NOT NULL,
    runner_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('claimed', 'running', 'checkpointed', 'succeeded', 'failed', 'cancelled', 'budget_exceeded', 'orphaned')),
    started_at TEXT NOT NULL,
    ended_at TEXT,
    error_code TEXT,
    error_summary TEXT,
    UNIQUE(task_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS v2_job_leases (
    lease_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL UNIQUE REFERENCES v2_task_attempts(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
    runner_id TEXT NOT NULL,
    claimed_at TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    released_at TEXT
);

-- 4. Durable Session Ledger, Features, Outcomes, Usage & Quotas
CREATE TABLE IF NOT EXISTS v2_sessions (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    plan_id TEXT REFERENCES v2_plans(id) ON DELETE SET NULL,
    plan_revision_id TEXT REFERENCES v2_plan_revisions(id) ON DELETE SET NULL,
    task_id TEXT REFERENCES v2_tasks(id) ON DELETE SET NULL,
    attempt_id TEXT REFERENCES v2_task_attempts(id) ON DELETE SET NULL,
    parent_session_id TEXT REFERENCES v2_sessions(id) ON DELETE SET NULL,
    role TEXT NOT NULL CHECK(role IN ('planner', 'executor', 'reviewer', 'manual_cli')),
    provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    cli_version TEXT,
    reasoning_effort TEXT,
    auth_mode TEXT NOT NULL,
    account_bucket TEXT,
    upstream_session_id TEXT,
    git_head TEXT,
    dirty_fingerprint TEXT,
    status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'checkpointed', 'failed', 'cancelled', 'partial')),
    unlinked INTEGER NOT NULL DEFAULT 0,
    incomplete_coverage INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_v2_sessions_cohort ON v2_sessions(project_id, provider, model_id, auth_mode, started_at);

CREATE TABLE IF NOT EXISTS v2_task_features (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES v2_sessions(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES v2_tasks(id) ON DELETE CASCADE,
    phase TEXT NOT NULL CHECK(phase IN ('planned', 'actual')),
    rubric_version TEXT NOT NULL,
    task_type TEXT NOT NULL,
    complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
    language TEXT,
    file_count INTEGER,
    total_file_bytes INTEGER,
    expected_edits INTEGER,
    expected_tests INTEGER,
    dependency_count INTEGER,
    context_pack_tokens INTEGER,
    context_pack_is_approx INTEGER NOT NULL DEFAULT 1,
    uncertainty_level TEXT CHECK(uncertainty_level IN ('low', 'medium', 'high')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_session_outcomes (
    session_id TEXT PRIMARY KEY REFERENCES v2_sessions(id) ON DELETE CASCADE,
    accepted INTEGER, -- 1=accepted, 0=rejected, NULL=pending/unknown
    tests_passed INTEGER,
    duration_ms INTEGER,
    retries INTEGER NOT NULL DEFAULT 0,
    rework_count INTEGER NOT NULL DEFAULT 0,
    actual_changed_files INTEGER,
    peak_context_tokens INTEGER,
    handoff_summary TEXT,
    recorded_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_usage_events (
    id TEXT PRIMARY KEY,
    source_event_id TEXT NOT NULL,
    session_id TEXT NOT NULL REFERENCES v2_sessions(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
    sequence_no INTEGER NOT NULL DEFAULT 0,
    counter_mode TEXT NOT NULL CHECK(counter_mode IN ('delta', 'cumulative')),
    measurement_kind TEXT NOT NULL CHECK(measurement_kind IN ('measured', 'estimated', 'unavailable')),
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_write_tokens INTEGER,
    reasoning_tokens INTEGER,
    cost_usd REAL,
    pricing_version TEXT,
    unit TEXT NOT NULL DEFAULT 'tokens',
    source TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(session_id, source_event_id)
);
CREATE INDEX IF NOT EXISTS idx_v2_usage_session ON v2_usage_events(session_id, sequence_no);

CREATE TABLE IF NOT EXISTS v2_quota_snapshots (
    id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    account_bucket TEXT,
    quota_kind TEXT NOT NULL CHECK(quota_kind IN ('subscription_quota', 'api_budget', 'rate_limit', 'context_window')),
    unit TEXT NOT NULL,
    numerator REAL,
    denominator REAL,
    window_label TEXT,
    reset_at TEXT,
    source TEXT NOT NULL,
    confounded INTEGER NOT NULL DEFAULT 0,
    observed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_quotas_latest ON v2_quota_snapshots(provider, auth_mode, quota_kind, observed_at DESC);

CREATE TABLE IF NOT EXISTS v2_budget_reservations (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
    task_id TEXT REFERENCES v2_tasks(id) ON DELETE CASCADE,
    provider TEXT NOT NULL,
    auth_mode TEXT NOT NULL,
    reserved_cost_usd REAL,
    reserved_tokens INTEGER,
    actual_cost_usd REAL,
    actual_tokens INTEGER,
    status TEXT NOT NULL CHECK(status IN ('reserved', 'settled', 'released')),
    created_at TEXT NOT NULL,
    settled_at TEXT
);

-- 5. Immutable Forecasts & Actuals
CREATE TABLE IF NOT EXISTS v2_forecast_snapshots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    plan_id TEXT REFERENCES v2_plans(id) ON DELETE SET NULL,
    plan_revision_id TEXT REFERENCES v2_plan_revisions(id) ON DELETE SET NULL,
    forecast_version TEXT NOT NULL,
    confidence TEXT NOT NULL CHECK(confidence IN ('high', 'medium', 'low', 'cold_start', 'uncalibrated')),
    sample_count INTEGER NOT NULL,
    cohort_criteria_json TEXT NOT NULL,
    forecast_json TEXT NOT NULL,
    assumptions_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v2_forecast_actuals (
    forecast_id TEXT PRIMARY KEY REFERENCES v2_forecast_snapshots(id) ON DELETE CASCADE,
    actual_input_tokens INTEGER,
    actual_output_tokens INTEGER,
    actual_cache_read_tokens INTEGER,
    actual_cache_write_tokens INTEGER,
    actual_total_tokens INTEGER,
    actual_cost_usd REAL,
    within_p90 INTEGER,
    abs_token_error INTEGER,
    reconciled_at TEXT NOT NULL
);

-- 6. Append-Only Audit Log
CREATE TABLE IF NOT EXISTS v2_audit_log (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_v2_audit_time ON v2_audit_log(project_id, created_at DESC);
