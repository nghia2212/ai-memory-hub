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

## V3.1 — Business Workspace & Dynamic MCP Platform

- **Business Workspace Manager**: Manage dynamic business profiles (`Game Development`, `Technical Architect`, `Software Developer`, and custom profiles) directly on the Vietnamese Dashboard (`Quản lý nghiệp vụ`), with 9-tab detail management, soft-archive/restore, versioning, import/export, and per-business MCP/tool permissions.
- **Dynamic MCP Gateway**: Reuses `v3_mcp_*` registry tables with additive Schema v8 extensions for real STDIO JSON-RPC 2.0, Streamable HTTP, Native CLI, REST API, and Local Deterministic Tool adapters; real health checks, tool discovery with schema-change re-approval, 14-step default-deny invocation enforcement, and monitoring KPIs (`Kho MCP & Công cụ`).
- **Windows Setup & QA**: See [docs/v31/WINDOWS-SETUP.md](docs/v31/WINDOWS-SETUP.md), [docs/v31/STATUS.md](docs/v31/STATUS.md), and `scripts/Test-V31.ps1`.
