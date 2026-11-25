# Initial catalog and profiles (candidate, not preinstalled)
Each entry must receive exact source/version/digest, transport, license, actual tool list, auth, resource benchmark and data policy before enabled. Unsupported entries remain unavailable. No auto-install all. Existing core Memory Bridge/Codebase retained.
| Group | Candidates | Runtime/dependency | Default |
|---|---|---|---|
| Core | Memory Bridge, Codebase Memory | existing Docker/local | preserve |
| Development | Serena, Context7, JetBrains, Filesystem, Git, GitHub | stdio/IDE/remote; Context7/GitHub external | scoped read |
| Architecture | Structurizr, document generator, custom Java toolkit | host/stdio/container | read/artifact staging |
| DB | Oracle SQLcl, SQLite wrapper, Flyway/Liquibase | installed Java/DB, local wrapper | read-only DEV/UAT |
| Quality | SonarQube MCP, Playwright, Chrome DevTools, JUnit/JaCoCo | external/local Sonar server, dedicated browser | isolated test |
| 3D | Blender MCP (ahujasid, ceeyang, yi00it, production candidate), asset wrappers | Windows Blender addon, scripts | one verified adapter per scene |
| DevOps | Docker MCP Toolkit, Kubernetes MCP, Cloudflare | host broker/remote API | read-only |
| Ops | Grafana, Sentry, Atlassian Rovo | local/remote services | read-only |
| Workflows | OpenRewrite, ArchUnit, Spring Modulith, Maven/Gradle, Testcontainers, OWASP Dependency-Check, JMeter/k6 | approved typed wrapper packages | bounded commands |
## Profiles
Java Developer: core, Serena, Context7 optional egress, Git, Maven/JUnit wrapper. Architect: core, Structurizr, ArchUnit, Spring Modulith, optional Oracle metadata. DB: SQLcl read-only, SQLite, migration dry-run. Game: Blender exclusive scene, GLB validation, codebase, Serena, Playwright sequential heavyweight. Frontend: Context7, Playwright, DevTools. Review: Serena, GitHub read, Sonar, ArchUnit. DevOps: GitHub, Docker status, Kubernetes read, Cloudflare read. Incident: Grafana/Sentry sanitized, core. Docs: Structurizr, Mermaid/PlantUML, doc generator. User-defined profiles import/export, overlapping groups dedupe by revision/isolation key; enabling profile does not authorize every tool.
## Java workbench
Inputs registered repo/worktree, Maven/Gradle wrapper reviewed, module scope, timeout. Tools proposed (not upstream): dependency_tree, archunit_check, unit_test, integration_test, coverage_report, dependency_scan, api_contract_validate, migration_dry_run, openrewrite_preview, diagram_export. No freeform command/SQL; validate args, run in disposable worktree, no arbitrary test scripts from unknown repo, network opt-in (dependencies), report exit code/log summary/artifact. OpenRewrite changes require explicit write grant; testcontainers via dedicated approved host capability, not gateway socket. Banking transfer sample only synthetic; output C4/ADR/sequence/OpenAPI and transaction/idempotency/reconciliation tests.
## 3D factory
Tripo external generation optional separate cost/data approval. Import approved GLB copy, inspect mesh/material/UV/bones/clips, optimize triangles, preserve originals, rig/weight QA, render front/side/3-quarter, export to staging, validate glTF, integrate game on approved worktree, Playwright screenshot and performance QA. Existing project baseline 30k triangles/30 bones/30 FPS only when asset-specific contract says so; no universal cap. Keep Python scripts as versioned reusable MCP wrappers; prefer verified Blender adapter and headless batch when appropriate. No overwriting source assets; owner-owned Blender never auto-close; unsaved scene blocks drain. Regression checks for textures, animation, scale, orientation and mobile performance; render preview evidence. 
## DevOps
Read-only logs/metrics/status default; separate approvals for changing infrastructure, database, push, merge, deployment. No production access default. Do not connect corporate accounts or customer data without organizational consent.
