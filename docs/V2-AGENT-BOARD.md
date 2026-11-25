# V2 board — specification committed; implementation queued
Dependency: review/integrate V1 A/B/C PRs #4/#3/#2, resolve Docker daemon, pass V1 required gates. Lead creates lead/v2-orchestrator from tested integration SHA (not main unless approved). Do not start feature branches from scaffold. Lead owns shared schema, Compose, security, cross-agent QA and release. A catalog/routing, B storage/UI, C planner/host runner; see docs/tasks/V2-AGENT-*.md. Status all QUEUED. Freeze interfaces before parallel work. Each agent commits/pushes Draft PR to V2 integration only. No merge main before required tests. V1.1 C handoff at 3376a3a349b4fddf1bb7e6d8e4f158f6ae6dc95e reports 17 tests passed; not independently rerun. V2 decisions are requirements, not deployed features.

Mandatory V2 extension: docs/SESSION-ANALYTICS-V2.md. A forecast engine; B ledger/dashboard; C telemetry and plan integration. Lead freezes schema and validates forecast accuracy/unknown quota handling. All queued pending V1 gates.


Mandatory adaptive brain extension: docs/ADAPTIVE-BRAIN-V2.md. A owns eligibility/routing, B role config/dashboard/accounting, C provider-neutral planner adapters and host compatibility, Lead freezes shared contracts and verifies runtime. Preferred Neon is conditional on supported access. All V2 work remains queued pending V1 integration gates.
