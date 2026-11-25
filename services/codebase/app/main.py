"""FastAPI entrypoint for the Codebase Memory adapter (REST /v1 + MCP Streamable HTTP /mcp)."""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .auth import verify_bearer
from .cbm_client import CBMBackend, StdioCBMClient
from .config import Settings
from .models import (
    IndexJobResponse,
    IndexRequest,
    JsonRpcRequest,
    SearchCodeRequest,
    SearchCodeResponse,
)
from .registry import WorktreeRegistry
from .service import CodebaseService
from .store import AdapterStore

MCP_TOOL_SEARCH_CODE = {
    "name": "search_code",
    "description": "Search indexed code symbols and locations within an approved project and worktree.",
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["project_id", "worktree_id", "query"],
        "properties": {
            "project_id": {"type": "string", "format": "uuid"},
            "worktree_id": {"type": "string", "format": "uuid"},
            "query": {"type": "string", "minLength": 1, "maxLength": 512},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
            "max_chars": {"type": "integer", "minimum": 1, "maximum": 8000, "default": 4000},
        },
    },
}


def create_app(
    settings: Settings | None = None,
    cbm_backend: CBMBackend | None = None,
    registry: WorktreeRegistry | None = None,
) -> FastAPI:
    cfg = settings or Settings.from_env()
    store = AdapterStore(cfg.data_dir / "adapter-state.db")
    reg = registry or WorktreeRegistry(cfg)
    if cfg.registry_file and cfg.registry_file.is_file():
        reg.load_from_file(cfg.registry_file)
    cbm = cbm_backend or StdioCBMClient(
        command=cfg.cbm_bin,
        cache_dir=cfg.cbm_cache_dir,
        allowed_root=cfg.cbm_allowed_root,
    )
    service = CodebaseService(cfg, reg, store, cbm)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        cfg.data_dir.mkdir(parents=True, exist_ok=True)
        cfg.cbm_cache_dir.mkdir(parents=True, exist_ok=True)
        home_env = os.environ.get("HOME")
        if home_env:
            Path(home_env).mkdir(parents=True, exist_ok=True)
        store.init()
        if cbm.is_ready():
            try:
                await cbm.start()
            except Exception:
                pass
        yield
        await cbm.close()
        store.close()

    app = FastAPI(title="AI Memory Hub Codebase Adapter", version="1.0.0", lifespan=lifespan)
    app.state.settings = cfg
    app.state.store = store
    app.state.registry = reg
    app.state.cbm = cbm
    app.state.service = service

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict) and "code" in exc.detail and "message" in exc.detail:
            payload = {"error": exc.detail}
        else:
            payload = {"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}}
        return JSONResponse(status_code=exc.status_code, content=payload)

    @app.exception_handler(RequestValidationError)
    async def validation_exc_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": "Invalid request payload"}},
        )

    def require_read(request: Request):
        return verify_bearer(request, cfg, "read")

    def require_admin(request: Request):
        return verify_bearer(request, cfg, "admin")

    @app.get("/livez")
    async def livez() -> dict[str, Any]:
        return {"service": cfg.service_name, "status": "ok"}

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        data_ok = cfg.data_dir.is_dir() and os.access(cfg.data_dir, os.W_OK)
        cbm_ok = cbm.is_ready()
        ready = data_ok and cbm_ok
        status_code = 200 if ready else 503
        return JSONResponse(
            status_code=status_code,
            content={
                "service": cfg.service_name,
                "ready": ready,
                "status": "ok" if ready else "unavailable",
            },
        )

    @app.post("/v1/index", response_model=IndexJobResponse, response_model_exclude_none=True)
    async def post_index(req: IndexRequest, _auth=Depends(require_admin)) -> IndexJobResponse:
        return await service.trigger_index(req)

    @app.get("/v1/index/{job_id}", response_model=IndexJobResponse, response_model_exclude_none=True)
    async def get_index(
        job_id: str,
        project_id: UUID = Query(...),
        worktree_id: UUID = Query(...),
        _auth=Depends(require_read),
    ) -> IndexJobResponse:
        return await service.get_index_job(job_id, project_id, worktree_id)

    @app.post("/v1/search/code", response_model=SearchCodeResponse, response_model_exclude_none=True)
    async def post_search_code(req: SearchCodeRequest, _auth=Depends(require_read)) -> SearchCodeResponse:
        return await service.search_code(req)

    @app.get("/v1/explorer/{view}")
    async def explore(view: Literal["tree", "source", "index", "outline", "graph"],
                      project_id: UUID, worktree_id: UUID, path: str = Query("", max_length=512),
                      symbol: str = Query("", max_length=512), offset: int = Query(0, ge=0, le=10000),
                      line: int = Query(1, ge=1), depth: int = Query(1, ge=1, le=3), _auth=Depends(require_read)):
        return await service.explore(project_id, worktree_id, view, path, symbol, offset, line, depth)

    @app.post("/mcp")
    async def mcp_endpoint(rpc: JsonRpcRequest, _auth=Depends(require_read)) -> JSONResponse:
        if rpc.method == "initialize":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": rpc.id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "serverInfo": {"name": "ai-memory-hub-codebase", "version": "1.0.0"},
                        "capabilities": {"tools": {"listChanged": False}},
                    },
                }
            )
        if rpc.method == "notifications/initialized":
            return JSONResponse(status_code=202, content={"ok": True})
        if rpc.method == "tools/list":
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": rpc.id,
                    "result": {"tools": [MCP_TOOL_SEARCH_CODE]},
                }
            )
        if rpc.method == "tools/call":
            tool_name = rpc.params.get("name")
            if tool_name != "search_code":
                return JSONResponse(
                    content={
                        "jsonrpc": "2.0",
                        "id": rpc.id,
                        "error": {"code": -32601, "message": "Unknown tool"},
                    }
                )
            try:
                req = SearchCodeRequest.model_validate(rpc.params.get("arguments") or {})
            except ValidationError:
                return JSONResponse(
                    status_code=422,
                    content={"error": {"code": "validation_error", "message": "Invalid tool arguments"}},
                )
            res = await service.search_code(req)
            text = res.model_dump_json(exclude_none=True)
            return JSONResponse(
                content={
                    "jsonrpc": "2.0",
                    "id": rpc.id,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "structuredContent": json.loads(text),
                        "isError": False,
                    },
                }
            )
        return JSONResponse(
            content={
                "jsonrpc": "2.0",
                "id": rpc.id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )

    return app


app = create_app()
