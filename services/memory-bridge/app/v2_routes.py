"""FastAPI APIRouter for Memory Bridge V2 endpoints (/v2/*)."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .errors import BridgeError
from .security import AuthPrincipal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v2_schemas import (
    BrainConfigUpdateRequest,
    CatalogRefreshRequest,
    EvaluationCreateRequest,
    ForecastComputeRequest,
    JobCheckpointRequest,
    JobClaimRequest,
    JobCompleteRequest,
    JobEventRequest,
    JobHeartbeatRequest,
    PlanApproveRequest,
    PlanCreateRequest,
    PlanLifecycleRequest,
    PlanRevisionUpdateRequest,
    PolicyUpdateRequest,
    QuotaSnapshotCreateRequest,
    RoutingRecommendRequest,
    SessionUpsertRequest,
    UsageEventCreateRequest,
)
from .v2_service import V2BridgeService


def create_v2_router(service: V2BridgeService, cfg: Settings) -> APIRouter:
    router = APIRouter(prefix="/v2", tags=["v2"])

    def get_principal(request: Request) -> AuthPrincipal:
        return authenticate_token(extract_bearer_token(request.headers.get("Authorization")), cfg)

    def req_read(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(p, "read")
        return p

    def req_admin(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(p, "admin")
        return p

    def req_runner(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(p, "runner")
        return p

    def req_planner_or_admin(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_any_scope(p, "planner", "admin")
        return p

    def req_read_or_planner(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_any_scope(p, "read", "planner")
        return p

    def req_telemetry_writer(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_any_scope(p, "runner", "planner", "write")
        return p

    def req_admin_or_runner(p: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_any_scope(p, "admin", "runner")
        return p

    # --- Catalog & Evaluations ---
    @router.get("/catalog")
    async def get_catalog(snapshot_id: str | None = Query(default=None), _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_catalog(snapshot_id)

    @router.post("/catalog/refresh", status_code=201)
    async def refresh_catalog(req: CatalogRefreshRequest, p: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.refresh_catalog(req, actor=p.role)

    @router.get("/evaluations")
    async def list_evaluations(
        task_type: str | None = Query(default=None),
        model_id: str | None = Query(default=None),
        auth_mode: str | None = Query(default=None),
        project_id: UUID | None = Query(default=None),
        _: AuthPrincipal = Depends(req_read),
    ) -> dict[str, Any]:
        return service.list_evaluations(
            task_type=task_type,
            model_id=model_id,
            auth_mode=auth_mode,
            project_id=str(project_id) if project_id else None,
        )

    @router.post("/evaluations", status_code=201)
    async def create_evaluation(req: EvaluationCreateRequest, p: AuthPrincipal = Depends(req_planner_or_admin)) -> dict[str, Any]:
        return service.create_evaluation(req, actor=p.role)

    @router.post("/routing/recommend")
    async def recommend_routing(req: RoutingRecommendRequest, _: AuthPrincipal = Depends(req_read_or_planner)) -> dict[str, Any]:
        return service.recommend_routing(req)

    # --- Policies & Adaptive Brain ---
    @router.get("/policies")
    async def get_policies(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_policy(str(project_id))

    @router.put("/policies")
    async def update_policies(req: PolicyUpdateRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.update_policy(req)

    @router.get("/brain/config")
    async def get_brain_config(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_brain_config(str(project_id))

    @router.put("/brain/config")
    async def update_brain_config(req: BrainConfigUpdateRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.update_brain_config(req)

    # --- Plans & Approvals ---
    @router.get("/plans")
    async def list_plans(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.list_plans(str(project_id))

    @router.post("/plans", status_code=201)
    async def create_plan(req: PlanCreateRequest, p: AuthPrincipal = Depends(req_planner_or_admin)) -> dict[str, Any]:
        return service.create_plan(req, created_by=p.role)

    @router.get("/plans/{plan_id}")
    async def get_plan(plan_id: UUID, project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_plan(str(plan_id), str(project_id))

    @router.put("/plans/{plan_id}/revisions")
    async def revise_plan(plan_id: UUID, req: PlanRevisionUpdateRequest, _: AuthPrincipal = Depends(req_planner_or_admin)) -> dict[str, Any]:
        return service.update_plan_revision(str(plan_id), req)

    @router.post("/plans/{plan_id}/approve")
    async def approve_plan(plan_id: UUID, project_id: UUID, req: PlanApproveRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.approve_plan(str(plan_id), str(project_id), req)

    @router.post("/plans/{plan_id}/reject")
    async def reject_plan(plan_id: UUID, req: PlanLifecycleRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.transition_plan(str(plan_id), "reject", req)

    @router.post("/plans/{plan_id}/pause")
    async def pause_plan(plan_id: UUID, req: PlanLifecycleRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.transition_plan(str(plan_id), "pause", req)

    @router.post("/plans/{plan_id}/resume")
    async def resume_plan(plan_id: UUID, req: PlanLifecycleRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.transition_plan(str(plan_id), "resume", req)

    @router.post("/plans/{plan_id}/cancel")
    async def cancel_plan(plan_id: UUID, req: PlanLifecycleRequest, _: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.transition_plan(str(plan_id), "cancel", req)

    # --- Narrow Host Runner Jobs ---
    @router.post("/jobs/claim")
    async def claim_job(req: JobClaimRequest, _: AuthPrincipal = Depends(req_runner)) -> dict[str, Any]:
        return service.claim_job(req)

    @router.post("/jobs/{job_id}/heartbeat")
    async def heartbeat_job(job_id: UUID, req: JobHeartbeatRequest, _: AuthPrincipal = Depends(req_runner)) -> dict[str, Any]:
        return service.heartbeat_job(str(job_id), req)

    @router.post("/jobs/{job_id}/events")
    async def job_event(job_id: UUID, req: JobEventRequest, _: AuthPrincipal = Depends(req_runner)) -> dict[str, Any]:
        return service.record_job_event(str(job_id), req)

    @router.post("/jobs/{job_id}/checkpoint")
    async def checkpoint_job(job_id: UUID, req: JobCheckpointRequest, _: AuthPrincipal = Depends(req_runner)) -> dict[str, Any]:
        return service.checkpoint_job(str(job_id), req)

    @router.post("/jobs/{job_id}/complete")
    async def complete_job(job_id: UUID, req: JobCompleteRequest, _: AuthPrincipal = Depends(req_runner)) -> dict[str, Any]:
        return service.complete_job(str(job_id), req)

    @router.post("/jobs/{job_id}/cancel")
    async def cancel_job(job_id: UUID, project_id: UUID, p: AuthPrincipal = Depends(req_admin_or_runner)) -> dict[str, Any]:
        return service.cancel_job(str(job_id), str(project_id), actor=p.role)

    # --- Sessions, Usage, Quotas, Forecasts & Calibration ---
    @router.get("/sessions")
    async def list_sessions(
        project_id: UUID,
        worktree_id: UUID | None = Query(default=None),
        task_type: str | None = Query(default=None),
        complexity: str | None = Query(default=None),
        model_id: str | None = Query(default=None),
        effort: str | None = Query(default=None),
        outcome: str | None = Query(default=None),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        _: AuthPrincipal = Depends(req_read),
    ) -> dict[str, Any]:
        return service.list_sessions(
            str(project_id),
            worktree_id=str(worktree_id) if worktree_id else None,
            task_type=task_type,
            complexity=complexity,
            model_id=model_id,
            effort=effort,
            outcome=outcome,
            from_date=from_date,
            to_date=to_date,
        )

    @router.post("/sessions", status_code=201)
    async def upsert_session(req: SessionUpsertRequest, p: AuthPrincipal = Depends(req_telemetry_writer)) -> dict[str, Any]:
        if p.role == "write" and (req.role != "manual_cli" or any((req.plan_id, req.plan_revision_id, req.task_id, req.attempt_id, req.parent_session_id))):
            raise BridgeError(403, "manual_scope_required", "Write token may only register unlinked manual sessions")
        return service.upsert_session(req)

    @router.get("/sessions/{session_id}")
    async def get_session(session_id: UUID, project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_session(str(session_id), str(project_id))

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: UUID, project_id: UUID, p: AuthPrincipal = Depends(req_admin)) -> dict[str, Any]:
        return service.delete_session(str(session_id), str(project_id), actor=p.role)

    @router.get("/analytics/cohorts")
    async def get_cohorts(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_cohorts(str(project_id))

    @router.post("/forecasts")
    async def compute_forecast(req: ForecastComputeRequest, _: AuthPrincipal = Depends(req_read_or_planner)) -> dict[str, Any]:
        return service.compute_forecast(req)

    @router.get("/forecasts/{forecast_id}/actual")
    async def get_forecast_actual(forecast_id: UUID, project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_forecast_actual(str(forecast_id), str(project_id))

    @router.get("/analytics/calibration")
    async def get_calibration(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_calibration(str(project_id))

    @router.get("/usage")
    async def get_usage(project_id: UUID, _: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_usage_summary(str(project_id))

    @router.post("/usage", status_code=201)
    async def record_usage(req: UsageEventCreateRequest, _: AuthPrincipal = Depends(req_telemetry_writer)) -> dict[str, Any]:
        return service.record_usage(req)

    @router.get("/quotas")
    async def get_quotas(_: AuthPrincipal = Depends(req_read)) -> dict[str, Any]:
        return service.get_quotas()

    @router.post("/quotas", status_code=201)
    async def record_quota(req: QuotaSnapshotCreateRequest, _: AuthPrincipal = Depends(req_admin_or_runner)) -> dict[str, Any]:
        return service.record_quota(req)

    return router
