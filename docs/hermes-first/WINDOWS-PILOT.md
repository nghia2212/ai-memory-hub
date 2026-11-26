# Hermes-first Docker pilot on Windows
No separate Hermes installation. The official image is managed by the existing Hub Compose project. Use Docker Desktop Linux containers. This is a pilot, not production cutover: no worker adapter, no automatic migration and no dual-dispatch of a project.

## Start
1. Preserve legacy data and drain active legacy jobs before testing. Stop the old Dashboard, optional legacy Hermes worker and Host Brain process using the unified Stop script before switching, so the adapter reloads the new source and local configuration. Back up through the existing Memory Bridge backup API and verify restore separately.
2. Run from repo root:
   
       .\scripts\Start-AIMemoryHub.ps1 -StartDockerDesktop -HealthTimeoutSec 180

   Start pulls the pinned official image if missing, creates the named volume, provisions missing dashboard credentials through init-env.ps1 and waits for authenticated Dashboard /api/status. Existing .env values are preserved by the existing bootstrap; backup is ignored by Git. It also starts the existing memory services and Host Brain Agent, but suppresses legacy automatic recovery in pilot mode. Do not submit the same project to both schedulers. No production tasks should be submitted to Hermes yet.
3. Open http://127.0.0.1:9119. Credentials are HERMES_NATIVE_DASHBOARD_USER and HERMES_NATIVE_DASHBOARD_PASSWORD in local .env. Do not share or commit them. Legacy UI is off by default; use -LegacyOnly for rollback. Hub status is a native Hermes plugin tab.
4. Configure an approved model/provider via official Dashboard or interactive setup:
   
       docker compose exec --user hermes -e HOME=/opt/data hermes-native hermes setup

   Model credentials require explicit operator setup; do not auto-import host tokens or enable paid fallback. No host workspace mount or Docker socket. For native Kanban, use disposable synthetic tasks only. Gateway is already supervised and may dispatch eligible tasks.
5. Check status:
   
       .\scripts\Status-AIMemoryHub.ps1
       docker compose ps
       docker compose exec --user hermes -e HOME=/opt/data hermes-native hermes --version

   Dashboard readiness is not proof of worker, model auth, MCP or recovery readiness.
6. Inspect Hermes Kanban and wait for all workers to finish before stopping:
   
       .\scripts\Stop-AIMemoryHub.ps1 -HermesDrained

   Stop preserves both legacy volumes and hermes-native-data. Restart uses the same state; interrupted task behavior still requires an actual test. Use -Force only when accepting interruption. No down -v.
7. Rollback: after draining Hermes, explicitly stop it with docker compose stop hermes-native; start with scripts/Start-AIMemoryHub.ps1 -LegacyOnly. LegacyOnly refuses startup while the official Hermes container is still running; it never stops it implicitly. No data deletion.
8. For Docker Compose directly, run scripts/init-env.ps1 first (secrets are mandatory) then docker compose up -d (plugin setup requires the unified launcher). Prefer unified launcher to check readiness and start Windows Host Brain.

## Gates not yet passed
- Real Windows Docker pull/start/login and restart persistence.
- Approved model authentication and scratch Kanban worker lifecycle.
- Legacy task drain, backup/restore and migration.
- Claude/Codex/Gemini/agy external worker adapter, quota and permissions.
- Native MCP registration of existing project memory, correct scope.
- Real token and recovery evidence.

References: https://github.com/NousResearch/hermes-agent/blob/v0.23.4/website/docs/user-guide/docker.md and https://github.com/NousResearch/hermes-agent/blob/v0.23.4/website/docs/user-guide/features/web-dashboard.md.
