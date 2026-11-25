# V3 Dynamic MCP Platform — đề xuất thiết kế
Status: DESIGN / QUEUED, not implementation. Base reference 3bea6ccfadfdd93cf688509f424bffcf5e5d7e03 on lead/v2-orchestrator. V2 PRs #5/#6/#7 still draft at design time. V1 Lead reports 35 pass, 1 skip; Docker runtime gate remains open. Rebase/cherry-pick this documentation only onto validated V2 before implementation. No V2/main modifications or agent activation by this design PR.
## Requirements
Owner can register, install, configure, enable/disable, update, rollback and remove compatible MCPs and profiles through Vietnamese dashboard without editing central source, rebuilding central image or redeploying hub. Existing client connections use stable discovery/execution facade; some clients require reconnect. New unsupported runtime/protocol or core security change may require release. No local LLM. Windows 11, 16 GiB, Docker Desktop optional for host development; actual Docker release gate mandatory.
## Reading order
- ARCHITECTURE.md: boundaries, runtime, resource policy
- REGISTRY-API.md and SCHEMA.sql: proposed contracts and storage
- plugin-manifest.schema.json and examples/: import schema and inert templates
- SECURITY-CODEMODE.md: threat model and execution
- TOOLKITS.md: catalog and workflows
- DASHBOARD.md: Vietnamese UI
- QA-ROADMAP.md: milestones and release gates
- AGENT-BOARD.md and tasks/: implementation ownership
- SOURCES.md: upstream candidates, verification requirements
## Invariants
Bridge is sole SQLite writer; reuse V2 project/worktree/task/plan revision/lease/usage identity. Global installation != project access != active process != tool invocation approval. Core local search and memory work without Hermes or plugins. Do not self-approve, auto-install unknown software, bypass OS permissions, extract credentials, grant Docker socket, or silently send data externally. No real banking data or paid provider calls in tests. All specs provisional until Lead freezes interfaces.
