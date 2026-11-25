# Agent C — agent/v1-hermes-integrations

Read README, AGENTS, ARCHITECTURE, ROADMAP, INTERFACE-V1, UPSTREAM-V1, AGENT-BOARD. Fetch origin/lead/v1-integration and create separate worktree/branch from its HEAD; never overwrite another agent or main.

## Ownership
services/hermes/**, integrations/**, scripts/install-claude.ps1, tests/hermes/**, tests/integrations/**, docs/evidence/agent-c.md

## Implementation
AIH-03/05/11: verify official image tag/digest and invocation; optional Hermes worker only approved redacted events with DeepSeek, bounded retry/cost and no source/tools; no key required core. Claude installer preserves existing MCP and hooks, dry-run, backup, atomic merge, uninstall own entries only, conflict detection; SessionStart resolve Git scope and retrieve bounded context; Stop/SessionEnd capture minimal metadata, never transcript by default, fail open with timeout. Instruct Claude search first then read actual relevant code; Codex/Gemini config examples verified against installed versions.

## Acceptance
Hermes disabled/invalid key no impact, mocked provider no outbound by default, approval gate, no secrets/logs, idempotent install twice with preexisting config, Windows paths with spaces, hook timeout/nonblocking, actual Claude MCP call where available; report blocked tests honestly.

## Dependencies
Shared contract fixed by Lead; use synthetic mocks until dependencies land; request interface changes via PR comment. Lead owns compose and shared scripts/config; propose needed changes, do not edit them. Replace your scaffold with working service and real health checks, keep port/data contract. Pin dependencies and report version/digest. Do not commit keys, real data or local paths.

## Handoff
Commit and push branch agent/v1-hermes-integrations; open Draft PR targeting lead/v1-integration. Include SHA, changed paths, exact test commands/results, runtime evidence, integration instructions, open blockers and security checklist. No merge main. Do not claim tests not executed.
