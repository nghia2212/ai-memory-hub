# Agent B — agent/v1-memory-bridge

Read README, AGENTS, ARCHITECTURE, ROADMAP, INTERFACE-V1, UPSTREAM-V1, AGENT-BOARD. Fetch origin/lead/v1-integration and create separate worktree/branch from its HEAD; never overwrite another agent or main.

## Ownership
services/memory-bridge/**, services/dashboard/**, tests/bridge/**, tests/dashboard/**, docs/evidence/agent-b.md

## Implementation
AIH-04/10/06/07: implement registry, FastAPI/FastMCP with tested lifespan, sole SQLite writer and migrations, FTS5 Vietnamese query, verification workflow, candidate writes, idempotency, scoped tokens, worker approved queue, backup/restore online with rollback; Vietnamese dashboard overview, status, registry/index status, code search proxy, memory review, agent history, errors/experience, Hermes config status, backup/restore and measured metrics. No fake status or direct Docker access; pending host setup for mounts.

## Acceptance
Unit and integration tests cross-project/worktree isolation, SQL/FTS injection, auth/origin/CSRF, duplicate event, candidate visibility, Unicode, strict serialized budget, corruption/restore rollback, restart persistence, MCP handshake, UI smoke; no LLM dependency.

## Dependencies
Shared contract fixed by Lead; use synthetic mocks until dependencies land; request interface changes via PR comment. Lead owns compose and shared scripts/config; propose needed changes, do not edit them. Replace your scaffold with working service and real health checks, keep port/data contract. Pin dependencies and report version/digest. Do not commit keys, real data or local paths.

## Handoff
Commit and push branch agent/v1-memory-bridge; open Draft PR targeting lead/v1-integration. Include SHA, changed paths, exact test commands/results, runtime evidence, integration instructions, open blockers and security checklist. No merge main. Do not claim tests not executed.
