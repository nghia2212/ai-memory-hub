# AI Memory Hub — Hermes Edition (migration)
Status: H0-H2 pilot wiring only; live gates pending. Base: 06ec853f6ca379a1ea89eaf95332ac3875a5d995. No production cutover.

## Architecture decision
Hermes v0.23.4 is the upstream runtime baseline, not the existing Python summarization worker. Native Windows first. Hermes Kanban is the single task lifecycle owner for migrated projects. Never dual-dispatch or write upstream SQLite directly. Use upstream CLI/tools/plugin API for lifecycle. Use existing Memory Bridge/Codebase MCP for structured project memory; no LLM on lookup. Keep legacy state and startup untouched until tested rollback. Prefer built-in Dashboard, profiles, Skills, sessions, memory, tools, MCP, cron and upstream kanban-graph/monitor plugins (audit permissions and compatibility before installing). Do not implement a second scheduler, DAG, memory UI or generic MCP registry.
 
## Gates
| Phase | Work | Exit gate |
| --- | --- | --- |
| H0 | inventory legacy sessions, drain/stop, online backup, verify restore in separate instance | no active claims, checksum and restore evidence |
| H1 | official native Windows release + isolated pilot profile; launcher in scripts/hermes | exact version, model auth, no shared credentials by default |
| H2 | native Kanban/Dashboard, memory, skill and MCP smoke tests | scratch board survives gateway and Windows restart, dependencies enforced, one claim, no duplicate execution |
| H3 | native profile lanes first; reuse existing safe CLI command construction for external worker adapters only where needed | Claude/Codex subscription auth + cancellation, exit mapping, review, heartbeat, quota failure and resume; then Gemini/agy |
| H4 | register existing Bridge/Codebase MCP with project scope and read-only tokens initially | correct project/worktree results, no leakage, no LLM on retrieval |
| H5 | reuse Hermes provider usage/fallback where verified; add Hub router/forecast only for gaps | unknown quota remains unknown; no paid fallback without approval; compare measured tokens |
| H6 | business-to-profile/skill/MCP policy mapping as a thin extension | approved per-business tools, default deny, no duplicate generic registry |
| H7 | evaluate upstream kanban-graph/monitor before custom analytics | measured telemetry with provenance, no credential disclosure |
| H8 | export verified memories and migrate approved non-running tasks with idempotency; preserve old DB read-only | dry run, mapping, reconcile counts, restore test, no active task replay |
| H9 | pilot on game feature branch, compare success, recovery, cost, latency; remove redundant legacy modules only after signoff | all prior gates pass; rollback exercised; no merge/deploy before review |
 
## Critical external-worker boundary
Upstream documents external CLI lanes as integration work, not turnkey. Use a Hermes profile lane + upstream coding skills initially in a disposable worktree; the parent owns lifecycle and requests review. Never treat CLI exit zero as acceptance. No automatic main push, merge, auth export or arbitrary shell. No claim of enforcing legacy workspace/permission policies in the native pilot. Production H3 must enforce approved workspace and permissions before tool invocation, keep secrets local, map each run to Hermes lifecycle and reject concurrent legacy execution. No direct DB writes.
 
## References
- https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4
- https://hermes-agent.nousresearch.com/docs/user-guide/windows-native
- https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban
- https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban-worker-lanes
- https://hermes-agent.nousresearch.com/docs/reference/cli-commands
- https://hermes-agent.nousresearch.com/docs/plugins/kanban-graph
Current online docs may be ahead of the pinned release; verify CLI flags and plugin compatibility locally. Do not silently upgrade.
