# Agent instructions

Read README.md and docs/ARCHITECTURE.md and docs/ROADMAP.md before implementation. This repo is a bootstrap, not a working service. Implement the P0 acceptance gates first.

- Verify upstream official documentation and exact release versions; never invent APIs or CLI flags.
- No external LLM request on search; Hermes is optional and disabled by default.
- Use one DB writer, parameterized SQL, migrations, bounded results, idempotency and provenance.
- Scope every request to a registered project/worktree. Reject unsafe paths. Do not log secrets.
- Keep source read-only inside indexing containers; no Docker socket.
- Default bind to loopback. Preserve existing host config during installation.
- Use separate feature branches, draft PRs and evidence-based tests; report limitations honestly.
- No personal source data, real API keys or local memory in Git. Use synthetic fixtures.
