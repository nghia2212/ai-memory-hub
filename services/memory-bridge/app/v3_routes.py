"""V3 FastAPI router for Dynamic MCP Registry/Routing and Conversation History/FTS5 Search."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v3_schema import (
    ConversationIngestRequest,
    ConversationPromoteRequest,
    McpApprovalRequest,
    McpRouteRequest,
    McpServerRegisterRequest,
)
from .v3_service import V3BridgeService


def create_v3_router(v3_service: V3BridgeService, cfg: Settings) -> APIRouter:
    router = APIRouter(prefix="/v3", tags=["v3 dynamic mcp & conversations"])

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

    # --- V3.0 Dynamic MCP ---
    @router.get("/mcp/servers")
    def list_mcp_servers(p: Principal = Depends(reader)):
        return v3_service.list_mcp_servers()

    @router.post("/mcp/servers", status_code=201)
    def register_mcp_server(req: McpServerRegisterRequest, p: Principal = Depends(admin)):
        return v3_service.register_mcp_server(req, actor=p.role)

    @router.post("/mcp/servers/{server_id}/approval")
    def approve_mcp_server(server_id: str, req: McpApprovalRequest, p: Principal = Depends(admin)):
        return v3_service.approve_mcp_server(server_id, req)

    @router.post("/mcp/route")
    def route_mcp(req: McpRouteRequest, p: Principal = Depends(writer)):
        return v3_service.route_mcp_capabilities(req, actor=p.role)

    @router.get("/mcp/audit")
    def list_mcp_audit(limit: int = Query(50, ge=1, le=200), p: Principal = Depends(reader)):
        return v3_service.list_mcp_audit(limit=limit)

    # --- V3.2 & V3.3 Conversations ---
    @router.post("/conversations/ingest", status_code=201)
    def ingest_conversation(req: ConversationIngestRequest, p: Principal = Depends(writer)):
        return v3_service.ingest_conversation(req)

    @router.get("/conversations")
    def list_conversations(
        project_id: UUID,
        worktree_id: UUID | None = None,
        client_type: str | None = None,
        model_id: str | None = None,
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        p: Principal = Depends(reader),
    ):
        return v3_service.list_conversations(
            project_id=str(project_id),
            worktree_id=str(worktree_id) if worktree_id else None,
            client_type=client_type,
            model_id=model_id,
            limit=limit,
            offset=offset,
        )

    @router.get("/conversations/search")
    def search_conversations(
        project_id: UUID,
        query: str = Query(min_length=1, max_length=200),
        worktree_id: UUID | None = None,
        client_type: str | None = None,
        model_id: str | None = None,
        limit: int = Query(10, ge=1, le=30),
        max_chars: int = Query(4000, ge=500, le=12000),
        p: Principal = Depends(reader),
    ):
        return v3_service.search_conversations(
            project_id=str(project_id),
            query=query,
            worktree_id=str(worktree_id) if worktree_id else None,
            client_type=client_type,
            model_id=model_id,
            limit=limit,
            max_chars=max_chars,
        )

    @router.get("/conversations/{conv_id}")
    def get_conversation(
        conv_id: UUID,
        project_id: UUID,
        limit: int = Query(100, ge=1, le=200),
        offset: int = Query(0, ge=0),
        p: Principal = Depends(reader),
    ):
        return v3_service.get_conversation_detail(str(conv_id), str(project_id), limit=limit, offset=offset)

    @router.get("/conversations/{conv_id}/export")
    def export_conversation(
        conv_id: UUID,
        project_id: UUID,
        fmt: str = Query("json", alias="format", pattern=r"^(json|md)$"),
        p: Principal = Depends(reader),
    ):
        detail = v3_service.get_conversation_detail(str(conv_id), str(project_id), limit=200, offset=0)
        if fmt == "md":
            conv = detail["conversation"]
            lines = [f"# {conv['title']}", f"- Client: `{conv['client_type']}` | Model: `{conv.get('model_id') or 'Unknown'}`", ""]
            for m in detail["messages"]:
                lines.append(f"## [{m['seq_no']}] {m['role'].upper()} ({m['created_at']})")
                lines.append(m["visible_text"])
                lines.append("")
            return Response(content="\n".join(lines), media_type="text/markdown; charset=utf-8")
        return detail

    @router.post("/conversations/{conv_id}/promote", status_code=201)
    def promote_excerpt(conv_id: UUID, req: ConversationPromoteRequest, p: Principal = Depends(admin)):
        return v3_service.promote_excerpt_to_memory(str(conv_id), req)

    @router.delete("/conversations/{conv_id}")
    def delete_conversation(conv_id: UUID, project_id: UUID, p: Principal = Depends(admin)):
        return v3_service.delete_conversation(str(conv_id), str(project_id))

    return router
