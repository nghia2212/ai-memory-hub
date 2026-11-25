# Agent B — V2 storage and Vietnamese dashboard
Start after Lead freezes V1 integration SHA and V2 contracts. Branch agent/v2-observability; own services/memory-bridge/**, services/dashboard/**, tests/bridge/**, tests/dashboard/**, docs/evidence/v2-agent-b.md. Implement migrations and typed API for registry/evaluation/plans/approval/jobs/usage/quota/policies; atomic approval and claims, auth, idempotency, audit, restart reconciliation, aggregation. Dashboard prompt/plan approval, exact model/task/session status, stop/pause, model matrix, token/cache/cost and distinct quota/context/budget percentages; unknown shown explicitly. Use mocked runner initially. Verify project isolation, CSRF, duplicate usage, denominator/window/source and no secrets. Incorporate V1.1 agent status proposal. Draft PR to lead/v2-orchestrator with evidence. No merge main.

## Extension: session analytics
Implement session ledger, features/outcomes, forecast snapshots/actuals, retention/deletion, consent, scoped APIs and dashboard history/cohort/forecast views per docs/SESSION-ANALYTICS-V2.md. Atomic budget reservations, dedup cumulative counters and unknown quota UX. Test persistence and isolation.


## Adaptive brain extension
Read docs/ADAPTIVE-BRAIN-V2.md. Add audited role configuration, effective vs preferred model, planner session/forecast metrics and separate planner/executor totals; Vietnamese dashboard 'Bộ não điều phối', availability and fallback reasons, approval/budget controls and unknown quota display.
