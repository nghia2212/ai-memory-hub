# AI Memory Hub V1 — Agent board

Latest user assignment supersedes four-agent sequencing in original roadmap. All implementation pending; scaffold only. Base lead/v1-integration.

| Owner | Branch | Scope | Status | Dependencies |
|---|---|---|---|---|
| A | agent/v1-codebase | services/codebase/**, tests/codebase/**, docs/evidence/agent-a.md | READY | INTERFACE-V1; mocks until integration |
| B | agent/v1-memory-bridge | services/memory-bridge/**, services/dashboard/**, tests/bridge/**, tests/dashboard/**, docs/evidence/agent-b.md | READY | INTERFACE-V1; mocks until integration |
| C | agent/v1-hermes-integrations | services/hermes/**, integrations/**, scripts/install-claude.ps1, tests/hermes/**, tests/integrations/**, docs/evidence/agent-c.md | READY | INTERFACE-V1; mocks until integration |
| Lead | lead/v1-integration | compose, config, shared docs, scripts/preflight.ps1, tests/integration | Scaffold | A/B/C PRs |

Read docs/tasks/AGENT-A.md, AGENT-B.md, AGENT-C.md for acceptance and file boundaries. No agent edits shared files without Lead approval. Work in separate git worktrees. Draft PR to integration, report SHA/tests/blockers, Lead reviews and updates board. Never merge main before all release gates pass. No fabricated status.
