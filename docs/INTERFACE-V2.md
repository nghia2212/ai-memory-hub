# Contract V2 — Intelligent Multi-Agent Orchestrator, Session Analytics & Adaptive Brain (Lead-Owned, Frozen)

- **Base V1/V1.1 Integration SHA:** `1ff4bb27167ecdda02767d1ac61b5965e54c7588` (`lead/v1-integration`)
- **Integration Branch:** `lead/v2-orchestrator`
- **Governing Specs:** `docs/ORCHESTRATOR-V2.md`, `docs/SESSION-ANALYTICS-V2.md`, `docs/ADAPTIVE-BRAIN-V2.md`
- **Shared Schema & Types:** `docs/SCHEMA-V2.sql`, `services/orchestrator/contracts.py`

## 1. Security & Auth Scopes

All `/v2/*` endpoints require `Authorization: Bearer <token>` and strict `project_id` / `worktree_id` scope isolation (except global read-only catalog endpoints). Unknown/mismatched scope returns `404`; invalid/extra fields (`extra="forbid"`) return `422`; unauthenticated `401`; forbidden `403`; conflict/revision tamper `409`; unavailable `503`.

| Scope | Allowed Operations |
| :--- | :--- |
| `read` | Read catalog, evaluations, plans, tasks, sessions, usage, quotas, forecasts, analytics, policies |
| `write` | Submit telemetry/usage/session records within project scope |
| `planner` | Propose plans/revisions, compute forecasts, record planner sessions (NEVER execute host commands or self-approve) |
| `runner` | Claim approved job leases, heartbeat, emit execution events/checkpoints, submit outcomes/telemetry (NEVER claim unapproved plans or arbitrary shell commands) |
| `admin` | Refresh catalog, approve/reject/pause/resume/cancel plans, update policies/brain config, toggle auto mode, delete retained data |

## 2. Core Metric & Privacy Invariants

- **No fake numbers:** Missing counters are `null` (never `0`). Unavailable quota/limit displays `'Không có dữ liệu'` (never `0%`).
- **Metric semantics:** Track `input_tokens`, `output_tokens`, `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` distinctly with `measurement_kind` (`measured | estimated | unavailable`), `unit`, `source`, `observed_at`. Never double-count cached input or reasoning subsets.
- **Separate percentages:** `context_occupancy_pct`, `subscription_quota_pct`, `api_budget_pct`, `rate_limit_pct` are strictly separate fields with explicit `numerator`, `denominator`, `window`, `reset_at`, `source`, `observed_at`. Never infer subscription quota from token count or context window.
- **No silent paid fallback:** Paid API fallback (e.g., DeepSeek) is disabled unless explicitly approved per project and budget-bounded.
- **No raw transcripts by default:** Store metadata + bounded redacted handoff only.

## 3. REST JSON `/v2` Endpoints (Memory Bridge Sole SQLite Writer)

### Model Registry & Evaluation (Agent A producer → Agent B storage)
- `GET /v2/catalog` (`read`): Latest or specific `snapshot_id`, freshness, stale flag, models list.
- `POST /v2/catalog/refresh` (`admin`): Trigger/persist validated official catalog snapshot (`snapshot_id`, `source_urls`, `models[]`, `fetched_at`, `expires_at`). New models enter `candidate` quarantine; retired models alert without silent switching.
- `GET /v2/evaluations` (`read`): Query model suitability matrix by `task_type`, `model_id`, `auth_mode` (distinguishing `observed` vs `hypothesis`).
- `POST /v2/evaluations` (`admin` / `planner`): Persist reproducible benchmark or local outcome evaluation sample.
- `POST /v2/routing/recommend` (`read` / `planner`): Deterministic routing recommendation for `(project_id, task_type, complexity, constraints)` with rationale, alternatives, and cold-start flags.

### Adaptive Brain & Policies (Agent B storage/UI + Agent A routing + Agent C adapters)
- `GET /v2/policies?project_id=...` (`read`): Get project execution policy (manual vs auto mode, expiry, cost/token ceilings, kill switch, allowed auth modes, concurrency cap default `2`).
- `PUT /v2/policies` (`admin`): Update project policy with audit trail; auto mode resets to `REVIEW_REQUIRED` on restart unless explicitly persisted.
- `GET /v2/brain/config?project_id=...` (`read`): Get configured preference vs effective verified brain per role (`tier0_rules`, `tier1_light`, `tier2_primary`, `tier3_advanced`, `secondary_fallback`), availability status, fallback order, daily planner budget.
- `PUT /v2/brain/config` (`admin`): Update role configuration with audit log.

### Plans, DAG & Atomic Approval (Agent C planner → Agent B storage/UI)
- `POST /v2/plans` (`planner` / `admin`): Create plan + revision `1` with DAG `tasks[]`, `dependencies[]`, `task_features[]`, `forecast_snapshot`, `revision_hash`, status `REVIEW_REQUIRED`.
- `GET /v2/plans/{plan_id}?project_id=...` (`read`): Plan detail, revisions, approval state, tasks, DAG, live progress, forecast vs actual.
- `PUT /v2/plans/{plan_id}/revisions` (`admin` / `planner`): Edit assignments/models/budget creating new revision (`revision_number + 1`); invalidates any prior approval (`409` on stale dispatch).
- `POST /v2/plans/{plan_id}/approve` (`admin`): Atomically approve exact `(plan_id, revision_id, revision_hash)` with `actor`, `mode` (`manual | auto_scoped`), optional `auto_expires_at`, and atomic budget reservation.
- `POST /v2/plans/{plan_id}/reject` / `pause` / `resume` / `cancel` (`admin`): Lifecycle controls with audit record.

### Narrow Authenticated Host Runner Jobs (Agent C host runner ↔ Agent B bridge)
- `POST /v2/jobs/claim` (`runner`): Atomically claim next ready task whose dependencies succeeded AND whose exact approved `plan_revision_id` + `revision_hash` match; returns `lease_id`, `lease_expires_at`, bounded `context_pack`, verified `model_id`, `effort`, `auth_mode`, `worktree_id`, `budgets`. Never accepts arbitrary shell commands.
- `POST /v2/jobs/{job_id}/heartbeat` (`runner`): Extend active lease and report live progress / peak context.
- `POST /v2/jobs/{job_id}/events` (`runner`): Append-only idempotent execution/usage/checkpoint events.
- `POST /v2/jobs/{job_id}/checkpoint` (`runner`): Persist bounded redacted checkpoint handoff and create child continuation session when approaching context/token/turn budget.
- `POST /v2/jobs/{job_id}/complete` (`runner`): Finalize attempt outcome (`succeeded | failed | cancelled | budget_exceeded`), release unused budget reservation, trigger forecast actual reconciliation.
- `POST /v2/jobs/{job_id}/cancel` (`admin` / `runner`): Cancel running/queued attempt safely.

### Durable Session Analytics, Forecasting & Quotas (Agent A forecast + Agent B ledger/UI + Agent C telemetry)
- `GET /v2/sessions` (`read`): Filter by `project_id`, `worktree_id`, `task_type`, `complexity`, `model_id`, `effort`, `outcome`, `from_date`, `to_date`.
- `POST /v2/sessions` (`runner` / `planner` / `write`): Create/update durable session ledger entry with idempotency.
- `GET /v2/sessions/{session_id}?project_id=...` (`read`): Session detail, parent/checkpoint chain, measured counters, features, outcome.
- `DELETE /v2/sessions/{session_id}?project_id=...` (`admin`): Retention/privacy deletion including derived forecast samples and aggregate rebuild.
- `GET /v2/analytics/cohorts?project_id=...` (`read`): Cohort token/cost distributions (`sample_count`, `p50`, `p90`, `cost_per_accepted_task`, `retry_rate`).
- `POST /v2/forecasts` (`read` / `planner`): Deterministic read-only forecast computation from pre-execution features (returns task & plan P50/P90 ranges, confidence tier, cohort provenance, alternative models, per-provider quota impact or `null`).
- `GET /v2/forecasts/{forecast_id}/actual?project_id=...` (`read`): Compare immutable pre-approval forecast against measured final actuals.
- `GET /v2/analytics/calibration?project_id=...` (`read`): Rolling holdout MAE/WAPE, empirical P90 coverage, drift indicator.
- `GET /v2/usage` / `POST /v2/usage` (`read` / `runner` / `planner`): Deduplicated append-only usage events (`source_event_id`, `cumulative_or_delta`, `sequence_no`).
- `GET /v2/quotas` / `POST /v2/quotas` (`read` / `runner` / `admin`): Provider/account quota snapshots with `unit`, `numerator`, `denominator`, `window`, `reset_at`, `source`, `observed_at`, `confounded` flag.
