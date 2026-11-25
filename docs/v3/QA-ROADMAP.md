# Delivery stages and QA gates
No dates promised; estimates in relative milestones. V1 Docker runtime and V2 integration/approval/telemetry gates remain mandatory. V3 design can be reviewed now; code starts only from validated V2 SHA and frozen contracts.
| Stage | Scope | Exit |
|---|---|---|
| V3.0 | Registry, migrations, manifest/import, profiles, grants, audit, minimal UI | add/remove synthetic HTTP and stdio without hub restart |
| V3.1 | Host runtime broker, lifecycle/leases/resources, gateway discovery | real Windows stdio/container + remote mock, drain/restart/isolation |
| V3.2 | Code Mode sandbox, workflows, analytics | sandbox adversarial suite and measured baseline |
| V3.3 | Developer & Architect packs, SQLcl optional | Java synthetic repo, C4 artifact, read-only DB fixture |
| V3.4 | Game 3D pack, browser QA | Blender version verified, staged GLB + render + browser smoke |
| V3.5 | DevOps/monitoring packs, Marketplace, install/update/rollback UX | read-only mocks, independent version update |
| V3.6 | Hardening/optimization, release | all mandatory gates, backup/recovery, actual Windows Docker stats |
## Mandatory tests
- Add a new synthetic MCP via dashboard JSON without source diff/build/restart; tool searchable and callable using same client endpoint; verify hub PID/container ID unchanged. Add new profile; disable/re-enable; remove; preserve registry after restart; import/export without secrets. Client reconnect documented where needed.
- Pin exact version and manifest; quarantine malicious package/import; rollback failed update; reject permission expansion without approval; tool-list change invalidates plan; dependency cycle; partial install cleanup; stale registry; remote disconnect.
- Concurrent leases and overlapping profiles, idle timeout, manual pin, cancellation, failed heartbeat, orphan reconciliation, no stop user-owned process, exclusive scene, CPU/RAM pressure queue; 16 GiB real laptop test incl WSL overhead, measure peak RSS/working set with timestamps; no synthetic values presented as actual.
- Auth all routes, cross-project/worktree/agent isolation, denied read->write escalation, CSRF/origin protection, path traversal/junction/symlink/SSRF, tool prompt injection, secret leakage, no arbitrary shell, no socket, no raw corporate data. Tests with malicious synthetic fixtures.
- Code Mode infinite loop, memory exhaustion, network/file/process escape, forbidden import, schema mismatch, tool budget, nested calls, cancellation, unapproved destructive tool, duplicate/uncertain writes; direct fallback only within same grant. No paid calls.
- Real protocol handshake/list/call for installed Claude/Codex/agy and Gemini if installed; report not available explicitly; verify schema change behavior. V2 plan binding/approval and session metrics regression; Hermes off core unaffected.
- Java tests synthetic project, Blender disposable copy + GLB checks, browser screenshots, no production credentials. 
- Backup/restore DB + manifests/workflows, retention deletion, migration from V2 with failure recovery, no loss of V1/V2 data; rollback release documented.
## Benchmark
Compare same task and same model/effort/auth settings using direct MCP vs discovery vs Code Mode, fixed fixtures and repeated runs (>=10 paired per scenario for initial descriptive comparison, no strong statistical claim). Record measured provider input/output/cache separately, tool schema chars, output bytes, model turns, tool calls, latency, peak RAM, success/quality, errors/retries; account for planner cost and caching. Token estimates clearly labeled; no claim of savings if unavailable. Report median/range, workload and limitations. 
## Release
Lead runs unit/integration/security/Windows E2E, records commands, SHA, artifacts and skipped gates. Draft PR only until verified. No main merge/deploy without separate owner approval and completed mandatory gates. 
