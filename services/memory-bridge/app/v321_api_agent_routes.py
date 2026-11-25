"""V3.2.1 P14-P19 FastAPI router for AI Provider Gateway, Project Egress Policy, API Agent Runs & Hybrid Stages."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v321_agent_engine import V321AgentExecutionEngine
from .v321_api_agent_schema import (
    AgentRunCreateRequest,
    AgentRunPreviewRequest,
    HybridPlanCreateRequest,
    HybridStageUpdateRequest,
    ProjectEgressPolicyRequest,
    ProviderConnectionTestRequest,
    ProviderProfilePatchRequest,
    ProviderProfileUpsertRequest,
)
from .v321_provider_service import V321ProviderService


def create_v321_api_agent_router(
    prov_svc: V321ProviderService,
    engine: V321AgentExecutionEngine,
    cfg: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/v321", tags=["v3.2.1 p14-p19 ai providers & api agent"])

    def principal(request: Request) -> Principal:
        return authenticate_token(extract_bearer_token(request.headers.get("Authorization")), cfg)

    def reader(p: Principal = Depends(principal)) -> Principal:
        require_scope(p, "read")
        return p

    def writer(p: Principal = Depends(principal)) -> Principal:
        require_any_scope(p, "write", "planner", "runner", "admin")
        return p

    def admin(p: Principal = Depends(principal)) -> Principal:
        require_scope(p, "admin")
        return p

    # --- P14 & P15: AI Providers ---
    @router.get("/providers")
    def list_providers(p: Principal = Depends(reader)):
        return prov_svc.list_providers()

    @router.post("/providers", status_code=201)
    def create_provider(req: ProviderProfileUpsertRequest, p: Principal = Depends(admin)):
        return prov_svc.create_provider(req)

    @router.put("/providers/{profile_id}")
    def update_provider(profile_id: str, req: ProviderProfilePatchRequest, p: Principal = Depends(admin)):
        return prov_svc.update_provider(profile_id, req)

    @router.post("/providers/{profile_id}/test")
    def test_provider(profile_id: str, req: ProviderConnectionTestRequest, p: Principal = Depends(admin)):
        return prov_svc.test_provider_connection(profile_id, req)

    # --- P15 & P18: Project Egress & Budget Policy ---
    @router.get("/projects/{project_id}/egress-policy")
    def get_egress_policy(project_id: UUID, p: Principal = Depends(reader)):
        return prov_svc.get_project_egress_policy(str(project_id))

    @router.put("/projects/{project_id}/egress-policy")
    def set_egress_policy(project_id: UUID, req: ProjectEgressPolicyRequest, p: Principal = Depends(admin)):
        return prov_svc.set_project_egress_policy(str(project_id), req)

    # --- P16, P17, P18: Hub-managed API Agent Runs ---
    @router.post("/agent-runs/preview")
    def preview_agent_run(req: AgentRunPreviewRequest, p: Principal = Depends(writer)):
        return engine.preview_run(req)

    @router.post("/agent-runs", status_code=201)
    def create_agent_run(req: AgentRunCreateRequest, p: Principal = Depends(admin)):
        return engine.create_and_run(req, actor=p.role)

    @router.get("/agent-runs")
    def list_agent_runs(project_id: UUID | None = None, limit: int = Query(30, ge=1, le=100), p: Principal = Depends(reader)):
        return engine.list_runs(str(project_id) if project_id else None, limit=limit)

    @router.get("/agent-runs/{run_id}")
    def get_agent_run(run_id: str, p: Principal = Depends(reader)):
        return engine.get_run(run_id)

    @router.post("/agent-runs/{run_id}/cancel")
    def cancel_agent_run(run_id: str, p: Principal = Depends(admin)):
        return engine.cancel_run(run_id)

    # --- P19: Hybrid Mode Stages ---
    @router.post("/hybrid-plans", status_code=201)
    def create_hybrid_plan(req: HybridPlanCreateRequest, p: Principal = Depends(admin)):
        return engine.create_hybrid_plan(req)

    @router.get("/hybrid-plans/{plan_id}")
    def list_hybrid_stages(plan_id: str, project_id: UUID, p: Principal = Depends(reader)):
        return engine.list_hybrid_stages(plan_id, str(project_id))

    @router.patch("/hybrid-stages/{stage_id}")
    def update_hybrid_stage(stage_id: str, req: HybridStageUpdateRequest, p: Principal = Depends(admin)):
        return engine.update_hybrid_stage(stage_id, req)

    return router
