# Delivery roadmap

All items below are planned, not completed.

| ID | Scope | Priority | Acceptance |
|---|---|---|---|
| AIH-01 | Compose bootstrap and pinned dependencies | P0 | compose config/build, health and restart verified |
| AIH-02 | Codebase Memory adapter | P0 | index/search actual test repo; worktree path mapping |
| AIH-03 | Hermes optional worker | P0 | disabled by default; approved events summarized via bridge |
| AIH-04 | Memory Bridge / SQLite / FTS5 | P0 | migrations, search limits, isolation and verified records |
| AIH-05 | Claude MCP / host hooks | P0 | real Claude connection; bounded nonblocking hooks |
| AIH-06 | Dashboard | P1 | health, projects, memory review and reindex |
| AIH-07 | Experience capture | P1 | idempotent events, dedupe, provenance and review |
| AIH-08 | Codex / Gemini integration | P1 | shared read and handoff tested with each client |
| AIH-09 | Metrics / benchmark | P1 | reproducible before/after, no fabricated token savings |
| AIH-10 | Backup and restore | P0 | SQLite consistent backup, restoration and volume persistence |
| AIH-11 | Windows installation | P0 | idempotent scripts; preserve existing configurations |
| AIH-12 | Multi-agent orchestration | P2 | scoped tasks, concurrency and human approval gates |

## Implementation sequencing

1. Agent A: AIH-01/02, verify upstream release and transport, code search adapter.
2. Agent B: AIH-04/10, implement DB and MCP contracts, unit tests.
3. Agent C: AIH-03/07, optional Hermes integration after bridge contract agreed.
4. Agent D: AIH-05/11, Windows scripts and integration smoke tests after service contracts agreed.
5. Lead: freeze interfaces, review PRs, integrate, run build and Docker smoke tests on Windows, then proceed to P1. Do not merge untested components as production-ready.

Each agent uses a separate branch and PR, includes exact test commands/results and notes any unverified upstream assumptions. Never expose API keys or private project code in logs, fixtures or commits.

## V3 — Dynamic MCP & Conversation History (planned)

See [V3 implementation plan](v3/IMPLEMENTATION-PLAN.md), [V2.2.3 Stability & Security](v22/PATCH-3.md), and [V2.2.4 Execution Map & Telemetry](v22/PATCH-4.md). **V2.2.4** là bước hoàn thiện observability (Unified Execution Workspace, execution sequence, multi-level summary, Token Telemetry Collector & Inspector) trước khi triển khai **M1 — Coding Execution Engine**. M0 requires actual Hub telemetry before feature development. Prefer Dashboard plan/approval/dispatch when the runner supports the intended operation; otherwise register the external IDE session and trace honestly. Do not dispatch write work through a read-only runner or bypass policy. No main merge without approval.

## V3.1 — Business Workspace & Dynamic MCP Platform

See [V3.1 Implementation Plan](v31/IMPLEMENTATION-PLAN.md), [V3.1 Status](v31/STATUS.md), [MCP Catalog](v31/MCP-CATALOG.md), [Business Workspace](v31/BUSINESS-WORKSPACE.md), [MCP Gateway](v31/MCP-GATEWAY.md), [Security](v31/SECURITY.md), [Windows Setup](v31/WINDOWS-SETUP.md), and [Acceptance Test](v31/ACCEPTANCE-TEST.md). Branch: `feature/v31-business-mcp-platform` (Schema v8 additive, no main merge without approval).
