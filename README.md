# AI Memory Hub

Local-first development memory for Claude Code, Codebase Memory MCP, Hermes Agent, Codex and Gemini.

## Status

Architecture/bootstrap only. Services and Docker images are not yet implemented or validated. See [architecture](docs/ARCHITECTURE.md) and [roadmap](docs/ROADMAP.md).

## Goals

- Find relevant symbols and source locations without broad repository reads.
- Retain verified project decisions, bug fixes, requirements and agent handoffs across sessions.
- Run supporting services in Docker Compose on Windows/WSL2; coding agents remain on the host.
- No LLM call on the memory retrieval path; optional Hermes summarization is bounded and opt-in.
- Isolate repositories and worktrees; retain evidence, commit and verification state.

Never commit secrets, source indexes, transcripts, local project paths or project memory. Keep the repository private if it will contain private information.
