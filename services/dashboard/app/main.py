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

    # --- V2 Adaptive Brain & Planner Token Accounting Proxy Routes ---
    @app.get("/api/v2/brain/config")
    async def api_v2_brain_config(project_id: str) -> JSONResponse:
        resp = await _proxy_bridge("GET", "/v2/brain/config", params={"project_id": project_id})
        if resp.status_code == 404:
            return JSONResponse(
                status_code=200,
                content={
                    "available": False,
                    "project_id": project_id,
                    "approval_mode": "REVIEW_REQUIRED",
                    "daily_planner_budget_usd": None,
                    "allow_paid_api_fallback": False,
                    "roles": [
                        {
                            "role_name": "tier0_rules",
                            "preferred_label": "Quy tắc cục bộ (0 LLM)",
                            "effective_provider": "local",
                            "effective_model_id": "deterministic-rules",
                            "auth_mode": "unknown",
                            "availability_status": "verified",
                            "availability_reason": "Luôn sẵn sàng cục bộ, không tốn token",
                            "fallback_order": [],
                        },
                        {
                            "role_name": "tier1_light",
                            "preferred_label": "Gemini 4 Hydrogen",
                            "effective_provider": None,
                            "effective_model_id": None,
                            "auth_mode": "subscription_cli",
                            "availability_status": "unavailable",
                            "availability_reason": "Chưa xuất hiện trong agy models v1.1.27; gợi ý xác minh: gemini-3.8-flash-medium",
                            "fallback_order": ["gemini-3.8-flash-medium", "gemini-3.8-flash-low"],
                        },
                        {
                            "role_name": "tier2_primary",
                            "preferred_label": "Gemini 4 Neon Medium",
                            "effective_provider": None,
                            "effective_model_id": None,
                            "auth_mode": "subscription_cli",
                            "availability_status": "unavailable",
                            "availability_reason": "Chưa xuất hiện trong agy models v1.1.27; cần xác minh hoặc duyệt thay thế: claude-sonnet-5-5-medium",
                            "fallback_order": ["claude-sonnet-5-5-medium", "gemini-3.8-flash-high"],
                        },
                        {
                            "role_name": "tier3_advanced",
                            "preferred_label": "Gemini 4 Carbon / Claude Opus 5.5",
                            "effective_provider": "agy",
                            "effective_model_id": "claude-opus-5-5-medium",
                            "auth_mode": "subscription_cli",
                            "availability_status": "verified",
                            "availability_reason": "Carbon chưa có trên CLI; claude-opus-5-5-medium đã xác minh qua agy models",
                            "fallback_order": ["claude-opus-5-5-medium", "claude-opus-5-5-high"],
                        },
                        {
                            "role_name": "secondary_fallback",
                            "preferred_label": "Claude Sonnet 5.5 Medium (DeepSeek opt-in)",
                            "effective_provider": "agy",
                            "effective_model_id": "claude-sonnet-5-5-medium",
                            "auth_mode": "subscription_cli",
                            "availability_status": "verified",
                            "availability_reason": "Đã xác minh qua agy models; DeepSeek API tắt mặc định",
                            "fallback_order": ["claude-sonnet-5-5-medium"],
                        },
                    ],
                },
            )
        return resp

    @app.put("/api/v2/brain/config")
    async def api_v2_brain_config_update(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("PUT", "/v2/brain/config", use_admin=True, json_body=await request.json())

    @app.get("/api/v2/usage")
    async def api_v2_usage(project_id: str) -> JSONResponse:
        resp = await _proxy_bridge("GET", "/v2/usage", params={"project_id": project_id})
        if resp.status_code == 404:
            return JSONResponse(
                status_code=200,
                content={
                    "available": False,
                    "project_id": project_id,
                    "planner": {
                        "input_tokens": None,
                        "output_tokens": None,
                        "cache_read_tokens": None,
                        "cost_usd": None,
                        "measurement_kind": "unavailable",
                    },
                    "executor": {
                        "input_tokens": None,
                        "output_tokens": None,
                        "cache_read_tokens": None,
                        "cost_usd": None,
                        "measurement_kind": "unavailable",
                    },
                    "quotas": [],
                },
            )
        return resp

    @app.get("/api/v2/catalog")
    async def api_v2_catalog() -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/catalog")

    @app.get("/api/v2/evaluations")
    async def api_v2_evaluations(project_id: str | None = None) -> JSONResponse:
        params = {"project_id": project_id} if project_id else None
        return await _proxy_bridge("GET", "/v2/evaluations", params=params)

    @app.get("/api/v2/policies")
    async def api_v2_policies(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/policies", params={"project_id": project_id})

    @app.put("/api/v2/policies")
    async def api_v2_policies_update(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge("PUT", "/v2/policies", use_admin=True, json_body=await request.json())

    @app.get("/api/v2/plans")
    async def api_v2_plans(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/plans", params={"project_id": project_id})

    @app.get("/api/v2/plans/{plan_id}")
    async def api_v2_plan_detail(plan_id: str, project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", f"/v2/plans/{plan_id}", params={"project_id": project_id})

    @app.post("/api/v2/plans/{plan_id}/{action}")
    async def api_v2_plan_action(plan_id: str, action: str, project_id: str, request: Request) -> JSONResponse:
        if action not in {"approve", "reject", "pause", "resume", "cancel"}:
            raise HTTPException(status_code=404, detail={"error": {"code": "invalid_action", "message": "Invalid plan action"}})
        require_authenticated_csrf(request, cfg)
        body = await request.json() if await request.body() else {}
        return await _proxy_bridge(
            "POST",
            f"/v2/plans/{plan_id}/{action}",
            use_admin=True,
            params={"project_id": project_id},
            json_body=body,
        )

    @app.get("/api/v2/sessions")
    async def api_v2_sessions(project_id: str, complexity: str = "", outcome: str = "") -> JSONResponse:
        params: dict[str, Any] = {"project_id": project_id}
        if complexity:
            params["complexity"] = complexity
        if outcome:
            params["outcome"] = outcome
        return await _proxy_bridge("GET", "/v2/sessions", params=params)

    @app.delete("/api/v2/sessions/{session_id}")
    async def api_v2_session_delete(session_id: str, project_id: str, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_bridge(
            "DELETE",
            f"/v2/sessions/{session_id}",
            use_admin=True,
            params={"project_id": project_id},
        )

    @app.get("/api/v2/analytics/cohorts")
    async def api_v2_cohorts(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/analytics/cohorts", params={"project_id": project_id})

    @app.get("/api/v2/analytics/calibration")
    async def api_v2_calibration(project_id: str) -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/analytics/calibration", params={"project_id": project_id})

    @app.get("/api/v2/quotas")
    async def api_v2_quotas() -> JSONResponse:
        return await _proxy_bridge("GET", "/v2/quotas")

    return app


app = create_dashboard_app()
