# V3 board — QUEUED
Start only after Lead validates V2 and creates lead/v3-mcp-platform from approved V2 SHA. Current design branch is documentation only. Recommend three agents + Lead; max two simultaneous heavy jobs on 16 GiB host. Phases sequential; agents may work in parallel on disjoint files. Lead freezes proposed contracts/schema, owns compose, shared auth and V2 integration, coordinates cross-service tests; no agent changes other ownership without agreement.
| Agent | Branch (future) | Ownership | Depends |
|---|---|---|---|
| A | agent/v3-gateway-runtime | services/mcp-gateway, services/mcp-runtime, scripts/mcp-runtime, tests/mcp-runtime | registry contract |
| B | agent/v3-registry-dashboard | Bridge V3 migrations/API, dashboard, tests/bridge/v3, tests/dashboard/v3 | frozen schema |
| C | agent/v3-toolkit-workflows | services/mcp-toolkit, integrations/mcp, catalog manifests, workflow packs, tests/mcp-toolkit | gateway facade and grants |
| Lead | lead/v3-mcp-platform | shared contracts, integration/compose/QA, release evidence | validated V2 |
All branches from same frozen V3 base; Draft PRs to lead/v3-mcp-platform. No main merge, no real paid API, no production data, no unreviewed installation. Report exact SHA/tests/limitations. 
