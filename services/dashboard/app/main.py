"""FastAPI server-side proxy and Vietnamese Dashboard for AI Memory Hub V1."""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from uuid import UUID
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
    app = FastAPI(title="AI Memory Hub - Vietnamese Dashboard", version="2.1.0-dev")
    app.state.settings = cfg

    async def _probe_health(url: str) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url.rstrip('/')}/healthz")
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                ready = r.status_code == 200 and bool(data.get("ready", True))
                if ready:
                    return {
                        "ready": True,
                        "state": "active",
                        "label": "Đang hoạt động",
                        "status_code": r.status_code,
                        **data,
                    }
                return {
                    "ready": False,
                    "state": "unreachable",
                    "label": "Lỗi kết nối",
                    "status_code": r.status_code,
                    **data,
                }
        except Exception:
            return {
                "ready": False,
                "state": "unreachable",
                "label": "Lỗi kết nối",
                "status": "unreachable",
                "detail": "Không thể kết nối tới endpoint /healthz của dịch vụ.",
            }

    async def _fetch_hermes_runtime_config() -> dict[str, Any]:
        default_cfg = {
            "enabled": cfg.hermes_enabled,
            "provider": cfg.hermes_provider,
            "allow_paid_deepseek": False,
            "source": "env",
            "updated_at": None,
        }
        token = cfg.bridge_read_token or cfg.bridge_admin_token
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{cfg.bridge_url.rstrip('/')}/v1/hermes/config", headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("source") == "admin_override":
                        return data
                    # If no admin override exists yet, honor env HERMES_ENABLED / HERMES_PROVIDER
                    return {
                        **data,
                        "enabled": cfg.hermes_enabled,
                        "provider": cfg.hermes_provider,
                    }
        except Exception:
            pass
        return default_cfg

    async def _probe_hermes_status() -> dict[str, Any]:
        hcfg = await _fetch_hermes_runtime_config()
        enabled = bool(hcfg.get("enabled", False))
        provider = str(hcfg.get("provider") or cfg.hermes_provider or "deepseek")
        allow_paid = bool(hcfg.get("allow_paid_deepseek", False))
        deepseek_key_set = bool(cfg.deepseek_api_key_set)
        meta = {
            "enabled": enabled,
            "provider": provider,
            "deepseek_configured": deepseek_key_set,
            "allow_paid_deepseek": allow_paid,
            "config_source": hcfg.get("source", "env"),
        }
        if not enabled:
            return {
                "ready": False,
                "state": "disabled",
                "label": "Đã tắt",
                "status": "disabled",
                "detail": "Hermes Worker đang chủ động tắt (mặc định an toàn, không gọi API trả phí).",
                **meta,
            }
        if provider == "deepseek" and (not deepseek_key_set or not allow_paid):
            reason = (
                "Hermes đã bật nhưng chưa cấu hình DEEPSEEK_API_KEY (không tự kích hoạt DeepSeek API trả phí)."
                if not deepseek_key_set
                else "Hermes đã bật nhưng chưa cấp phép dùng DeepSeek API trả phí (allow_paid_deepseek=false)."
            )
            return {
                "ready": False,
                "state": "unconfigured",
                "label": "Chưa cấu hình",
                "status": "unconfigured",
                "detail": reason,
                **meta,
            }
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{cfg.hermes_url.rstrip('/')}/healthz")
                data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
                raw_status = str(data.get("status") or "")
                if raw_status == "disabled" or data.get("enabled") is False:
                    return {
                        "ready": False,
                        "state": "disabled",
                        "label": "Đã tắt",
                        "status": "disabled",
                        "status_code": r.status_code,
                        "detail": "Container Hermes đang chạy ở chế độ tắt (HERMES_ENABLED=false).",
                        **meta,
                    }
                if raw_status.startswith("missing_") or raw_status.startswith("invalid_"):
                    return {
                        "ready": False,
                        "state": "unconfigured",
                        "label": "Chưa cấu hình",
                        "status": raw_status,
                        "status_code": r.status_code,
                        "detail": f"Hermes thiếu cấu hình provider: {raw_status}.",
                        **meta,
                    }
                if r.status_code == 200 and data.get("ready"):
                    return {
                        "ready": True,
                        "state": "active",
                        "label": "Đang hoạt động",
                        "status": raw_status or "ok",
                        "status_code": r.status_code,
                        "detail": f"Hermes Worker đang hoạt động (provider={provider}).",
                        **meta,
                    }
                return {
                    "ready": False,
                    "state": "unreachable",
                    "label": "Lỗi kết nối",
                    "status": raw_status or "error",
                    "status_code": r.status_code,
                    "detail": f"Hermes trả về mã HTTP {r.status_code}.",
                    **meta,
                }
        except Exception:
            return {
                "ready": False,
                "state": "unreachable",
                "label": "Lỗi kết nối",
                "status": "unreachable",
                "detail": "Hermes đã bật nhưng container không phản hồi (chạy `docker compose --profile hermes up -d` nếu cần chạy worker).",
                **meta,
            }

    async def _proxy_bridge(
        method: str,
        path: str,
        *,
        use_admin: bool = False,
        planner: bool = False,
        json_body: Any = None,
        params: dict[str, Any] | None = None,
    ) -> JSONResponse:
        token = cfg.bridge_planner_token if planner else cfg.bridge_admin_token if use_admin else cfg.bridge_read_token
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

    @app.get("/workbench.js")
    def workbench_js():
        return Response(Path(__file__).with_name("workbench.js").read_bytes(), media_type="application/javascript")

    @app.get("/workbench.css")
    def workbench_css():
        return Response(Path(__file__).with_name("workbench.css").read_bytes(), media_type="text/css")

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

    async def _proxy_host_brain(
        method: str,
        path: str,
        *,
        json_body: Any = None,
        timeout: float = 95.0,
    ) -> JSONResponse:
        headers = {"Authorization": f"Bearer {cfg.bridge_runner_token}"} if cfg.bridge_runner_token else {}
        url = f"{cfg.host_brain_url.rstrip('/')}{path}"
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.request(method, url, headers=headers, json=json_body)
                content = resp.json() if resp.content else {}
                return JSONResponse(status_code=resp.status_code, content=content)
        except httpx.HTTPError:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "code": "host_brain_unavailable",
                        "message": "Host Brain Agent chưa chạy trên host (chạy scripts/start-host-brain-agent.ps1).",
                    }
                },
            )

    async def _probe_host_brain_status() -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{cfg.host_brain_url.rstrip('/')}/healthz")
                if r.status_code == 200:
                    return {
                        "ready": True,
                        "state": "active",
                        "label": "Đang hoạt động",
                        "detail": "Host Brain Agent (agy/claude/codex CLI) đang kết nối.",
                    }
        except Exception:
            pass
        return {
            "ready": False,
            "state": "disabled",
            "label": "Đã tắt",
            "detail": "Host Brain Agent chạy theo yêu cầu trên host (chạy scripts/start-host-brain-agent.ps1 để bật).",
        }

    # --- Read-only Proxy Routes ---
    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return {
            "bridge": await _probe_health(cfg.bridge_url),
            "codebase": await _probe_health(cfg.codebase_url),
            "hermes": await _probe_hermes_status(),
            "host_brain": await _probe_host_brain_status(),
        }

    @app.get("/api/hermes/config")
    async def api_hermes_config() -> dict[str, Any]:
        hcfg = await _fetch_hermes_runtime_config()
        status = await _probe_hermes_status()
        return {**hcfg, "deepseek_configured": cfg.deepseek_api_key_set, "runtime": status}

    @app.put("/api/hermes/config")
    async def api_hermes_config_update(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json()
        return await _proxy_bridge("PUT", "/v1/hermes/config", use_admin=True, json_body=body)

    @app.patch("/api/projects/{project_id}/hermes")
    async def api_project_hermes_update(project_id: str, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json()
        return await _proxy_bridge(
            "PATCH", f"/v1/projects/{project_id}/hermes", use_admin=True, json_body=body
        )

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

    @app.get("/api/explorer/{view}")
    async def explorer(view: str, request: Request):
        if view not in {"tree", "source", "index", "outline", "graph"}:
            raise HTTPException(404, "Unknown explorer view")
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                reply = await client.get(f"{cfg.codebase_url}/v1/explorer/{view}",
                    headers={"Authorization": f"Bearer {cfg.codebase_read_token}"}, params=request.query_params)
                return JSONResponse(reply.json(), status_code=reply.status_code)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(503, "Codebase Adapter unavailable")

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

    @app.post("/api/v2/catalog/sync")
    async def api_v2_catalog_sync(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        return await _proxy_host_brain("POST", "/host/discover", json_body={}, timeout=40.0)

    @app.post("/api/v2/brain/probe")
    async def api_v2_brain_probe(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json()
        # Enforce no automatic paid API usage
        body["allow_paid_api"] = bool(body.get("allow_paid_api", False))
        return await _proxy_host_brain("POST", "/host/probe-model", json_body=body, timeout=75.0)

    @app.post("/api/v2/plans/analyze")
    async def api_v2_plan_analyze(request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json()
        project_id = str(body.get("project_id") or "")
        worktree_id = str(body.get("worktree_id") or "")
        objective = str(body.get("objective") or "").strip()
        if not project_id or not worktree_id or not objective:
            raise HTTPException(
                status_code=422,
                detail={"error": {"code": "invalid_input", "message": "Cần project_id, worktree_id và objective"}},
            )

        # Heuristic complexity/task_type classification if not provided
        low_obj = objective.lower()
        task_type = body.get("task_type")
        if not task_type:
            if any(k in low_obj for k in ("kiến trúc", "architect", "thiết kế")):
                task_type = "architecture"
            elif any(k in low_obj for k in ("lỗi", "bug", "debug", "sửa")):
                task_type = "debugging"
            elif any(k in low_obj for k in ("test", "kiểm thử", "qa")):
                task_type = "testing"
            elif any(k in low_obj for k in ("ui", "giao diện", "dashboard")):
                task_type = "ui"
            elif any(k in low_obj for k in ("review", "rà soát", "đánh giá")):
                task_type = "review"
            else:
                task_type = "implementation"
        complexity = body.get("complexity")
        if not complexity:
            if len(objective) < 60:
                complexity = "S"
            elif len(objective) < 180:
                complexity = "M"
            else:
                complexity = "L"

        routing = await _proxy_bridge("POST", "/v2/routing/recommend", planner=True,
            json_body={"project_id": project_id, "task_type": task_type, "complexity": complexity,
                       "allowed_auth_modes": ["subscription_cli"]})
        if routing.status_code != 200:
            return routing
        chosen = json.loads(routing.body)
        provider = chosen["provider"]
        model_id = chosen["model_id"]
        effort = chosen.get("effort")
        auth_mode = chosen["auth_mode"]
        catalog = await _proxy_bridge("GET", "/v2/catalog")
        if catalog.status_code != 200:
            return catalog
        catalog_data = json.loads(catalog.body)
        if auth_mode != "subscription_cli" or catalog_data.get("is_stale"):
            raise HTTPException(409, "Verified subscription CLI required")
        scope = {"project_id": project_id, "worktree_id": worktree_id}
        terms = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", objective)
        code_term = str(body.get("symbol") or next((t for t in terms if any(c.isupper() for c in t[1:])), objective[:100]))[:180]
        memory_term = str(body.get("memory_query") or objective[:100])
        context, code, memory = await asyncio.gather(
            _proxy_bridge("GET", "/v1/context", params=scope),
            _proxy_bridge("POST", "/v1/search/code", json_body={**scope, "query": code_term, "limit": 5}),
            _proxy_bridge("POST", "/v1/search/memory", json_body={**scope, "query": memory_term[:180], "limit": 3}),
        )
        if context.status_code != 200:
            return context
        code_refs = json.loads(code.body).get("items", []) if code.status_code == 200 else []
        mem_refs = json.loads(memory.body).get("items", []) if memory.status_code == 200 else []
        if not mem_refs:
            mem_refs = json.loads(context.body).get("items", [])[:3]
        refs = [f"{r['path']}:{r['line_start']} {r['title']}" for r in code_refs]
        refs.extend(f"Memory {m['id']}: {m['title']}" for m in mem_refs)
        task_objective = objective + ("\nTham chiếu đã tra cứu:\n" + "\n".join(refs) if refs else "")
        task_spec = {
            "task_key": "T1",
            "title": objective[:120],
            "objective": task_objective[:4000],
            "dependencies": [],
            "owned_paths": [],
            "acceptance_checks": ["Báo cáo tóm tắt có bằng chứng kiểm tra thực tế"],
            "features": {
                "rubric_version": "v2.0",
                "task_type": task_type,
                "complexity": complexity,
                "file_count": len({r["path"] for r in code_refs}),
                "expected_edits": 0,
                "expected_tests": 1,
                "dependency_count": 0,
                "uncertainty_level": "low" if complexity in {"XS", "S"} else "medium",
            },
            "assigned_agent": provider,
            "assigned_provider": provider,
            "assigned_model_id": model_id,
            "assigned_effort": effort,
            "auth_mode": auth_mode,
            "max_retries": 1,
        }
        plan_payload = {
            "project_id": project_id,
            "worktree_id": worktree_id,
            "objective": objective,
            "tasks": [task_spec],
        }
        forecast = await _proxy_bridge("POST", "/v2/forecasts", planner=True,
                                       json_body={**scope, "tasks": [task_spec], "persist": True})
        if forecast.status_code != 200:
            return forecast
        plan_payload["forecast_id"] = json.loads(forecast.body)["forecast_id"]
        plan_payload["catalog_snapshot_id"] = catalog_data["snapshot_id"]
        created = await _proxy_bridge("POST", "/v2/plans", planner=True, json_body=plan_payload)
        return created

    @app.post("/api/v2/plans/{plan_id}/dispatch")
    async def api_v2_plan_dispatch(plan_id: str, project_id: str, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json() if await request.body() else {}
        return await _proxy_host_brain(
            "POST",
            "/host/execute-plan",
            json_body={
                "project_id": project_id,
                "worktree_id": body.get("worktree_id", ""),
                "plan_id": plan_id,
            },
            timeout=105.0,
        )

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

    @app.put("/api/v2/plans/{plan_id}/revisions")
    async def revise_workbench_plan(plan_id: UUID, request: Request) -> JSONResponse:
        require_authenticated_csrf(request, cfg)
        body = await request.json()
        if not isinstance(body, dict) or not body.get("expected_revision_id"):
            raise HTTPException(422, "Expected revision required")
        project_id = str(UUID(str(body.get("project_id"))))
        current = await _proxy_bridge("GET", f"/v2/plans/{plan_id}", params={"project_id": project_id})
        if current.status_code != 200:
            return current
        detail = json.loads(current.body)
        if detail["current_revision_id"] != body["expected_revision_id"]:
            raise HTTPException(409, "Revision changed; reload")
        tasks = body.get("tasks")
        if not isinstance(tasks, list) or not 1 <= len(tasks) <= 50:
            raise HTTPException(422, "Invalid tasks")
        catalog = await _proxy_bridge("GET", "/v2/catalog")
        if catalog.status_code != 200:
            return catalog
        cat = json.loads(catalog.body)
        allowed = {(x.get("provider"), x.get("model_id")) for x in cat.get("models", []) if x.get("status") == "verified" and x.get("auth_mode") == "subscription_cli"}
        if cat.get("is_stale") or any(not isinstance(t, dict) or t.get("auth_mode") != "subscription_cli" or (t.get("assigned_provider"), t.get("assigned_model_id")) not in allowed for t in tasks):
            raise HTTPException(409, "Only verified subscription CLI models are allowed")
        forecast = await _proxy_bridge("POST", "/v2/forecasts", planner=True, json_body={"project_id": project_id, "worktree_id": detail["worktree_id"], "tasks": tasks, "persist": True})
        if forecast.status_code != 200:
            return forecast
        return await _proxy_bridge("PUT", f"/v2/plans/{plan_id}/revisions", planner=True, json_body={"project_id": project_id, "tasks": tasks, "expected_revision_id": body["expected_revision_id"], "forecast_id": json.loads(forecast.body)["forecast_id"], "created_by": "dashboard"})

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

    @app.get("/api/v2/session-detail")
    async def api_v21_session_detail(session_id: UUID, project_id: UUID) -> JSONResponse:
        return await _proxy_bridge("GET", f"/v2/sessions/{session_id}", params={"project_id": str(project_id)})

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
