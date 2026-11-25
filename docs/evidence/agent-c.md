# Agent C Evidence — AI Memory Hub V1 & V1.1 Multi-Agent Integration

- **Branch:** `agent/v1-hermes-integrations`
- **Base:** `lead/v1-integration` (`60f0324e04a0536a5c74fd080db32e7d9f4c4e53`)
- **Draft PR:** https://github.com/nghia2212/ai-memory-hub/pull/2
- **Updated:** `2026-11-25T09:43:15+07:00`

## 1. Upstream & Host CLI Verification

### Hermes Agent v0.23.4 (`NousResearch/hermes-agent`)
- Verified image tag & manifest list digest via `docker manifest inspect nousresearch/hermes-agent:v0.23.4` and `docker buildx imagetools inspect nousresearch/hermes-agent:v0.23.4`:
  - Manifest list: `docker.io/nousresearch/hermes-agent:v0.23.4@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f`
  - `linux/amd64`: `sha256:3bc37fd8546e90fc41fe0d29511b4052840c281617cbcec6093bfe03d86a4ad9`
  - `linux/arm64`: `sha256:2223ae201f9b5994468d6e6ec499fc92cfb4a451592c013a02d314ffdaafd860`
- Recorded in `services/hermes/upstream.json`.

### DeepSeek API
- Verified official DeepSeek Hermes docs (`https://api-docs.deepseek.com/quick_start/agent_integrations/hermes/`): `https://api.deepseek.com`, models `deepseek-v4-pro`, `deepseek-flash`.
- **Live DeepSeek API call:** NOT RUN (requires project owner consent; tested against local `ThreadingHTTPServer` mock + `MockProvider`).

### Host CLIs & Hook Lifecycles
- **Claude Code:** `claude --version` -> `2.1.269 (Claude Code)`. MCP `--transport http` + `~/.claude/settings.json` hooks (`SessionStart`, `Stop`, `SessionEnd`).
- **Codex CLI:** `codex --version` -> `codex-cli 0.166.0`. Verified `codex mcp add --url <URL> --bearer-token-env-var <ENV_VAR>` and `codex features list | Select-String -Pattern "hook"` -> `hooks  stable  true`. Uses `~/.codex/config.toml` + `~/.codex/hooks.json` (requires `/hooks` interactive trust on first run; `integrations/common/handoff_cli.py` provided as fallback).
- **Gemini CLI:** NOT INSTALLED on this Windows host. Configured per official Gemini CLI docs (`~/.gemini/settings.json` `httpUrl` + `SessionStart`/`SessionEnd` hooks outputting JSON `hookSpecificOutput.additionalContext`, plus `handoff_cli.py` fallback).
- **PowerShell / Python:** Windows PowerShell `5.1.26100.9818`, `Python 3.13.17`.

## 2. Automated Test Results (17/17 Passed)

### Hermes Unit Tests
Command:
```powershell
python -m unittest discover -s D:\workspace1\ai-memory-hub-agent-c\tests\hermes -v
```
Result: **9/9 PASS (`2.575s`)**:
- `test_redact_secrets_and_paths`, `test_sanitize_drops_forbidden_fields_and_bounds`
- `test_disabled_server_health_and_livez`, `test_enabled_without_key_returns_503`
- `test_disabled_by_default_no_calls`, `test_missing_deepseek_key_fails_validation`
- `test_approval_gate_and_candidate_posting`, `test_budget_cap_stops_excess_events`
- `test_deepseek_provider_with_local_mock_http_and_retry`

### Multi-Agent Hooks, Installers & Handoff Tests
Command:
```powershell
python -m unittest discover -s D:\workspace1\ai-memory-hub-agent-c\tests\integrations -v
```
Result: **8/8 PASS (`16.439s`)**:
- `test_resolve_git_scope_with_spaces`, `test_hooks_fail_open_when_bridge_down`, `test_hooks_with_mock_bridge_and_no_transcript_read`
- `test_dry_run_install_idempotent_conflict_and_uninstall` (`install-claude.ps1`)
- `test_gemini_installer_lifecycle` (`install-gemini.ps1`: `-DryRun`, merge, idempotency, conflict/`-Force`, `-Uninstall`)
- `test_codex_installer_lifecycle` (`install-codex.ps1`: `-DryRun`, `config.toml` + `hooks.json` merge, no token in `config.toml`, idempotency, `-Uninstall`)
- `test_install_all_cli_detection_dry_run` (`install-all.ps1 -Agents auto -DryRun`)
- `test_claude_handoff_to_gemini_and_codex_shared_bridge`: end-to-end Claude → Gemini → Codex task handoff (`task_id=TASK-V11-01`, `project_id=proj-shared`, `worktree_id=wt-shared`) against single shared mock Bridge verifying `source_agent` (`claude`, `gemini`, `codex`) and shared index/memory.

### Docker Compose Config
- `docker compose -f D:\workspace1\ai-memory-hub-agent-c\docker-compose.yml --profile hermes config --quiet` → **exit code 0**.

## 3. Coordination with Agent B & Lead (Dashboard Agent Status)
- Per `AGENT-BOARD.md`, Agent C does not edit `services/memory-bridge/**` or `services/dashboard/**`.
- Added [integrations/common/AGENT-STATUS-PROPOSAL.md](file:///D:/workspace1/ai-memory-hub-agent-c/integrations/common/AGENT-STATUS-PROPOSAL.md) defining `GET /v1/agents/status` and Dashboard **Connected AI Agents** panel consuming standardized `source_agent` (`claude`, `gemini`, `codex`, `hermes`) and `task_id` events.

## 4. V2 Readiness & Host CLI Discovery (Preparation Only)

- **V2 Activation Gate (`docs/V2-AGENT-BOARD.md` on `origin/lead/v1-integration@c1c3036`):**
  - Status is **QUEUED**; `lead/v2-orchestrator` does not exist yet (pending V1 PR #4/#3/#2 review/integration, Docker daemon resolution, and frozen V2 contracts).
  - Per Lead's rule, Agent C has **not** created `agent/v2-orchestration` prematurely.
- **V1/V1.1 Hardening Applied on `agent/v1-hermes-integrations`:**
  - Added graceful `SIGINT`/`SIGTERM` shutdown in `services/hermes/app/server.py`.
  - Preserved `task_id` and `provenance.event_source_agent` in `services/hermes/app/worker.py`.
- **Verified Host CLI Non-Interactive & Telemetry Interfaces (read-only `--help` checks, zero paid calls):**
  - `claude` (`2.1.269`): `-p/--print`, `--output-format <text|json|stream-json>`, `--model <model>`, `--fallback-model <model>`, `--effort <low|medium|high|xhigh|max>`, `--max-budget-usd <amount>`, `--session-id <uuid>`, `--permission-mode <mode>`.
  - `codex` (`codex-cli 0.166.0`): `codex exec --json -m <model> -s <read-only|workspace-write> -C <DIR> --output-schema <FILE> -o <FILE> --ephemeral`. No CLI flag exposes remaining subscription quota (remains `unavailable` / `'Không có dữ liệu'`).
  - `gemini`: Not installed on host.

## 5. Blockers & Untested Items

1. **Docker Desktop daemon not running:** `docker compose --profile hermes build hermes` failed to connect to `npipe:////./pipe/dockerDesktopLinuxEngine`.
2. **Live Memory Bridge & Codebase MCP:** Mocked in tests pending Lead integration of Agent A & Agent B branches.
3. **DeepSeek live API:** Not called without owner consent.
4. **Gemini CLI binary:** Not installed on host; `gemini` CLI live handshake untested.
