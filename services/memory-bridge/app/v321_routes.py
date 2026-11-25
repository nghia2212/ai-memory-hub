"""V3.2.1 (MCP321) FastAPI router for MCP Candidate Catalog & External Plan/Task Sync."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v321_schema import (
    CatalogRegisterRequest,
    ExternalPlanIngestRequest,
    ProjectMcpOverrideRequest,
    SessionToMonitoringPlanRequest,
    TraceAssociationRequest,
)
from .v321_service import V321Mcp321Service


def create_v321_router(svc: V321Mcp321Service, cfg: Settings) -> APIRouter:
    router = APIRouter(prefix="/v321", tags=["v3.2.1 mcp catalog & external plans"])

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

    # --- P1 MCP Candidate Catalog (separate from registered servers) ---
    @router.get("/catalog")
    def list_catalog(category: str | None = None, p: Principal = Depends(reader)):
        return svc.list_catalog(category=category)

    @router.post("/catalog/register")
    def register_from_catalog(req: CatalogRegisterRequest, p: Principal = Depends(admin)):
        return svc.register_from_catalog(req, actor=p.role)

    # --- P8 External Plans & Tasks (Monitor-Only) ---
    @router.get("/external-plans")
    def list_external_plans(
        project_id: UUID,
        source_client: str | None = None,
        include_deleted: bool = Query(False),
        p: Principal = Depends(reader),
    ):
        return svc.list_external_plans(str(project_id), source_client=source_client, include_deleted=include_deleted)

    @router.post("/external-plans/ingest", status_code=201)
    def ingest_external_plan(req: ExternalPlanIngestRequest, p: Principal = Depends(writer)):
        return svc.ingest_external_plan(req, actor=p.role)

    @router.post("/external-plans/from-session", status_code=201)
    def create_from_session(req: SessionToMonitoringPlanRequest, p: Principal = Depends(admin)):
        return svc.create_monitoring_plan_from_session(req, actor=p.role)

    @router.post("/external-plans/associate", status_code=201)
    def associate_trace(req: TraceAssociationRequest, p: Principal = Depends(admin)):
        return svc.associate_trace(req, actor=p.role)

    @router.delete("/external-plans/{plan_id}")
    def delete_external_plan(plan_id: str, project_id: UUID, p: Principal = Depends(admin)):
        return svc.delete_external_plan(plan_id, str(project_id))

    # --- Business Default Binding Sync & Project-Level MCP Overrides ---
    @router.post("/businesses/sync-default-bindings")
    def sync_default_bindings(p: Principal = Depends(admin)):
        if svc.biz_service is None:
            return {"synced": False, "reason": "business_service_not_attached"}
        return svc.biz_service.sync_default_business_bindings()

    @router.get("/projects/{project_id}/mcp-overrides")
    def list_project_mcp_overrides(project_id: UUID, business_id: str, p: Principal = Depends(reader)):
        return svc.list_project_mcp_overrides(str(project_id), business_id)

    @router.post("/projects/{project_id}/mcp-overrides")
    def set_project_mcp_override(project_id: UUID, req: ProjectMcpOverrideRequest, p: Principal = Depends(admin)):
        return svc.set_project_mcp_override(str(project_id), req)

    # --- P9: Client Runtime Adapters Inventory ---
    @router.get("/runtime-adapters")
    def list_runtime_adapters(p: Principal = Depends(reader)):
        return svc.get_runtime_adapters()

    return router
