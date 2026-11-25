# V2.2 Execution Map

Development branch: lead/v21-development-workbench. Additive schema migration 4.
Do not merge main until Windows acceptance passes.

## Contract

GET /v2/traces?project_id=... (read bearer), optional worktree_id, plan_id,
task_id, session_id, root_trace_id, actor_id, model_id, status, from_time,
to_time, search, sort, limit (1–500), offset. Timestamps require timezone.
GET /v2/traces/{trace_id}?project_id=... returns a scoped step.
POST /v2/traces uses write/planner/runner scope, or restricted adapter/worker
credentials. A trace ID is idempotent for identical evidence. A running
span may finish once; parent and context are immutable.

Dashboard read proxies require a login cookie. Four views use the same data.
All visible text is rendered via DOM textContent. Evidence JSON is filtered
metadata, not raw prompts, secrets, source content or private reasoning.
Graph is a parent/child hierarchy, not a reconstruction of unobserved causality.
Independent roots are not linked heuristically. Explicit X-Trace-Parent is
optional; only an existing parent in the registered scope is used.

## Accounting

Session usage is a canonical projection of new V2 usage events, not an LLM
call. Measured cumulative counters replace previous values, deltas extend
them; missing values remain null. Metrics include session aggregates and
standalone measured provider calls; exclude linked call counters to avoid
double accounting. Totals are observed partial values, never invoices.
Cache and reasoning counters may overlap input/output, so do not sum them.
Counts represent observed steps, not all calls. Metrics are capped at 5000
filtered steps; pagination is separate. Wall span, union of completed
intervals and summed step duration are different concepts.

## Coverage limitations

CLI process wall time is not model inference latency. CLI requested models
are not proof of effective models; actual model is shown only if reported.
Hermes provider HTTP attempts include transport latency. Provider retries
have separate trace IDs. No automatic CLI MCP header injection is guaranteed;
unlinked events stay independent. Project-wide memory without a worktree is
not traced. Historical V2.1 events are not backfilled. QA evidence is displayed
when explicitly supplied; automatic fine-grained QA capture is not complete.
No dynamic MCP discovery/activation is introduced by this version.

## Windows acceptance

Use scripts/Test-V22.ps1 from the repository or its downloadable copy.
Default is read-only smoke test of running containers. -Upgrade checks a clean
checkout, backs up the existing Bridge database, fetches the development
branch and fast-forwards, adds missing env tokens, rebuilds core services.
No volume deletion, no forced reset, no paid model invocation. Stop active
runners before upgrade. -RunTests runs focused test suites in disposable
containers with temporary databases (requires Docker and package access).
Review the script output and manually log in to Dashboard to verify all four
views, filters, step details and no-data states. Real telemetry requires
actual permitted work. Do not confuse synthetic unit fixtures with real data.
