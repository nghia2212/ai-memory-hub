"""FastAPI + FastMCP ASGI entrypoint for Memory Bridge V1."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastmcp.utilities.lifespan import combine_lifespans

from .config import Settings, load_settings
from .db import DatabaseManager
from .errors import BridgeError, bridge_error_handler, error_payload, validation_error_handler
from .mcp_server import create_mcp_server
from .schemas import (
    CodeSearchRequest,
    EventCreateRequest,
    HandoffCreateRequest,
    HermesConfigUpdateRequest,
    IndexTriggerRequest,
    MemoryCreateRequest,
    MemorySearchRequest,
    MemoryVerifyRequest,
    ProjectCreateRequest,
    ProjectHermesUpdateRequest,
    RestoreRequest,
    WorkerResultRequest,
    WorktreeCreateRequest,
)
from .security import AuthPrincipal, authenticate_token, current_mcp_principal, extract_bearer_token, require_scope
from .service import MemoryBridgeService
from .v2_routes import create_v2_router
from .trace_routes import create_trace_router
from .v3_routes import create_v3_router
from .v3_service import V3BridgeService
from .v31_business_service import V31BusinessService
from .v31_mcp_gateway import V31McpGatewayService
from .v31_routes import create_v31_router
from .v32_routes import create_v32_router
from .v32_skill_service import V32SkillService
from .v321_routes import create_v321_router
from .v321_service import V321Mcp321Service
from .trace_operations import trace_parent, trace_transport
from .v2_service import V2BridgeService


def create_app(settings: Settings | None = None) -> FastAPI:
    cfg = settings or load_settings()
    db = DatabaseManager(db_path=cfg.db_path, backup_dir=cfg.backup_dir)
    service = MemoryBridgeService(db=db, settings=cfg)
    mcp = create_mcp_server(service)
    mcp_app = mcp.http_app(path="/")

    @asynccontextmanager
    async def bridge_lifespan(_app: FastAPI):
        db.initialize()

        async def sweep_leases():
            while True:
                await asyncio.sleep(15)
                try:
                    await asyncio.to_thread(db.reconcile_expired_leases)
                except Exception:
                    logging.getLogger(__name__).exception("Lease reconciliation failed")

        sweeper = asyncio.create_task(sweep_leases())
        try:
            yield
        finally:
            sweeper.cancel()
            with suppress(asyncio.CancelledError):
                await sweeper

    app = FastAPI(
        title="AI Memory Hub - Memory Bridge V1",
        version="1.0.0",
        lifespan=combine_lifespans(bridge_lifespan, mcp_app.lifespan),
    )
    app.state.settings = cfg
    app.state.db = db
    app.state.service = service

    app.add_exception_handler(BridgeError, bridge_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)

    @app.middleware("http")
    async def trace_context_middleware(request: Request, call_next):
        # Explicit caller correlation only; no guessing from active sessions.
        token = trace_parent.set(request.headers.get("X-Trace-Parent"))
        transport = trace_transport.set("mcp" if request.url.path == "/mcp" or request.url.path.startswith("/mcp/") else "http")
        try:
            return await call_next(request)
        finally:
            trace_transport.reset(transport)
            trace_parent.reset(token)

    @app.middleware("http")
    async def mcp_auth_middleware(request: Request, call_next):
        if request.url.path == "/mcp" or request.url.path.startswith("/mcp/"):
            token = extract_bearer_token(request.headers.get("Authorization"))
            try:
                principal = authenticate_token(token, cfg)
                require_scope(principal, "read")
            except BridgeError as exc:
                return JSONResponse(status_code=exc.status_code, content=error_payload(exc.code, exc.message))
            ctx_token = current_mcp_principal.set(principal)
            try:
                return await call_next(request)
            finally:
                current_mcp_principal.reset(ctx_token)
        return await call_next(request)

    def get_principal(request: Request) -> AuthPrincipal:
        token = extract_bearer_token(request.headers.get("Authorization"))
        return authenticate_token(token, cfg)

    def require_read(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(principal, "read")
        return principal

    def require_write(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(principal, "write")
        return principal

    def require_admin(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(principal, "admin")
        return principal

    def require_worker(principal: AuthPrincipal = Depends(get_principal)) -> AuthPrincipal:
        require_scope(principal, "worker")
        return principal

    # --- Health ---
    @app.get("/livez")
    async def livez() -> dict[str, Any]:
        return {"service": cfg.service_name, "alive": True}

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        ready, schema_version = db.check_health()
        status_code = 200 if ready else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "service": cfg.service_name,
                "ready": ready,
                "schema_version": schema_version,
            },
        )

    # --- Registry ---
    @app.get("/v1/projects")
    async def list_projects(_: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.list_projects()

    @app.post("/v1/projects", status_code=201)
    async def create_project(req: ProjectCreateRequest, _: AuthPrincipal = Depends(require_admin)) -> dict[str, Any]:
        return service.create_project(req)

    @app.patch("/v1/projects/{project_id}/hermes")
    async def update_project_hermes(
        project_id: UUID,
        req: ProjectHermesUpdateRequest,
        _: AuthPrincipal = Depends(require_admin),
    ) -> dict[str, Any]:
        return service.update_project_hermes(str(project_id), req.hermes_approved)

    @app.get("/v1/hermes/config")
    async def get_hermes_config(_: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.get_hermes_config()

    @app.put("/v1/hermes/config")
    async def update_hermes_config(
        req: HermesConfigUpdateRequest,
        _: AuthPrincipal = Depends(require_admin),
    ) -> dict[str, Any]:
        return service.update_hermes_config(
            enabled=req.enabled,
            provider=req.provider,
            allow_paid_deepseek=req.allow_paid_deepseek,
        )

    @app.get("/v1/projects/{project_id}/worktrees")
    async def list_worktrees(project_id: UUID, _: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.list_worktrees(str(project_id))

    @app.post("/v1/projects/{project_id}/worktrees", status_code=201)
    async def create_worktree(
        project_id: UUID,
        req: WorktreeCreateRequest,
        _: AuthPrincipal = Depends(require_admin),
    ) -> dict[str, Any]:
        return service.create_worktree(str(project_id), req)

    # --- Search & Index ---
    @app.post("/v1/search/memory")
    def search_memory(req: MemorySearchRequest, _: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.search_memory(req)

    @app.post("/v1/search/code")
    def search_code(req: CodeSearchRequest, _: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.search_code(req)

    @app.post("/v1/index", status_code=202)
    def trigger_index(req: IndexTriggerRequest, _: AuthPrincipal = Depends(require_admin)) -> dict[str, Any]:
        return service.trigger_index(req)

    @app.get("/v1/index/{job_id}")
    def get_index_job(
        job_id: UUID,
        project_id: UUID | None = Query(default=None),
        worktree_id: UUID | None = Query(default=None),
        _: AuthPrincipal = Depends(require_read),
    ) -> dict[str, Any]:
        return service.get_index_job(
            str(job_id),
            str(project_id) if project_id else None,
            str(worktree_id) if worktree_id else None,
        )

    # --- Memories & Verification ---
    @app.get("/v1/memories")
    async def list_memories(
        project_id: UUID,
        worktree_id: UUID | None = Query(default=None),
        state: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        _: AuthPrincipal = Depends(require_read),
    ) -> dict[str, Any]:
        return service.list_memories(
            str(project_id),
            worktree_id=str(worktree_id) if worktree_id else None,
            state=state,
            limit=limit,
        )

    @app.post("/v1/memories")
    async def create_memory(req: MemoryCreateRequest, _: AuthPrincipal = Depends(require_write)) -> JSONResponse:
        status_code, data = service.create_memory(req)
        return JSONResponse(status_code=status_code, content=data)

    @app.post("/v1/memories/{memory_id}/verify")
    async def verify_memory(
        memory_id: UUID,
        req: MemoryVerifyRequest,
        _: AuthPrincipal = Depends(require_admin),
    ) -> dict[str, Any]:
        return service.verify_memory(str(memory_id), req)

    @app.get("/v1/context")
    def project_context(project_id: UUID, worktree_id: UUID, _: AuthPrincipal = Depends(require_read)):
        return service.get_project_context(str(project_id), str(worktree_id))

    # --- Events & Handoffs ---
    @app.get("/v1/events")
    async def list_events(
        project_id: UUID,
        worktree_id: UUID | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        _: AuthPrincipal = Depends(require_read),
    ) -> dict[str, Any]:
        return service.list_events(
            str(project_id),
            worktree_id=str(worktree_id) if worktree_id else None,
            limit=limit,
        )

    @app.post("/v1/events")
    async def create_event(
        req: EventCreateRequest,
        principal: AuthPrincipal = Depends(require_write),
    ) -> JSONResponse:
        status_code, data = service.create_event(req, principal=principal)
        return JSONResponse(status_code=status_code, content=data)

    @app.post("/v1/events/{event_id}/approve")
    async def approve_event(
        event_id: UUID,
        _: AuthPrincipal = Depends(require_admin),
    ) -> dict[str, Any]:
        return service.approve_event(str(event_id))

    @app.post("/v1/handoffs")
    async def create_handoff(req: HandoffCreateRequest, _: AuthPrincipal = Depends(require_write)) -> JSONResponse:
        status_code, data = service.save_handoff(req)
        return JSONResponse(status_code=status_code, content=data)

    @app.get("/v1/handoffs/{task_id}")
    async def get_handoff(
        task_id: str,
        project_id: UUID,
        worktree_id: UUID,
        _: AuthPrincipal = Depends(require_read),
    ) -> dict[str, Any]:
        return service.get_handoff(task_id=task_id, project_id=str(project_id), worktree_id=str(worktree_id))

    @app.get("/v1/agents/status")
    async def get_agents_status(
        project_id: UUID,
        worktree_id: UUID | None = Query(default=None),
        _: AuthPrincipal = Depends(require_read),
    ) -> dict[str, Any]:
        return service.get_agents_status(
            str(project_id),
            worktree_id=str(worktree_id) if worktree_id else None,
        )

    # --- Worker (Hermes) contract ---
    @app.get("/v1/worker/events")
    async def worker_events(
        project_id: UUID | None = Query(default=None),
        limit: int = Query(default=10, ge=1, le=50),
        _: AuthPrincipal = Depends(require_worker),
    ) -> dict[str, Any]:
        return service.get_worker_events(str(project_id) if project_id else None, limit=limit)

    @app.post("/v1/worker/results")
    async def worker_results(req: WorkerResultRequest, _: AuthPrincipal = Depends(require_worker)) -> JSONResponse:
        status_code, data = service.submit_worker_result(req)
        return JSONResponse(status_code=status_code, content=data)

    # --- Backup & Restore ---
    @app.get("/v1/backups")
    async def list_backups(_: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return {"items": db.list_backups()}

    @app.post("/v1/backup", status_code=201)
    async def create_backup(_: AuthPrincipal = Depends(require_admin)) -> dict[str, Any]:
        return db.create_backup()

    @app.post("/v1/restore")
    async def restore_backup(req: RestoreRequest, _: AuthPrincipal = Depends(require_admin)) -> dict[str, Any]:
        return db.restore_backup(str(req.backup_id))

    # --- Metrics ---
    @app.get("/v1/metrics")
    async def get_metrics(_: AuthPrincipal = Depends(require_read)) -> dict[str, Any]:
        return service.get_metrics()

    # Mount V2 Orchestrator & Session Analytics router
    v2_service = V2BridgeService(db=db)
    app.state.v2_service = v2_service
    app.include_router(create_v2_router(v2_service, cfg))
    app.include_router(create_trace_router(db, cfg))
    v3_service = V3BridgeService(db)
    app.state.v3_service = v3_service
    app.include_router(create_v3_router(v3_service, cfg))
    v31_biz = V31BusinessService(db)
    v31_gw = V31McpGatewayService(db, v3_service, settings=cfg)
    app.state.v31_business_service = v31_biz
    app.state.v31_mcp_gateway = v31_gw
    app.include_router(create_v31_router(v31_biz, v31_gw, cfg))
    v32_skills = V32SkillService(db)
    app.state.v32_skill_service = v32_skills
    app.include_router(create_v32_router(v32_skills, cfg))
    try:
        from services.orchestrator.tool_packs import register_all_local_packs
        register_all_local_packs(v31_gw)
    except Exception:
        logging.getLogger(__name__).warning("Optional local tool packs unavailable in current runtime.")
    v321_svc = V321Mcp321Service(db, v31_gw)
    app.state.v321_service = v321_svc
    app.include_router(create_v321_router(v321_svc, cfg))

    # Mount FastMCP Streamable HTTP app at /mcp
    app.mount("/mcp", mcp_app)
    return app


app = create_app()
