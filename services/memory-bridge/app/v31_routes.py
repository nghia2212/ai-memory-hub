"""V3.1 FastAPI router for Business Workspace Manager, Dynamic MCP Gateway, Policy Routing & Asset Registry."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v31_business_service import V31BusinessService
from .v31_mcp_gateway import V31McpGatewayService
from .v31_schema import (
    AssetRegisterRequest,
    BusinessCreateRequest,
    BusinessDuplicateRequest,
    BusinessFromTemplateRequest,
    BusinessImportRequest,
    BusinessMcpBindRequest,
    BusinessProjectLinkRequest,
    BusinessToolPolicyRequest,
    BusinessUpdateRequest,
    CustomTemplateCreateRequest,
    McpBusinessRouteRequest,
    McpGatewayServerCreateRequest,
    McpGatewayServerUpdateRequest,
    McpInvokeRequest,
)


def create_v31_router(
    biz_service: V31BusinessService,
    gateway_service: V31McpGatewayService,
    cfg: Settings,
) -> APIRouter:
    router = APIRouter(prefix="/v31", tags=["v3.1 business workspace & dynamic mcp gateway"])

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

    # --- P1 Business Workspace Manager ---
    @router.get("/businesses")
    def list_businesses(
        search: str = Query("", max_length=120),
        include_archived: bool = Query(False),
        enabled_only: bool = Query(False),
        p: Principal = Depends(reader),
    ):
        return biz_service.list_businesses(search=search, include_archived=include_archived, enabled_only=enabled_only)

    @router.post("/businesses", status_code=201)
    def create_business(req: BusinessCreateRequest, p: Principal = Depends(admin)):
        return biz_service.create_business(req, actor=p.role)

    @router.get("/businesses/templates")
    def list_templates(p: Principal = Depends(reader)):
        return biz_service.list_templates()

    @router.post("/businesses/templates", status_code=201)
    def create_template(req: CustomTemplateCreateRequest, p: Principal = Depends(admin)):
        return biz_service.create_custom_template(req)

    @router.delete("/businesses/templates/{template_key}")
    def delete_template(template_key: str, p: Principal = Depends(admin)):
        return biz_service.delete_custom_template(template_key)

    @router.post("/businesses/from-template", status_code=201)
    def create_from_template(req: BusinessFromTemplateRequest, p: Principal = Depends(admin)):
        return biz_service.create_from_template(req, actor=p.role)

    @router.post("/businesses/import", status_code=201)
    def import_business(req: BusinessImportRequest, p: Principal = Depends(admin)):
        return biz_service.import_business(req, actor=p.role)

    @router.get("/businesses/{business_id}")
    def get_business(business_id: str, p: Principal = Depends(reader)):
        return biz_service.get_business_detail(business_id)

    @router.put("/businesses/{business_id}")
    def update_business(business_id: str, req: BusinessUpdateRequest, p: Principal = Depends(admin)):
        return biz_service.update_business(business_id, req, actor=p.role)

    @router.post("/businesses/{business_id}/archive")
    def archive_business(business_id: str, p: Principal = Depends(admin)):
        return biz_service.archive_business(business_id, actor=p.role)

    @router.post("/businesses/{business_id}/restore")
    def restore_business(business_id: str, p: Principal = Depends(admin)):
        return biz_service.restore_business(business_id, actor=p.role)

    @router.post("/businesses/{business_id}/duplicate", status_code=201)
    def duplicate_business(business_id: str, req: BusinessDuplicateRequest, p: Principal = Depends(admin)):
        return biz_service.duplicate_business(business_id, req, actor=p.role)

    @router.get("/businesses/{business_id}/export")
    def export_business(business_id: str, p: Principal = Depends(reader)):
        return biz_service.export_business(business_id)

    @router.get("/businesses/{business_id}/versions")
    def list_business_versions(business_id: str, p: Principal = Depends(reader)):
        return biz_service.list_versions(business_id)

    @router.post("/businesses/{business_id}/versions/{revision}/restore")
    def restore_business_version(business_id: str, revision: int, p: Principal = Depends(admin)):
        return biz_service.restore_version(business_id, revision, actor=p.role)

    @router.post("/businesses/{business_id}/projects")
    def attach_project(business_id: str, req: BusinessProjectLinkRequest, p: Principal = Depends(admin)):
        return biz_service.attach_project(business_id, req.project_id)

    @router.delete("/businesses/{business_id}/projects/{project_id}")
    def detach_project(business_id: str, project_id: str, p: Principal = Depends(admin)):
        return biz_service.detach_project(business_id, project_id)

    @router.post("/businesses/{business_id}/mcps")
    def bind_mcp(business_id: str, req: BusinessMcpBindRequest, p: Principal = Depends(admin)):
        return biz_service.bind_mcp(business_id, req)

    @router.delete("/businesses/{business_id}/mcps/{server_id}")
    def unbind_mcp(business_id: str, server_id: str, p: Principal = Depends(admin)):
        return biz_service.unbind_mcp(business_id, server_id)

    @router.post("/businesses/{business_id}/tools/policy")
    def set_tool_policy(business_id: str, req: BusinessToolPolicyRequest, p: Principal = Depends(admin)):
        return biz_service.set_tool_policy(business_id, req)

    # --- P2 & P3 Dynamic MCP Gateway ---
    @router.get("/mcp/servers")
    def gateway_list_servers(include_archived: bool = Query(False), p: Principal = Depends(reader)):
        return gateway_service.list_servers(include_archived=include_archived)

    @router.post("/mcp/servers", status_code=201)
    def gateway_create_server(req: McpGatewayServerCreateRequest, p: Principal = Depends(admin)):
        return gateway_service.create_server(req, actor=p.role)

    @router.put("/mcp/servers/{server_id}")
    def gateway_update_server(server_id: str, req: McpGatewayServerUpdateRequest, p: Principal = Depends(admin)):
        return gateway_service.update_server(server_id, req, actor=p.role)

    @router.post("/mcp/servers/{server_id}/archive")
    def gateway_archive_server(server_id: str, p: Principal = Depends(admin)):
        return gateway_service.archive_or_restore_server(server_id, archived=True, actor=p.role)

    @router.post("/mcp/servers/{server_id}/restore")
    def gateway_restore_server(server_id: str, p: Principal = Depends(admin)):
        return gateway_service.archive_or_restore_server(server_id, archived=False, actor=p.role)

    @router.post("/mcp/servers/{server_id}/health")
    def gateway_health_check(server_id: str, p: Principal = Depends(writer)):
        return gateway_service.check_server_health(server_id, actor=p.role)

    @router.post("/mcp/servers/{server_id}/discover")
    def gateway_discover_tools(server_id: str, p: Principal = Depends(admin)):
        return gateway_service.discover_server_tools(server_id, actor=p.role)

    @router.post("/mcp/servers/{server_id}/stop")
    def gateway_stop_server(server_id: str, p: Principal = Depends(admin)):
        return gateway_service.stop_server_process(server_id)

    @router.post("/mcp/invoke")
    def gateway_invoke(req: McpInvokeRequest, p: Principal = Depends(writer)):
        return gateway_service.invoke_tool(req, actor=p.role)

    @router.post("/mcp/route")
    def gateway_route_for_business(req: McpBusinessRouteRequest, p: Principal = Depends(writer)):
        return gateway_service.route_for_business(req, actor=p.role)

    @router.get("/mcp/invocations")
    def gateway_list_invocations(
        business_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        actor: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        p: Principal = Depends(reader),
    ):
        return gateway_service.list_invocations(
            business_id=business_id,
            project_id=project_id,
            session_id=session_id,
            actor=actor,
            limit=limit,
        )

    @router.get("/mcp/monitoring")
    def gateway_monitoring(p: Principal = Depends(reader)):
        return gateway_service.get_monitoring_summary()

    # --- P6.5 Asset Registry ---
    @router.get("/assets")
    def list_assets(project_id: UUID, fmt: str | None = Query(None, alias="format"), p: Principal = Depends(reader)):
        return biz_service.list_assets(str(project_id), fmt=fmt)

    @router.post("/assets", status_code=201)
    def register_asset(req: AssetRegisterRequest, p: Principal = Depends(writer)):
        return biz_service.register_asset(req)

    return router
