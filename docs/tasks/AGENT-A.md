# Agent A — agent/v1-codebase

Read README, AGENTS, ARCHITECTURE, ROADMAP, INTERFACE-V1, UPSTREAM-V1, AGENT-BOARD. Fetch origin/lead/v1-integration and create separate worktree/branch from its HEAD; never overwrite another agent or main.

## Ownership
services/codebase/**, tests/codebase/**, docs/evidence/agent-a.md

## Implementation
AIH-02: verify release artifact checksum and CLI/MCP schema; build non-root adapter on verified artifact; use persistent index at /data; explicit registry allowlist from bridge; isolate duplicate repo/branch/worktree names; host-generated read-only mounts, Windows linked .git mapping without drive-wide mounts; expose scoped index jobs/status/search and MCP search_code; deduplicate indexing jobs and incrementally refresh on HEAD/dirty changes; exclude secrets and dependencies, disable source export and model/network calls.

## Acceptance
Synthetic Java and TypeScript repositories with same symbol but different bodies, two worktrees same branch/detached HEAD, dirty edits, restart cache, no full reindex on session start, path traversal/symlink rejection, bounded results, actual tools/list and search evidence.

## Dependencies
Shared contract fixed by Lead; use synthetic mocks until dependencies land; request interface changes via PR comment. Lead owns compose and shared scripts/config; propose needed changes, do not edit them. Replace your scaffold with working service and real health checks, keep port/data contract. Pin dependencies and report version/digest. Do not commit keys, real data or local paths.

## Handoff
Commit and push branch agent/v1-codebase; open Draft PR targeting lead/v1-integration. Include SHA, changed paths, exact test commands/results, runtime evidence, integration instructions, open blockers and security checklist. No merge main. Do not claim tests not executed.
