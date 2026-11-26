# Windows pilot (H0–H2)
This is a parallel **isolated pilot**, not a production replacement. Existing Hub, Docker volumes, credentials and ongoing tasks are not modified by the native launcher. Do not run both schedulers on the same worktree/project. Use a disposable repo and synthetic prompts. Do not give production code or private data to a model without approval.

1. Before eventual migration: inventory running/queued/blocked tasks. Drain jobs; back up Memory Bridge using its existing authenticated backup mechanism (see docs/INTERFACE-V1.md), verify restore on a separate instance. Back up local config separately to a private location, never Git. Do not copy live SQLite files. No backup or migration is performed by these scripts.
2. Install official Hermes v0.23.4 via the release page (https://github.com/NousResearch/hermes-agent/releases/tag/v0.23.4); verify installer provenance and version. Open a new PowerShell terminal. If a native install exists, do not overwrite its configuration.
3. Run the following once (if the profile already exists, inspect it rather than recreating):
   
       hermes profile create hub-pilot --no-credentials --no-alias
       hermes -p hub-pilot setup

   Select an approved model, avoid paid API fallback, disable unneeded tools and messaging integrations. Do not import all credentials. Profile isolation does not imply physical filesystem sandboxing.
4. In the Hub repo:
   
       .\scripts\hermes\Start-HermesEdition.ps1 -Action Check
       .\scripts\hermes\Start-HermesEdition.ps1 -Action CreateBoard
       .\scripts\hermes\Start-HermesEdition.ps1 -Action List
       .\scripts\hermes\Start-HermesEdition.ps1 -Action Dashboard

   Open http://127.0.0.1:9119. CreateBoard is explicit and intended once; if already created, use List. Dashboard runs in foreground. No task is queued by these commands.
5. On the pilot board, add a synthetic task, leave it unassigned until review. Verify board persistence after restarting Dashboard. Configure an approved worker profile and test only in a disposable workspace. Start gateway in a separate PowerShell window **only after reviewing all boards/profiles**; Hermes gateway may dispatch ready cards:

       .\scripts\hermes\Start-HermesEdition.ps1 -Action Gateway

   Record real task IDs, status, events, review and restart behavior. Never put secrets in task bodies. Stop pilot foreground processes with Ctrl+C.
6. For H4 later, after draining and stopping legacy jobs/runners, start only existing memory services with scripts/hermes/Start-MemoryServices.ps1. Register existing authenticated local MCP endpoints through native Hermes MCP settings; do not expose bridge credentials to the browser or Git. Verify project/worktree isolation. This is not automatic configuration.
7. Rollback: stop pilot gateway/dashboard, leave pilot data intact, return to legacy branch/launcher only after verifying no overlapping workers. No deletion of volumes or upstream profiles. Keep old tasks in their original system until H8 reconciliation.

## Evidence checklist
- [ ] Official runtime installed, exact version, profile and credentials verified.
- [ ] Scratch board create/list and native dashboard.
- [ ] Gateway worker run, review gate, restart with durable board, no duplicate claim.
- [ ] Claude/Codex real subscription execution and quota exhaustion behavior.
- [ ] Existing memory data preserved; backup/restore independently verified.
- [ ] No production job resumed or dispatched by pilot.
- [ ] Record measured usage, success and failure evidence (no guessed quota).

No Windows runtime tests have been executed by the GitHub-only migration author. Do not treat this checklist as passing.
