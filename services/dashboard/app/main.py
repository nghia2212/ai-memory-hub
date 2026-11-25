"""FastAPI server-side proxy and Vietnamese Dashboard for AI Memory Hub V1."""
from __future__ import annotations

import hmac
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from .config import DashboardSettings, load_dashboard_settings
from .security import (
    SESSION_COOKIE_NAME,
    compute_csrf_token,
    create_session_token,
    require_authenticated_csrf,
    verify_origin,
    verify_session_token,
)
from .ui import DASHBOARD_HTML


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str


def create_dashboard_app(settings: DashboardSettings | None = None) -> FastAPI:
    cfg = settings or load_dashboard_settings()
    app = FastAPI(title="AI Memory Hub - Vietnamese Dashboard", version="1.0.0")
    app.state.settings = cfg

    async def _probe_health(url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url.rstrip('/')}/healthz")
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                return {"ready": r.status_code == 200, "status_code": r.status_code, **data}
        except Exception:
            return {"ready": False, "status": "unreachable"}

    async def _proxy_bridge(
        method: str,
        path: str,
        *,
        use_admin: bool = False,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> JSONResponse:
        token = cfg.bridge_admin_token if use_admin else (cfg.bridge_read_token or cfg.bridge_admin_token)
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        url = f"{cfg.bridge_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.request(method, url, headers=headers, json=json_body, params=params)
                content = resp.json() if resp.content else {}
                return JSONResponse(status_code=resp.status_code, content=content)
        except httpx.HTTPError:
            return JSONResponse(
                status_code=503,
                content={"error": {"code": "bridge_unavailable", "message": "Memory Bridge unavailable"}},
            )

    @app.get("/livez")
    async def livez() -> dict[str, Any]:
        return {"service": cfg.service_name, "alive": True}

    @app.get("/healthz")
    async def healthz() -> JSONResponse:
        bridge = await _probe_health(cfg.bridge_url)
        ready = bool(bridge.get("ready"))
        return JSONResponse(
            status_code=200 if ready else 503,
            content={"service": cfg.service_name, "ready": ready, "bridge": bridge},
        )

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(content=DASHBOARD_HTML)

    # --- Auth ---
    @app.get("/api/auth/session")
    async def auth_session(request: Request) -> dict[str, Any]:
        sid = verify_session_token(request.cookies.get(SESSION_COOKIE_NAME), cfg.session_secret)
        if not sid:
            return {"authenticated": False, "csrf_token": None}
        return {"authenticated": True, "csrf_token": compute_csrf_token(cfg.session_secret, sid)}

    @app.post("/api/auth/login")
    async def auth_login(req: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
        verify_origin(request, cfg)
        if not cfg.dashboard_password or not hmac.compare_digest(req.password, cfg.dashboard_password):
            raise HTTPException(
                status_code=401,
                detail={"error": {"code": "invalid_credentials", "message": "Invalid dashboard password"}},
            )
        cookie_token, csrf_token = create_session_token(cfg.session_secret)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=cookie_token,
            httponly=True,
            samesite="strict",
            secure=False,
            max_age=8 * 3600,
        )
        return {"authenticated": True, "csrf_token": csrf_token}

    @app.post("/api/auth/logout")
    async def auth_logout(request: Request, response: Response) -> dict[str, Any]:
        require_authenticated_csrf(request, cfg)
        response.delete_cookie(SESSION_COOKIE_NAME)
        return {"authenticated": False}

    # --- Read-only Proxy Routes ---
    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return {
            "bridge": await _probe_health(cfg.bridge_url),
            "codebase": await _probe_health(cfg.codebase_url),
            "hermes": await _probe_health(cfg.hermes_url),
        }

    @app.get("/api/projects")
    async def api_projects() -> JSONResponse:
        return await _proxy_bridge("GET", "/v1/projects")

    @app.get("/api/projects/{project_id}/worktrees")
    async def api_worktrees(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", f"/v1/projects/{project_id}/worktrees")

    @app.get("/api/memories")
    async def api_memories(project_id: str, state: str = "") -> JSONResponse:
        params: dict[str, Any] = {"project_id": project_id}
        if state:
            params["state"] = state
        return await _proxy_bridge("GET", "/v1/memories", params=params)

    @app.get("/api/events")
    async def api_events(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", "/v1/events", params={"project_id": project_id})

    @app.get("/api/agents/status")
    async def api_agents_status(project_id: str, worktree_id: str | None = None) -> JSONResponse:
        params: dict[str, Any] = {"project_id": project_id}
        if worktree_id:
            params["worktree_id"] = worktree_id
        return await _proxy_bridge("GET", "/v1/agents/status", params=params)

    @app.get("/api/handoffs/{task_id}")
    async def api_handoffs(task_id: str, project_id: str, worktree_id: str) -> JSONResponse:
        return await _proxy_bridge(
            "GET", f"/v1/handoffs/{task_id}", params={"project_id": project_id, "worktree_id": worktree_id}
        )

    @app.get("/api/backups")
    async def api_backups() -> JSONResponse:
        return await _proxy_bridge("GET", "/v1/backups")

    @app.get("/api/metrics")
    async def api_metrics() -> JSONResponse:
        return await _proxy_bridge("GET", "/v1/metrics")

    @app.post("/api/search/memory")
    async def api_search_memory(request: Request) -> JSONResponse:
        verify_origin(request, cfg)
        body = await request.json()
        return await _proxy_bridge("POST", "/v1/search/memory", json_body=body)

    @app.post("/api/search/code")
    async def api_search_code(request: Request) -> JSONResponse:
        verify_origin(request, cfg)
        body = await request.json()
        return await _proxy_bridge("POST", "/v1/search/code", json_body=body)

    # --- Authenticated + CSRF + Origin Mutating Proxy Routes ---
    @app.post("/api/projects")
    async def api_create_project(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("POST", "/v1/projects", use_admin=True, json_body=await request.json())

    @app.post("/api/projects/{project_id}/worktrees")
    async def api_create_worktree(project_id: str, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge(
            "POST", f"/v1/projects/{project_id}/worktrees", use_admin=True, json_body=await request.json()
        )

    @app.post("/api/memories/{memory_id}/verify")
    async def api_verify_memory(memory_id: str, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge(
            "POST", f"/v1/memories/{memory_id}/verify", use_admin=True, json_body=await request.json()
        )

    @app.post("/api/index")
    async def api_index(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("POST", "/v1/index", use_admin=True, json_body=await request.json())

    @app.post("/api/backup")
    async def api_backup(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("POST", "/v1/backup", use_admin=True)

    @app.post("/api/restore")
    async def api_restore(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("POST", "/v1/restore", use_admin=True, json_body=await request.json())

    return app


app = create_dashboard_app()
