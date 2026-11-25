"""V3.2.1 P16B FastAPI Router for Persistent Task Checkpoints & Auto Recovery."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v321_recovery_schema import (
    RecoveryActionRequest,
    RecoveryChangeAgentRequest,
    RecoveryPolicyUpdateRequest,
    RecoveryResumeRequest,
    RecoveryScanRequest,
    TaskCheckpointCreateRequest,
)
from .v321_recovery_service import V321RecoveryService


def create_v321_recovery_router(svc: V321RecoveryService, cfg: Settings) -> APIRouter:
    router = APIRouter(prefix="/v321", tags=["v3.2.1 p16b persistent task & recovery"])

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

    @router.get("/projects/{project_id}/recovery-policy")
    def get_recovery_policy(project_id: UUID, p: Principal = Depends(reader)):
        return svc.get_recovery_policy(str(project_id))

    @router.put("/projects/{project_id}/recovery-policy")
    def update_recovery_policy(project_id: UUID, req: RecoveryPolicyUpdateRequest, p: Principal = Depends(admin)):
        return svc.update_recovery_policy(str(project_id), req, actor=p.role)

    @router.post("/recovery/checkpoints", status_code=201)
    def create_checkpoint(req: TaskCheckpointCreateRequest, p: Principal = Depends(writer)):
        return svc.record_checkpoint(req)

    @router.get("/recovery/checkpoints")
    def list_checkpoints(
        entity_kind: str = Query(..., min_length=2, max_length=32),
        entity_id: str = Query(..., min_length=2, max_length=120),
        project_id: UUID = Query(...),
        p: Principal = Depends(reader),
    ):
        return svc.list_checkpoints(entity_kind, entity_id, str(project_id))

    @router.get("/recovery/queue")
    def list_recovery_queue(
        project_id: UUID | None = None,
        include_resolved: bool = Query(False),
        p: Principal = Depends(reader),
    ):
        return svc.list_recovery_queue(str(project_id) if project_id else None, include_resolved=include_resolved)

    @router.post("/recovery/scan")
    def scan_recovery(req: RecoveryScanRequest, p: Principal = Depends(admin)):
        return svc.reconcile_startup(
            project_id=str(req.project_id) if req.project_id else None,
            check_host_runner=req.check_host_runner,
            apply_auto_policy=req.apply_auto_policy,
        )

    @router.post("/recovery/{recovery_id}/resume")
    def resume_recovery(recovery_id: str, req: RecoveryResumeRequest, p: Principal = Depends(admin)):
        return svc.resume_item(recovery_id, req, actor=p.role)

    @router.post("/recovery/{recovery_id}/reconnect")
    def reconnect_recovery(recovery_id: str, req: RecoveryActionRequest, p: Principal = Depends(admin)):
        return svc.reconnect_item(recovery_id, req, actor=p.role)

    @router.post("/recovery/{recovery_id}/pause")
    def pause_recovery(recovery_id: str, req: RecoveryActionRequest, p: Principal = Depends(admin)):
        return svc.pause_item(recovery_id, req, actor=p.role)

    @router.post("/recovery/{recovery_id}/cancel")
    def cancel_recovery(recovery_id: str, req: RecoveryActionRequest, p: Principal = Depends(admin)):
        return svc.cancel_item(recovery_id, req, actor=p.role)

    @router.post("/recovery/{recovery_id}/change-agent")
    def change_recovery_agent(recovery_id: str, req: RecoveryChangeAgentRequest, p: Principal = Depends(admin)):
        return svc.change_agent(recovery_id, req, actor=p.role)

    @router.post("/recovery/{recovery_id}/review")
    def review_recovery(
        recovery_id: str,
        req: RecoveryActionRequest,
        deliver_cached_result: bool = Query(False),
        p: Principal = Depends(admin),
    ):
        return svc.review_item(recovery_id, req, deliver_cached_result=deliver_cached_result, actor=p.role)

    return router
