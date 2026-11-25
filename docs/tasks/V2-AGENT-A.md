# Agent A — V2 catalog and evaluation
Start after Lead freezes V1 integration SHA and V2 contracts. Branch agent/v2-model-registry; own services/orchestrator/catalog/**, services/orchestrator/routing/**, tests/orchestrator/catalog/**, docs/evidence/v2-agent-a.md. Read docs/ORCHESTRATOR-V2.md. Implement official-source adapters, daily/on-demand refresh, versioned snapshot, lifecycle eligibility, evaluation matrix and deterministic scoring with explanation; fixtures for stale/retired/unknown/new models. Verify official documentation at implementation time; no invented model IDs, flags, price/quota. Coordinate host discovery contract with C and persistence with B. Draft PR to lead/v2-orchestrator, report SHA/test evidence/blockers. No merge main.

## Extension: forecasting
Own services/orchestrator/forecast/** and tests/orchestrator/forecast/**. Implement complexity rubric, cohort selection, cold start, P50/P90, plan aggregation, forecast calibration/drift, quota-unit compatibility and alternative model forecasts per docs/SESSION-ANALYTICS-V2.md. Use only pre-execution features and measured samples; no LLM or fake data. Coordinate B/C schema.


## Adaptive brain extension
Read docs/ADAPTIVE-BRAIN-V2.md. Verify preferred labels against official catalogs and supported runner/account capability; implement brain role eligibility/routing, configurable ranking and measured quality/cost comparison. No fabricated IDs or benchmark scores. Coordinate adapter capability contract with C.
