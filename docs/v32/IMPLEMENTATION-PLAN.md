# V3.2 — Centralized Skill Management
Date: 2026-11-25 | Status: PLANNED, user approved design direction only.
Base: feature/v31-business-mcp-platform. Design branch: design/v32-skill-management.
This release name denotes the Skill Management extension to Business MCP V3.1, not the conversation-storage M4/V3.2 label in docs/v3/IMPLEMENTATION-PLAN.md. Preserve that roadmap; track this extension as SK32. Do not merge main without approval.

## P0 — baseline and contracts
Fetch current V3.1 HEAD and record SHA; inspect real migrations, business/project/agent IDs, API, policies, execution map and runner capabilities. Reconcile unfinished V3.1 work first; no assumption that branch existence means acceptance. Follow AGENTS.md and V3 M0 telemetry gate: actual scoped project/worktree/session/root trace, verified memory and code queries, record real transport, no fake traces/token counts. Register genuine external IDE session if Hub runner cannot write. Stop implementation on missing trace or unsafe migration; report blockers. Freeze interfaces and owner assignments before parallel coding.

## Architecture
Registry is provider-neutral and uses SKILL.md plus optional references/assets/scripts. Local-first Bridge remains sole DB writer; use additive migrations after inspecting actual schema. Proposed logical entities (adapt names to existing contracts):
- skill: stable UUID, namespace, slug, title, description, owner, lifecycle, provenance, created/updated timestamps.
- skill_version: immutable version, content hash, manifest, bounded markdown, approved attachments, state draft/review/published/retired, author, approval, timestamp.
- skill_binding: scope type global/business/project/agent-role, actual scope ID, skill ID, enabled/disabled/inherit, version constraint, priority, required flag under authorized policy.
- skill_run: task/attempt/session/trace IDs if real, resolved version/hash, reason, adapter, status selected/delivered/loaded/executed/unknown, evidence, byte/token metrics nullable.
- skill_candidate: verified memory references, proposed patch, validation evidence, reviewer and disposition.
Keep Skill definitions separate from memory facts and MCP credentials. Skills cannot grant tool permissions or bypass approval. A project may map to multiple businesses only if existing policy permits; ambiguous bindings require explicit resolution.

## P1 — registry, CRUD and versions (Agent A)
Create/list/read/edit draft/archive/import/export; publish requires authorized approval; immutable published version, optimistic concurrency, audit, pin and rollback by changing active version (never mutate history). Delete unreferenced drafts only, archive referenced versions; preserve historical task evidence. Validate manifest (name, description, tags, applicable capabilities, required MCP/tool IDs, permissions, version, optional references). Content and attachment quotas; safe paths, no symlinks, zip traversal or executable auto-run. Import preview/diff/hash/source/license and explicit approval. Markdown is untrusted data; no raw HTML execution. Backups and restore. Test migration and CRUD/auth/limits/concurrency on isolated data.

## P2 — scoped bindings and deterministic resolution (Agent A + Lead)
Global -> business -> project -> agent-role scope. More specific explicit binding wins only within authorized policy, inherit uses nearest applicable parent; disabled overrides inherited optional skill; mandatory policy cannot be disabled by less privileged scope. Hard deny and tenant/project isolation always win. Multiple business conflicts reject with explanation unless explicit project decision. Resolve task by project/business, role, tags/capabilities and explicit requested skills; verify dependencies and actual authorized MCP availability; missing required dependency blocks task, optional dependency reports degraded. Deduplicate stable IDs, resolve immutable versions, detect conflicts/cycles, stable order, bounded budget; required skills that cannot fit block dispatch rather than silently truncate. Save resolution snapshot per attempt; updates apply to new attempts only unless approved replan. No external LLM required for lookup/resolution. Unit tests for all precedence, isolation, disabled, pin, conflict, missing dependency, reproducibility.

## P3 — Dashboard (Agent B)
Vietnamese UI: Skill Library, detail editor/preview/diff/version/approval/history, filter by scope/status/tag, CRUD and import/export, assign/unassign and inherit/enable/disable, bulk preview, project and business configuration, effective-skill preview with reason/version/source/required/missing MCP, audit and rollback. Show what changes when deleting a business/project (remove bindings or archive, never delete shared skill silently). Reuse existing auth/CSRF; safe rendered markdown, accessible responsive UI; no secrets in export. Browser tests for real operations and permissions, not only mock screens.

## P4 — task orchestration (Lead + Agent A)
Integrate resolver before plan approval/dispatch, show selected versions and MCP dependencies in task plan, approval and attempt. Progressive disclosure: manifest first, load only relevant SKILL.md and bounded references; no full registry/context dump. Explicit required vs optional selection, user override within policy. Record actual delivery/load separately; fail closed for required skill not deliverable. Existing tasks without skills continue unchanged. Expose proposed scoped REST and MCP read methods list_skills, resolve_skills, get_skill_version; validate actual route naming and transport before implementation. Admin writes authenticated separately, never expose arbitrary script execution.

## P5 — provider adapters (Agent C)
Inspect installed versions and official docs first. Claude Code/Codex/Gemini CLI/Antigravity native skill support only where verified; otherwise controlled context adapter with clear capability label. DeepSeek API and Qwen local use bounded context where configured; no assumption of native skills. Adapter manifest declares discovery/install/load/evidence support. Preview sync, managed namespace, atomic update, backup existing config, preserve unrelated user skills, version hash, scoped files, remove only Hub-owned assets. No auto-install, shell commands, network download, paid calls or model switching without permission. Unsupported clients remain unsupported with documented manual fallback. Real client smoke tests when available; report unavailable quota/client separately.

## P6 — Execution Map and telemetry (Agent B + C)
Extend existing timeline, not a parallel dashboard: Task -> Agent -> Skill resolution -> delivery/load -> MCP calls -> memory -> test/handoff. Show version/hash, scope, selection reason, dependencies, timestamps and coverage. Distinguish selected, delivered, observed loaded, executed and unknown; never infer execution merely from prompt injection. Link real trace IDs only. Report bytes and provider-measured tokens when available, otherwise null or separately labeled estimate. Redact content and credentials. Tests for partial/failed/unlinked traces.


## P6A — Task-centric Execution Map (V3.2.1, mandatory within SK32; Agent B + Lead)
User requirement approved 2026-11-25. V3.1 is still under implementation by Gemini; do not modify its branch or dispatch competing work. Begin only after V3.1 delivery and P0 baseline verification. Rebase the design/integration branch onto the accepted V3.1 HEAD and preserve existing P0–P9 scope. This is part of V3.2 acceptance, not a deferred optional release.

### UX and navigation
- Cascading scope selectors: Project -> Plan (the original user request) -> Task (individual work item) -> Attempt; optional worktree, Agent, Session, status, time and root trace filters. Use actual IDs, searchable human-readable titles, status and timestamps; include all/clear options, reset incompatible dependent selections. Plan view includes all its tasks; task view shows only that task by default, with explicit include-descendants toggle where a real hierarchy exists. Distinguish DAG dependencies from parent/child relationships. Do not invent hierarchy.
- Add "Bản đồ thực thi" links from plan/task detail, task list and attempt/session detail, passing scope IDs through a validated deep link. Preserve selection on refresh and view switch; breadcrumbs, back to plan/project, no stale response after switching. Default existing project-wide view remains available.
- Apply identical server-side scope to Graph, Swimlane, Timeline, Table, summaries, detail panel, auto-refresh and export (JSON/CSV). Show empty/loading/error states; never show another task's data while fetching. Show related out-of-scope dependency/parent only as explicitly marked navigation link, not as included step or metric.
- Task summary: observed steps by status, progress only with reliable planned denominator, elapsed wall-clock vs summed durations, Agent/Memory/MCP/Skill/QA/Git activity, measured token/cache and cost coverage, retry/attempt breakdown; avoid double counting and never label estimated/unknown as actual. Preserve global execution_sequence; optional local ordinal clearly labeled. No extra LLM calls for filtering or summaries.

### Backend and trace integrity
- Audit current contracts in docs/v22/EXECUTION-MAP.md and actual implementation: GET /v2/traces already documents project_id, plan_id, task_id, session_id, root_trace_id; verify dashboard proxy, summaries/export, pagination and indexes. Add attempt_id or descendant query only if absent, using additive compatible contracts and indexed scoped queries. Filter before pagination and aggregation; authorize all IDs against project/worktree and reject inconsistent scope combinations. Bound results, retain auth/CSRF and audit.
- At dispatch, propagate actual project/worktree/plan/task/attempt/session/root identifiers through runner, agent adapters, MCP, memory, skill and QA/Git events where supported. Explicit parent linkage only; immutable trace context; retries have distinct attempt IDs. Do not infer membership from time, model, branch or text. External IDE sessions may attach to a task only through explicit authorized registration/linkage with evidence; do not overwrite historical trace context. Show legacy or unsupported traces as "Chưa liên kết nhiệm vụ" in project scope, excluded from scoped task totals; indicate partial coverage. No heuristic backfill, fake telemetry or synthesized steps.
- Agent B owns UX and browser tests; Lead owns API and scope contracts; Agent A supports indexes/authorization; Agent C verifies real adapter correlation and Skill P6 telemetry. Report actual trace IDs, commits, test evidence and unsupported clients. Integrate before P9, no separate competing execution dashboard.

### Mandatory acceptance
1. Two plans in one project, multiple concurrent tasks and retries: select each plan/task/attempt and prove no cross-task leakage in all four views, details, totals, auto-refresh and export.
2. Direct links from workbench open the correct scope; refresh, switch tasks rapidly, clear filters and return to project view without stale data or lost selection.
3. Parent task includes descendants only when selected and explicit relationship exists; DAG dependency is not misclassified. Out-of-scope links and unlinked legacy traces never inflate task totals.
4. Reject foreign project/task/attempt IDs; verify auth, pagination and accurate summary across pages. Missing token/cost remains unknown, not zero; no double count across attempts/sessions.
5. Actual permitted task run demonstrates correlation of observed Agent/MCP/Memory/Skill/QA/Git events as supported; label missing linkage honestly. Use isolated synthetic fixtures for unit/browser regression; real Windows smoke and evidence required for release.

## P7 — experience to Skill (Agent D)
From verified memory/evidence propose candidate skill or patch; dedupe and conflict checks, provenance, project scope, human review, diff, publish via P1; never auto-promote or leak across projects. Optional LLM only opt-in and redacted, default deterministic/manual. Test rejection, approval, rollback, poisoned memory and isolation.

## P8 — starter library and benchmark (Agent D)
Generic reviewed starter skills: planning, git-workflow, code-review, root-cause-analysis, regression-testing, security-review, documentation, memory-management, agent-handover, token-optimization. Game: Three.js, Phaser, Blender, GLB optimization, rigging, mobile performance, children UX, visual QA, audio, deployment. TA/developer: architecture, system design, Java/Spring, API design, DB optimization, Docker, incident response, integration and technical documentation. Project-specific rules entered locally with explicit scope; no private banking/project information in public Git. Synthetic example project fixtures only. Default enable only reviewed bindings; no indiscriminate injection. Benchmark paired identical tasks with/without skills, same model/settings, capture input/output/cache tokens where available, tool calls, time, cost basis and QA outcome; no fabricated savings or paid test by default.

## P9 — integration, Windows delivery and release (Lead + all)
Prerequisites P0; A handles P1/P2; B dashboard after contracts, C adapters after resolver contract, D candidates/library after version contract; Lead owns P4 integration. P6 and mandatory P6A follow actual execution events; P6A must pass before P9. Use independent branches agent/sk32-registry, agent/sk32-dashboard, agent/sk32-adapters, agent/sk32-learning from agreed integration SHA; draft PRs toward lead/sk32-integration, not main. Rebase against approved V3.1 baseline before release. Each milestone commit, tests, real trace/handoff, status and blockers. Run regression V2/V3/V3.1, auth/isolation, malicious import/prompt injection, migration from copy, backup/restore, offline retrieval, Docker build/health/restart, browser and Windows smoke. Provide idempotent scripts/Test-V32.ps1 and upgrade/rollback instructions with timeouts and visible progress; no live volume deletion, no restart without consent. Preserve all existing MCP and memory behavior. Release only after gates pass or explicit documented waivers; no production/main merge without user approval.

## Acceptance matrix
- Create one global skill, bind to two projects without duplicate content; disable in one without affecting other.
- Project/role override resolves deterministically; mandatory policies and tool permissions cannot be weakened.
- Edit/publish v2, pin v1, rollback, historical task still shows original hash.
- Add/remove business/project bindings without deleting shared skills.
- Actual task selects relevant subset, checks MCP permissions and budget; required missing skill blocks dispatch.
- Adapter real delivery evidence shown; unsupported/unknown honestly displayed.
- Import traversal, malicious scripts/HTML, secret leakage and cross-project reads rejected; no automatic execution.
- Verified memory proposes draft, rejection leaves active version unchanged, approval auditable.
- Offline lookup works without provider; existing MCP workflows regressions pass; migration/restore preserves data.
- Dashboard real workflow and Windows smoke evidence, exact commands/results and limitations.

## Reporting
Maintain docs/v32/STATUS.md during implementation with P0-P9 state, owner, commit, test results, real IDs (redacted as needed), risks and next task. No synthetic production evidence. Public repository: no credentials, actual local paths, transcripts, customer/bank data or private memory. This commit is planning only; implementation and runtime validation remain pending.
