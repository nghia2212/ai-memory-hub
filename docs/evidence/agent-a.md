# Agent A Evidence — Codebase Memory Adapter (`agent/v1-codebase`)

## 1. Upstream Verification (`codebase-memory-mcp v0.12.1`)

- **Release:** `https://github.com/DeusData/codebase-memory-mcp/releases/tag/v0.12.1`
- **Verified SHA-256 (`checksums.txt`):**
  - `codebase-memory-mcp-linux-amd64-portable.tar.gz`: `8c3d0341064301e78ecc84cf7348d4e2c6562a0f57448243ea560aaa34de36c1` (verified locally via `Get-FileHash -Algorithm SHA256`)
  - `codebase-memory-mcp-linux-amd64.tar.gz`: `8453015145f072c1b9b019cbdd02cea8cc6f18cc5e116036dd20992631c638c7` (verified locally via `Get-FileHash -Algorithm SHA256`)
- **Verified Binary Execution (WSL2 Linux x86_64):**
  - `codebase-memory-mcp --version` → `codebase-memory-mcp 0.12.1`
  - **Transport:** MCP over `stdio` (`JSON-RPC 2.0`, `protocolVersion: "2024-11-05"`) and `cli` one-shot mode. Upstream does **not** expose an MCP HTTP endpoint; the adapter wraps `stdio` behind FastAPI REST (`/v1/*`) and Streamable HTTP (`/mcp`).
  - **Verified `tools/list` (17 tools):** `index_repository`, `search_graph`, `query_graph`, `trace_path`, `get_code_snippet`, `get_file_outline`, `get_graph_schema`, `compare_graphs`, `get_architecture`, `search_code`, `list_projects`, `delete_project`, `index_status`, `check_index_coverage`, `detect_changes`, `manage_adr`, `ingest_traces`.
  - **Key observed schemas & flags:**
    - `index_repository`: required `repo_path`; supports `name`, `mode` (`full|moderate|fast|cross-repo-intelligence`), `persistence` (default `false` — kept `false` so nothing is written into read-only source mounts).
    - `search_graph`: required `project`; supports `name_pattern`, `limit`, `format: "json"`. Returns `groups[].{qn_prefix, file, rows: [[name, label, lines, in, out]]}`.
    - `search_code`: required `pattern`, `project`; supports `result_limit`, `raw_limit`, `format: "json"`. Observed `elapsed_ms: 4` on synthetic fixture.
    - `index_status`: required `project`; `format: "json"` reports `status`, `nodes`, `edges`, `indexed_at`.

## 2. Adapter Implementation (`services/codebase/`)

- **Dockerfile & Runtime Security:**
  - Downloads `codebase-memory-mcp-linux-amd64-portable.tar.gz` (`v0.12.1`) and verifies SHA-256 `8c3d0341064301e78ecc84cf7348d4e2c6562a0f57448243ea560aaa34de36c1` during image build.
  - Runs as non-root `USER 10001:10001`, compatible with `read_only: true` rootfs + `tmpfs: [/tmp]` + persistent volume `/data` (`CBM_CACHE_DIR=/data/cbm-cache`, `/data/adapter-state.db`).
  - No Docker socket; source trees are intended to be mounted read-only under `/repos` (`CBM_ALLOWED_ROOT=/repos`).
  - Disables upstream `auto_index`, `auto_watch`, and `watcher_enabled` on startup; never invokes `semantic_query` or any external LLM/network call.
- **API & Contract Conformance (`INTERFACE-V1.md`):**
  - `/livez` (200) and `/healthz` (200 only when `/data` is writable and `codebase-memory-mcp` binary is ready; 503 otherwise).
  - Bearer token authentication with `read` and `admin` scopes (`401` unauthenticated, `403` insufficient scope).
  - Strict Pydantic validation (`extra="forbid"`, `query <= 512`, `limit <= 10`, `max_chars <= 8000`) returning `{error: {code, message}}` without leaking host paths.
  - Scope isolation via deterministic `cbm_<project_id>_<worktree_id>` naming, preventing collisions across duplicate repo names, same-branch worktrees, and detached HEADs.
  - Incremental refresh & deduplication: tracks `head_commit` + `dirty_fingerprint` in SQLite WAL (`/data/adapter-state.db`), deduplicates concurrent index jobs, returns `no_op: true` on unchanged snapshots (no full reindex on session start), and marks stale results as `index_freshness: "stale"`.
  - Path traversal, symlink escape, and secret/dependency exclusions (`.env*`, `*.pem`, `*.key`, `node_modules/**`, `dist/**`, `**/credentials*`).
  - Bounded JSON envelope: enforces `len(json) <= max_chars` with `truncated: true` when trimmed.

## 3. Test Commands & Results

Command executed:
```powershell
D:\workspace1\ai-memory-hub-agent-a\.venv\Scripts\python.exe -m pytest D:\workspace1\ai-memory-hub-agent-a\tests\codebase -v
```

- `tests/codebase/test_security_and_isolation.py` (6 unit tests):
  - `test_path_traversal_and_absolute_rejection`: PASSED
  - `test_symlink_escape_rejected`: PASSED
  - `test_secret_and_dependency_exclusions`: PASSED
  - `test_windows_host_path_mapping`: PASSED
  - `test_linked_worktree_and_detached_head`: PASSED
  - `test_auth_scope_isolation_and_budget`: PASSED
- `tests/codebase/test_real_cbm_integration.py` (1 end-to-end integration test against real `codebase-memory-mcp 0.12.1` in WSL2):
  - `test_real_cbm_java_ts_worktrees_dirty_and_restart`: PASSED (verified synthetic Java & TypeScript repos with same symbol `processOrder`, two git worktrees incl. detached HEAD, cross-scope isolation, dirty edit `stale` → reindex `fresh`, `no_op` on repeated session start, restart persistence, and `/mcp` `tools/list` + `tools/call`).

## 4. Open Blockers & Integration Notes for Lead

1. **Docker Desktop daemon on this Windows host:** `com.docker.service` is `Stopped` and `Disabled` (`Start=4`), so `docker build` / `docker compose up` could not be executed in this environment. The `v0.12.1` Linux binary itself was verified in WSL2 (`docker-desktop` distro). Lead should run `docker compose build codebase` and `docker compose up -d --wait` on a host with Docker Desktop running.
2. **Volume startup fix for `read_only: true`:** `app/main.py` lifespan now ensures `DATA_DIR`, `CBM_CACHE_DIR`, and `HOME` directories exist inside `/data` before starting `codebase-memory-mcp`, avoiding permission/missing-dir issues if an existing `/data` volume from the scaffold is reused.
3. **Suggested `docker-compose.yml` env wiring for `codebase` (Lead-owned):**
   - Pass `CODEBASE_READ_TOKENS`, `CODEBASE_ADMIN_TOKENS`, and `BRIDGE_URL=http://memory-bridge:8082` (or `REGISTRY_FILE`).
   - Mount each approved host worktree read-only (`:ro`) under `/repos/...` via host-generated compose override.
4. **Host-side path mapping:** The adapter exposes `map_to_host_path(host_root, rel_posix_path)` and keeps `path` repository-relative in `/v1/search/code` responses per `INTERFACE-V1.md`.

## 5. V2 Readiness Notes (`docs/V2-AGENT-BOARD.md`, `docs/tasks/V2-AGENT-A.md`)

- **Gate Status:** `lead/v2-orchestrator` does not exist yet on `origin`; all V2 tasks are `QUEUED` pending Lead review/integration of V1 PRs `#4`/`#3`/`#2`, Docker daemon verification, and frozen V2 schemas (`ORCHESTRATOR-V2.md`, `SESSION-ANALYTICS-V2.md`). Per `V2-AGENT-BOARD.md` ("Do not start feature branches from scaffold"), `agent/v2-model-registry` has not been created yet.
- **Official Provider Docs Verified (2026-11-25) for V2 Catalog & Forecasting:**
  - **Anthropic (`platform.claude.com`):** Mainline lineup (`claude-fable-5-2`, `claude-opus-5-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`) with 1M context window, up to 128k output, tiered Haiku prompt-length pricing, prompt caching, and batch support.
  - **OpenAI (`developers.openai.com`):** Prompt caching automatic >= 1,024 tokens (128-token increments), 30m default TTL (1.25x cache write) vs 12h extended TTL (2.5x cache write), ~90–95% cached read discount, long-context surcharge (>272k input), Batch/Flex ~50% discount.
  - **Google Gemini (`ai.google.dev`):** Gemini 4 (Carbon, Argon, Neon, Hydrogen) & Gemini 3.x implicit caching (4,096 min tokens; Gemini 4 multi-turn requires >=2,050 new tokens to extend cache) vs Gemini 2.5/3 explicit caching (2,048/4,096 min tokens, TTL storage fee), 90–95% cache read discount.
- **Ready on `lead/v2-orchestrator` creation:** Will branch `agent/v2-model-registry` and implement `services/orchestrator/catalog/**`, `services/orchestrator/routing/**`, `services/orchestrator/forecast/**`, and corresponding test suites (`tests/orchestrator/catalog/**`, `tests/orchestrator/forecast/**`).
