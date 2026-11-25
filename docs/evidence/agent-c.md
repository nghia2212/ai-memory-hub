# Agent C Evidence — AI Memory Hub V1

- **Branch:** `agent/v1-hermes-integrations`
- **Base:** `lead/v1-integration` (`60f0324e04a0536a5c74fd080db32e7d9f4c4e53`)
- **Timestamp:** `2026-11-25T09:36:26+07:00`

## 1. Upstream Verification

### Hermes Agent v0.23.4 (`NousResearch/hermes-agent`)
- Verified image tag & manifest list digest via:
  - `docker manifest inspect nousresearch/hermes-agent:v0.23.4`
  - `docker buildx imagetools inspect nousresearch/hermes-agent:v0.23.4`
- Observed digests:
  - Manifest list: `docker.io/nousresearch/hermes-agent:v0.23.4@sha256:ef0650d523ee1b5ab32c779acabc446d9936869a9932591553688a62bcf0c42f`
  - `linux/amd64`: `sha256:3bc37fd8546e90fc41fe0d29511b4052840c281617cbcec6093bfe03d86a4ad9`
  - `linux/arm64`: `sha256:2223ae201f9b5994468d6e6ec499fc92cfb4a451592c013a02d314ffdaafd860`
- Invocation verified from upstream docs: `setup` (wizard) and `gateway run` (OpenAI-compatible API gateway on port `8642`). Recorded in `services/hermes/upstream.json`.

### DeepSeek API
- Verified official DeepSeek Hermes integration docs (`https://api-docs.deepseek.com/quick_start/agent_integrations/hermes/`):
  - Base URL: `https://api.deepseek.com`
  - Models: `deepseek-v4-pro`, `deepseek-flash`
- **Live DeepSeek API call:** NOT RUN (no live provider call without project owner consent; tested against local `ThreadingHTTPServer` mock + `MockProvider`).

### Host CLIs
- `claude --version`: `2.1.269 (Claude Code)`; `claude mcp --help` verified `--transport http <name> <url> --header "Authorization: Bearer ..."`.
- `codex --version`: `codex-cli 0.166.0`; `codex mcp add --help` verified `--url <URL> --bearer-token-env-var <ENV_VAR>`.
- `gemini --version`: NOT INSTALLED on this Windows host; `integrations/gemini/README.md` documented from official Gemini CLI docs (`gemini mcp add -s user -t http ...` and `~/.gemini/settings.json` `httpUrl`).
- `powershell.exe`: Windows PowerShell `5.1.26100.9818`.
- `python --version`: `Python 3.13.17`.

## 2. Automated Test Results

### Hermes Unit Tests
Command:
```powershell
python -m unittest discover -s D:\workspace1\ai-memory-hub-agent-c\tests\hermes -v
```
Result: **9 tests passed (`OK` in 2.566s)**:
- `test_redact_secrets_and_paths`: redacts `Bearer`, `sk-`, `ghp_`, DB credentials, emails, `C:\Users\<user>`, `/home/<user>`.
- `test_sanitize_drops_forbidden_fields_and_bounds`: strips `transcript`, `transcript_path`, `raw_code`, `source_code`, `diff_full`, bounds payload length.
- `test_disabled_server_health_and_livez`: `/livez` 200 and `/healthz` 200 (`enabled=False`) when `HERMES_ENABLED=false`.
- `test_enabled_without_key_returns_503`: `/healthz` 503 when `HERMES_ENABLED=true` and `HERMES_PROVIDER=deepseek` without `DEEPSEEK_API_KEY`.
- `test_disabled_by_default_no_calls`: zero bridge or provider calls when disabled.
- `test_missing_deepseek_key_fails_validation`: fails validation cleanly, zero outbound calls.
- `test_approval_gate_and_candidate_posting`: skips unapproved events/projects/kinds; posts `state="candidate"` with deterministic `idempotency_key`.
- `test_budget_cap_stops_excess_events`: enforces `HERMES_MAX_TOTAL_TOKENS_PER_RUN`.
- `test_deepseek_provider_with_local_mock_http_and_retry`: verifies `Authorization: Bearer ...`, `model=deepseek-flash`, retry on HTTP 503, and token accounting against local mock HTTP server.

### Claude Hooks & PowerShell Installer Tests
Command:
```powershell
python -m unittest discover -s D:\workspace1\ai-memory-hub-agent-c\tests\integrations -v
```
Result: **4 tests passed (`OK` in 8.939s)**:
- `test_resolve_git_scope_with_spaces`: resolves git root/worktree/branch/dirty state in paths with spaces.
- `test_hooks_fail_open_when_bridge_down`: `session_start.py`, `session_event.py --kind stop`, `session_event.py --kind session_end` all exit `0` when Bridge is unreachable.
- `test_hooks_with_mock_bridge_and_no_transcript_read`: verifies bounded `SessionStart` output (`<= 1500` chars) and `SessionEnd` event posting without reading `transcript_path`.
- `test_dry_run_install_idempotent_conflict_and_uninstall`: runs `scripts/install-claude.ps1` in a temp directory with spaces; verifies `-DryRun`, `.bak.*` backups, secret provisioning without stdout leakage, preservation of existing `mcpServers`/`hooks`/`.env`, idempotency on 2nd run, conflict detection + `-Force`, and safe `-Uninstall`.

### Docker Compose Config
Command:
```powershell
docker compose -f D:\workspace1\ai-memory-hub-agent-c\docker-compose.yml --profile hermes config --quiet
```
Result: **Exit code 0**.

## 3. Blockers & Untested Items

1. **Docker Desktop daemon not running:** `docker compose --profile hermes build hermes` failed to connect to `npipe:////./pipe/dockerDesktopLinuxEngine`. Container build & live Compose smoke tests remain blocked until Docker Desktop is started during Lead integration.
2. **Memory Bridge & Codebase MCP services:** Mocked in unit/hook tests (`services/memory-bridge` and `services/codebase` are being implemented concurrently on `agent/v1-memory-bridge` and `agent/v1-codebase`). End-to-end live Claude MCP tool call (`claude mcp list` / live `/mcp` handshake) is pending Lead integration.
3. **DeepSeek live API:** Not called per policy (requires owner consent and API key).
4. **Gemini CLI:** Not installed on this Windows host; integration guide follows official Gemini CLI documentation.
5. **GitHub CLI (`gh`):** Not installed on PATH; Draft PR creation may require manual creation or GitHub API token if not available in local credentials.

## 4. Security Checklist

- [x] `HERMES_ENABLED=false` by default; core services do not depend on Hermes.
- [x] Deny-by-default approval gate (`approved=true`, allowed event kind, project opt-in).
- [x] Secret/PII/local-path redaction and hard stripping of `transcript`, `transcript_path`, `raw_code`, `source_code`, `diff_full`.
- [x] No repository volume mount or Docker socket in `services/hermes`.
- [x] Hooks fail-open (`exit 0`) with short timeouts (`1.5s–2.0s`) and never read transcripts.
- [x] Installer provisions tokens into `.env` without printing secrets, creates backups, merges existing configs, and uninstalls only AI Memory Hub entries.
- [x] No real API keys, transcripts, or personal paths committed.
