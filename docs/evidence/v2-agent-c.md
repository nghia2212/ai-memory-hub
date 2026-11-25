# V2 Agent C Evidence — Planner, Adaptive Brain & Narrow Host Runner

- **Branch:** `agent/v2-orchestration`
- **Base:** `lead/v2-orchestrator` (`3bea6ccfadfdd93cf688509f424bffcf5e5d7e03`)
- **Timestamp:** `2026-11-25T10:27:02+07:00`

## 1. Contract Compliance (`services/orchestrator/contracts.py`)
- Imports and conforms to `BrainAdapter`, `BrainRequest`, `BrainResponse`, `BrainPolicyConfig`, `BrainRoleConfig`, `HostCLICapability`, `HostCapabilityReport`, `ModelRecord`, `PlanCreateRequest`, `PlanTaskSpec`, `PreExecutionFeatures`, `QuotaSnapshot`, `TokenUsageCounters`.
- Missing telemetry counters are strictly `None` (`null`), never `0`.
- Unsupported subscription quotas return `[]` / `'Không có dữ liệu'`, never `0%` or inferred from token counts.
- `OptInDeepSeekBrainAdapter` is disabled by default (`allow_paid_api_fallback=False`) and never called without explicit project approval.

## 2. Host CLI Discovery Verification (Read-Only, Zero Paid Calls)
Executed `discover_host_cli_capabilities()` on this Windows host:
- `agy`: `installed=True`, `version='1.1.27'`, `10` verified models (`gemini-3.8-flash-high/medium/low`, `claude-opus-5-5-low/medium/high`, `claude-sonnet-5-5-low/medium/high`, `gpt-oss-120b-medium`).
- `claude`: `installed=True`, `version='2.1.269 (Claude Code)'`.
- `codex`: `installed=True`, `version='codex-cli 0.166.0'`.
- `gemini`: `installed=False`.
- `resolve_role_availability('Gemini 4 Neon Medium', 'gemini-4-neon-medium', report)` → `availability_status='unavailable'`, `effective_model_id=None`, `fallback_order=['claude-sonnet-5-5-medium', 'gemini-3.8-flash-high']`.

## 3. Automated Test Results (7/7 Passed, Fake/Mock Adapters Only)
Command:
```powershell
.venv\Scripts\python.exe -m unittest discover -s tests\orchestrator\runner -v
```
Result: **7/7 PASS (`0.043s`)**:
- `test_neon_unavailable_when_missing_from_agy_models`: confirms missing `Gemini 4 Neon` is marked `unavailable` while `claude-sonnet-5-5-medium` is `verified`.
- `test_deepseek_blocked_when_paid_fallback_disabled`: confirms zero outbound/paid calls when `allow_paid_api_fallback=False`.
- `test_dag_validation_and_revision_hash`: confirms Tier 0 rule plan (`cost_usd=0.0`), cycle detection (`InvalidDAGError`), and revision hash tamper sensitivity.
- `test_planner_budget_blocks_llm_when_exhausted`: confirms `daily_planner_budget_usd` enforcement.
- `test_command_builder_safety_and_no_bypass`: rejects shell injection, `..` traversal, and verifies no permission-bypass flags.
- `test_telemetry_missing_counters_are_none_and_redacted`: confirms missing counters stay `None`, secrets/paths are redacted, and `discover_supported_quotas` returns `[]`.
- `test_host_runner_rejects_unapproved_and_handles_budget_and_timeout`: confirms unapproved revision rejection, `codex` JSONL telemetry extraction, deduplicated `source_event_id` usage posting, and timeout checkpointing.

Dry-run script check:
```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File scripts\runner\run-host-runner.ps1 -DryRun
```
Result: **Exit code 0**.

## 4. Blockers & Open Items
1. **Docker Desktop daemon:** Not running on host (`lead/v2-orchestrator` open gate).
2. **Bridge `/v2/*` endpoints:** Runner tests use `FakeRunnerBridge` until Agent B (`agent/v2-observability`) lands `/v2/jobs/*`, `/v2/sessions`, `/v2/usage`.
3. **Real CLI execution & paid DeepSeek calls:** Not executed without owner permission.
