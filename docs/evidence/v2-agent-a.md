# Agent A V2 Evidence — Model Registry, Routing & Forecasting (`agent/v2-model-registry`)

- **Base Branch / Commit:** `lead/v2-orchestrator` (`3bea6ccfadfdd93cf688509f424bffcf5e5d7e03`)
- **Ownership:** `services/orchestrator/catalog/**`, `services/orchestrator/routing/**`, `services/orchestrator/forecast/**`, `tests/orchestrator/catalog/**`, `tests/orchestrator/forecast/**`, `docs/evidence/v2-agent-a.md`
- **Contracts Conformance:** All shared models (`ModelRecord`, `RoutingRecommendation`, `PreExecutionFeatures`, `QuotaSnapshot`, `TaskForecast`, `HostCapabilityReport`, `BrainRoleConfig`, `BrainPolicyConfig`) are imported directly from `services/orchestrator/contracts.py`.

## 1. Implemented Components

### A. Model Catalog & Host Discovery (`services/orchestrator/catalog/`)
- `sources.py`:
  - Allowlisted official-source adapters (`AnthropicCatalogAdapter`, `OpenAICatalogAdapter`, `GoogleGeminiCatalogAdapter`) restricted to `ALLOWED_SOURCE_HOSTS` (`platform.claude.com`, `docs.anthropic.com`, `developers.openai.com`, `platform.openai.com`, `ai.google.dev`) with `MAX_SOURCE_BYTES` cap, timeout, and untrusted JSON parsing.
  - `HostDiscoveryCatalogMapper` maps verified `HostCapabilityReport` selectors (verified against `docs/evidence/host-brain-discovery-v2.md`, e.g. `agy 1.1.27` with 10 verified selectors) without pricing subscription CLI usage as API usage.
- `registry.py`:
  - Versioned immutable `CatalogSnapshot` with SHA-256 checksum and TTL expiry.
  - New official models enter `candidate` quarantine until verified; removed/deprecated models emit `RetirementAlert` without silent substitution.
  - Upstream fetch failure falls back to the last known snapshot marked `is_stale=True` and raises `CatalogStaleError` on automatic routing.

### B. Deterministic Routing & Adaptive Brain (`services/orchestrator/routing/`)
- `evaluator.py`: `EvaluationMatrix` strictly separates `observed`, `synthetic_benchmark`, and `hypothesis` (cold-start) entries.
- `router.py`: `DeterministicRouter` (0 LLM) enforces hard filters (`status == "verified"`, `allowed_auth_modes`, `allow_paid_api_fallback=False` by default, `min_context_limit`, `required_capabilities`, exhausted `QuotaSnapshot` rejection, `max_cost_usd`) and ranks adequate models favoring lower cost. Unknown quotas remain `'Không có dữ liệu'`.
- `brain_resolver.py`: `resolve_brain_roles` verifies `tier0_rules`..`secondary_fallback` against `HostCapabilityReport` (`agy models`). When `Gemini 4 Neon Medium` / `Gemini 4 Hydrogen` / `Gemini 4 Carbon` are absent from `agy models`, they remain `availability_status="unavailable"` with verified fallback candidates (`claude-sonnet-5-5-medium`, `gemini-3.8-flash-high`, `claude-opus-5-5-medium`) surfaced for owner approval—never fabricated and never silently falling back to paid APIs.

### C. Session Analytics & Forecasting (`services/orchestrator/forecast/`)
- `rubric.py`: `classify_complexity` maps pre-execution features only (`task_type`, `file_count`, `expected_edits`, `dependency_count`, `expected_tests`, `uncertainty_level`) to `XS/S/M/L/XL`.
- `engine.py`: `ForecastEngine` implements project-isolated cohort selection, recency weighting, hierarchical fallback (`>=50` high, `>=20` medium, `5-19` low, `<5` cold_start), empirical P50/P90 from joint samples, resampled joint plan aggregation (avoiding naive independent P90 summation), alternative model forecasts, and strict quota-unit compatibility (`compute_quota_impact` returns `None` for incompatible units or `context_window`).
- `calibration.py`: `evaluate_calibration` computes rolling holdout MAE, zero-safe WAPE, empirical P90 coverage, and drift detection (`coverage < 0.75` or `wape > 0.35`).

## 2. Test Commands & Results (Fake/Mock Adapters, 0 Paid API Calls)

```powershell
$env:PYTHONPATH="D:\workspace1\ai-memory-hub-v2-agent-a"
D:\workspace1\ai-memory-hub-agent-a\.venv\Scripts\python.exe -m pytest D:\workspace1\ai-memory-hub-v2-agent-a\tests\orchestrator\catalog D:\workspace1\ai-memory-hub-v2-agent-a\tests\orchestrator\forecast -v
```

- **Result:** `8 passed in 0.29s`
  - `test_official_adapter_allowlist_and_quarantine_and_retirement`: PASSED
  - `test_stale_fallback_blocks_automatic_routing`: PASSED
  - `test_adaptive_brain_neon_unavailable_and_no_paid_fallback`: PASSED
  - `test_deterministic_router_prefers_cheapest_adequate_and_respects_quota`: PASSED
  - `test_complexity_rubric_pre_execution_only`: PASSED
  - `test_cold_start_and_unknown_quota_never_fabricated`: PASSED
  - `test_cohort_quantiles_hierarchical_fallback_and_plan_aggregation`: PASSED
  - `test_calibration_mae_wape_and_drift_detection`: PASSED

## 3. Open Blockers & Integration Notes

1. **Docker Desktop on host:** `com.docker.service` remains disabled on this Windows host (Lead-owned gate before `main`).
2. **Live provider catalog endpoints:** Official adapters were verified with synthetic JSON fixtures matching verified docs; live HTTP JSON feeds for `platform.claude.com`, `developers.openai.com`, and `ai.google.dev` URLs should be validated/adjusted if upstream publishes HTML-only pages rather than `.json` endpoints.
3. **Bridge integration:** Agent B can wire `/v2/catalog`, `/v2/catalog/refresh`, `/v2/evaluations`, `/v2/routing/recommend`, `/v2/forecasts`, and `/v2/analytics/calibration` directly to `CatalogService`, `DeterministicRouter`, `resolve_brain_roles`, and `ForecastEngine`.
