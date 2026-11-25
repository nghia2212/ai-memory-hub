"""V3.1 P2 & P3 & P9 — Dynamic MCP Gateway: Real Discovery, Real Health Check, Real Invocation, Lifecycle & Business Routing."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .v31_schema import (
    McpBusinessRouteRequest,
    McpGatewayServerCreateRequest,
    McpGatewayServerUpdateRequest,
    McpInvokeRequest,
)
from .v3_service import V3BridgeService

_SHELL_INJECTION_RE = re.compile(r"(?:[;&|`$]|\b(?:rm\s+-rf|powershell\s+-enc|cmd\s+/c|sh\s+-c)\b)", re.IGNORECASE)
_IN_DOCKER = Path("/.dockerenv").exists()

# Local deterministic tool registry (populated by tool packs or tests)
LocalToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class _StdioMcpProcess:
    """Managed Hub-owned STDIO JSON-RPC 2.0 MCP server process."""

    def __init__(self, server_id: str, executable: str, args: list[str], cwd: str | None, env: dict[str, str]) -> None:
        self.server_id = server_id
        self.executable = executable
        self.args = args
        self.cwd = cwd or None
        self.env = env
        self.proc: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()
        self.last_used = time.monotonic()
        self._req_id = 0
        self.protocol_version = "2024-11-05"
        self.initialized = False

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        if self.is_alive():
            return
        self.proc = subprocess.Popen(
            [self.executable, *self.args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=self.cwd,
            env=self.env,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        self.initialized = False
        self.last_used = time.monotonic()

    def stop(self) -> None:
        if self.proc is not None:
            try:
                if self.proc.poll() is None:
                    self.proc.terminate()
                    try:
                        self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        self.proc.kill()
            finally:
                self.proc = None
                self.initialized = False

    def _send_and_recv(self, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        assert self.proc is not None and self.proc.stdin is not None and self.proc.stdout is not None
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        self.proc.stdin.write(line)
        self.proc.stdin.flush()

        result_box: list[str | None] = [None]
        err_box: list[Exception | None] = [None]

        def _reader() -> None:
            try:
                while True:
                    raw = self.proc.stdout.readline()  # type: ignore[union-attr]
                    if not raw:
                        result_box[0] = None
                        return
                    raw_s = raw.strip()
                    if not raw_s:
                        continue
                    try:
                        parsed = json.loads(raw_s)
                    except json.JSONDecodeError:
                        continue
                    if "id" in parsed and parsed.get("id") == payload.get("id"):
                        result_box[0] = raw_s
                        return
            except Exception as exc:
                err_box[0] = exc

        t = threading.Thread(target=_reader, daemon=True)
        t.start()
        t.join(timeout=timeout)
        if t.is_alive():
            raise TimeoutError(f"STDIO MCP timeout after {timeout}s")
        if err_box[0] is not None:
            raise err_box[0]
        if result_box[0] is None:
            raise RuntimeError("STDIO MCP process closed stdout unexpectedly (crash or exit)")
        return json.loads(result_box[0])

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        assert self.proc is not None and self.proc.stdin is not None
        msg: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def ensure_initialized(self, timeout: float = 10.0) -> dict[str, Any]:
        self.start()
        if self.initialized and self.is_alive():
            return {"protocolVersion": self.protocol_version}
        self._req_id += 1
        resp = self._send_and_recv(
            {
                "jsonrpc": "2.0",
                "id": self._req_id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "ai-memory-hub-gateway", "version": "3.1.0"},
                },
            },
            timeout=timeout,
        )
        if "error" in resp:
            raise RuntimeError(f"MCP initialize failed: {resp['error']}")
        res = resp.get("result") or {}
        self.protocol_version = str(res.get("protocolVersion") or "2024-11-05")
        self._notify("notifications/initialized")
        self.initialized = True
        self.last_used = time.monotonic()
        return res

    def list_tools(self, timeout: float = 15.0) -> list[dict[str, Any]]:
        with self.lock:
            self.ensure_initialized(timeout=timeout)
            tools: list[dict[str, Any]] = []
            cursor: str | None = None
            for _ in range(20):
                self._req_id += 1
                params: dict[str, Any] = {"cursor": cursor} if cursor else {}
                resp = self._send_and_recv(
                    {"jsonrpc": "2.0", "id": self._req_id, "method": "tools/list", "params": params},
                    timeout=timeout,
                )
                if "error" in resp:
                    raise RuntimeError(f"MCP tools/list failed: {resp['error']}")
                res = resp.get("result") or {}
                tools.extend(res.get("tools") or [])
                cursor = res.get("nextCursor")
                if not cursor:
                    break
            self.last_used = time.monotonic()
            return tools

    def call_tool(self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        with self.lock:
            self.ensure_initialized(timeout=min(timeout, 10.0))
            self._req_id += 1
            resp = self._send_and_recv(
                {
                    "jsonrpc": "2.0",
                    "id": self._req_id,
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": arguments},
                },
                timeout=timeout,
            )
            self.last_used = time.monotonic()
            if "error" in resp:
                raise RuntimeError(f"MCP tools/call error: {resp['error']}")
            return resp.get("result") or {}


def _parse_mcp_http_response(resp: httpx.Response) -> dict[str, Any]:
    ct = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" in ct:
        last_json: dict[str, Any] = {}
        for line in resp.text.splitlines():
            if line.startswith("data:"):
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    parsed = json.loads(chunk)
                    if isinstance(parsed, dict):
                        last_json = parsed
                except json.JSONDecodeError:
                    continue
        return last_json
    return resp.json() if resp.content else {}


def _http_mcp_handshake(endpoint: str, headers: dict[str, str], timeout: float) -> tuple[str | None, dict[str, Any]]:
    req_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **headers,
    }
    init_body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-memory-hub-gateway", "version": "3.1.0"},
        },
    }
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.post(endpoint, headers=req_headers, json=init_body)
        if r.status_code in (401, 403):
            raise PermissionError(f"HTTP {r.status_code} authentication failed")
        r.raise_for_status()
        session_id = r.headers.get("mcp-session-id")
        data = _parse_mcp_http_response(r)
        if "error" in data:
            raise RuntimeError(f"HTTP MCP initialize error: {data['error']}")
        notif_headers = {**req_headers}
        if session_id:
            notif_headers["Mcp-Session-Id"] = session_id
        try:
            client.post(endpoint, headers=notif_headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass
        return session_id, data.get("result") or {}


def _http_mcp_list_tools(endpoint: str, headers: dict[str, str], timeout: float) -> tuple[str, list[dict[str, Any]]]:
    session_id, init_res = _http_mcp_handshake(endpoint, headers, timeout)
    proto = str(init_res.get("protocolVersion") or "2024-11-05")
    req_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **headers,
    }
    if session_id:
        req_headers["Mcp-Session-Id"] = session_id
    tools: list[dict[str, Any]] = []
    cursor: str | None = None
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for idx in range(20):
            params: dict[str, Any] = {"cursor": cursor} if cursor else {}
            r = client.post(
                endpoint,
                headers=req_headers,
                json={"jsonrpc": "2.0", "id": 10 + idx, "method": "tools/list", "params": params},
            )
            r.raise_for_status()
            data = _parse_mcp_http_response(r)
            if "error" in data:
                raise RuntimeError(f"HTTP MCP tools/list error: {data['error']}")
            res = data.get("result") or {}
            tools.extend(res.get("tools") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
    return proto, tools


def _http_mcp_call_tool(endpoint: str, headers: dict[str, str], tool_name: str, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
    session_id, _ = _http_mcp_handshake(endpoint, headers, min(timeout, 10.0))
    req_headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
        **headers,
    }
    if session_id:
        req_headers["Mcp-Session-Id"] = session_id
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        r = client.post(
            endpoint,
            headers=req_headers,
            json={"jsonrpc": "2.0", "id": 100, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}},
        )
        if r.status_code in (401, 403):
            raise PermissionError(f"HTTP {r.status_code} authentication failed")
        r.raise_for_status()
        data = _parse_mcp_http_response(r)
        if "error" in data:
            raise RuntimeError(f"HTTP MCP tools/call error: {data['error']}")
        return data.get("result") or {}


def _schema_hash(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def _validate_arguments_against_schema(arguments: dict[str, Any], input_schema: dict[str, Any]) -> None:
    if not input_schema or not isinstance(input_schema, dict):
        return
    required = input_schema.get("required") or []
    for req_key in required:
        if req_key not in arguments or arguments[req_key] is None:
            raise BridgeError(422, "invalid_tool_arguments", f"Missing required argument '{req_key}' for tool invocation.")
    props = input_schema.get("properties") or {}
    type_map: dict[str, tuple[type, ...]] = {
        "string": (str,),
        "integer": (int,),
        "number": (int, float),
        "boolean": (bool,),
        "array": (list,),
        "object": (dict,),
    }
    for k, val in arguments.items():
        if k in props and val is not None:
            expected_t = props[k].get("type")
            if expected_t in type_map:
                if expected_t == "integer" and isinstance(val, bool):
                    raise BridgeError(422, "invalid_tool_arguments", f"Argument '{k}' must be integer, got bool.")
                if not isinstance(val, type_map[expected_t]):
                    raise BridgeError(422, "invalid_tool_arguments", f"Argument '{k}' expected type '{expected_t}'.")


class V31McpGatewayService:
    """Real Dynamic MCP Gateway reusing v3_mcp_servers/v3_mcp_tools/v3_mcp_audit + v31 extensions."""

    def __init__(self, db: DatabaseManager, v3_service: V3BridgeService, *, settings: Any = None, max_concurrent_global: int = 6) -> None:
        self.db = db
        self.v3_service = v3_service
        self.cfg = settings
        self._stdio_pool: dict[str, _StdioMcpProcess] = {}
        self._pool_lock = threading.Lock()
        self._global_sem = threading.BoundedSemaphore(max_concurrent_global)
        self._server_sems: dict[str, threading.BoundedSemaphore] = {}
        self._local_tools: dict[tuple[str, str], tuple[dict[str, Any], LocalToolHandler]] = {}

    def register_local_tool(self, server_name: str, tool_meta: dict[str, Any], handler: LocalToolHandler) -> None:
        """Register an in-process deterministic local tool adapter."""
        self._local_tools[(server_name, tool_meta["name"])] = (tool_meta, handler)

    def _get_server_sem(self, server_id: str, limit: int) -> threading.BoundedSemaphore:
        with self._pool_lock:
            if server_id not in self._server_sems:
                self._server_sems[server_id] = threading.BoundedSemaphore(max(1, limit))
            return self._server_sems[server_id]

    def _resolve_effective_http_target(self, srv: Any, secret_refs: list[str]) -> tuple[str, dict[str, str]]:
        """Resolve Docker-internal service URL and auth headers cleanly for built-in servers without hardcoding localhost."""
        ep = str(srv["endpoint_or_cmd"])
        headers = self._resolve_auth_headers(secret_refs)
        if srv["trust_tier"] == "builtin":
            if srv["name"] == "ai-memory-bridge":
                tok = (next(iter(self.cfg.read_tokens), None) if self.cfg and getattr(self.cfg, "read_tokens", None) else None) or os.environ.get("BRIDGE_READ_TOKEN")
                if tok and "Authorization" not in headers:
                    headers["Authorization"] = f"Bearer {tok}"
            elif srv["name"] == "ai-memory-codebase":
                if _IN_DOCKER and ("127.0.0.1:8081" in ep or "localhost:8081" in ep):
                    cb_base = ((self.cfg.codebase_url if self.cfg else None) or os.environ.get("CODEBASE_URL") or "http://codebase:8081").rstrip("/")
                    ep = f"{cb_base}/mcp"
                tok = (
                    (getattr(self.cfg, "codebase_read_token", None) if self.cfg else None)
                    or (getattr(self.cfg, "codebase_token", None) if self.cfg else None)
                    or os.environ.get("CODEBASE_READ_TOKEN")
                    or os.environ.get("CODEBASE_TOKEN")
                )
                if tok and "Authorization" not in headers:
                    headers["Authorization"] = f"Bearer {tok}"
        return ep, headers

    def _resolve_auth_headers(self, secret_refs: list[str]) -> dict[str, str]:
        headers: dict[str, str] = {}
        for ref in secret_refs:
            val = os.environ.get(ref)
            if val:
                headers["Authorization"] = f"Bearer {val}"
                break
        return headers

    def _build_stdio_env(self, env_allowlist: list[str], secret_refs: list[str]) -> dict[str, str]:
        safe_base = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "PYTHONIOENCODING"}
        allowed = safe_base | set(env_allowlist) | set(secret_refs)
        return {k: v for k, v in os.environ.items() if k in allowed}

    def _validate_server_config(self, transport: str, endpoint: str, executable: str, args: list[str]) -> str:
        if transport in ("streamable_http", "rest_api"):
            if not endpoint or not endpoint.startswith(("http://127.0.0.1:", "http://localhost:", "http://host.docker.internal:", "http://codebase:", "http://memory-bridge:", "https://")):
                raise BridgeError(422, "unsafe_mcp_endpoint", "HTTP/REST endpoint must use loopback, internal Docker service, or HTTPS.")
            if _SHELL_INJECTION_RE.search(endpoint):
                raise BridgeError(422, "unsafe_mcp_endpoint", "Unsafe characters in endpoint.")
            return endpoint
        if transport in ("stdio", "native_cli"):
            if not executable:
                raise BridgeError(422, "missing_executable", "Executable is required for stdio/native_cli transport.")
            if _SHELL_INJECTION_RE.search(executable):
                raise BridgeError(422, "command_injection_denied", "Shell metacharacters are forbidden in executable.")
            for a in args:
                if _SHELL_INJECTION_RE.search(a):
                    raise BridgeError(422, "command_injection_denied", f"Shell metacharacters are forbidden in argument: {a}")
            return executable
        return endpoint or f"local://{executable or 'deterministic'}"

    # --- Server CRUD (reusing v3_mcp_servers) ---
    def list_servers(self, *, include_archived: bool = False) -> dict[str, Any]:
        self.v3_service._ensure_seeded()
        with self.db.read_conn() as conn:
            rows = conn.execute(
                """
                SELECT s.*, ext.description, ext.adapter_transport, ext.executable, ext.args_json,
                       ext.working_dir, ext.env_allowlist_json, ext.secret_refs_json,
                       ext.capability_tags_json, ext.timeout_sec, ext.concurrency_limit,
                       ext.ram_limit_mb, ext.cpu_limit_pct, ext.activation_policy,
                       COALESCE(ext.enabled, 1) AS ext_enabled, COALESCE(ext.archived, 0) AS ext_archived,
                       ext.protocol_version, ext.last_error_code, ext.last_error_message,
                       ext.last_success_at, ext.process_pid, ext.process_status,
                       COALESCE(ext.invocation_count, 0) AS invocation_count,
                       COALESCE(ext.success_count, 0) AS success_count,
                       COALESCE(ext.failure_count, 0) AS failure_count,
                       COALESCE(ext.total_latency_ms, 0) AS total_latency_ms
                FROM v3_mcp_servers s
                LEFT JOIN v31_mcp_server_ext ext ON ext.server_id = s.id
                ORDER BY s.trust_tier, s.name
                """
            ).fetchall()
            items = []
            for r in rows:
                if not include_archived and r["ext_archived"]:
                    continue
                sid = r["id"]
                tools = conn.execute(
                    """
                    SELECT t.*, te.input_schema_json, COALESCE(te.enabled, 1) AS tool_enabled,
                           COALESCE(te.removed, 0) AS tool_removed, te.schema_changed_at
                    FROM v3_mcp_tools t
                    LEFT JOIN v31_mcp_tool_ext te ON te.tool_id = t.id
                    WHERE t.server_id = ?
                    ORDER BY t.tool_name
                    """,
                    (sid,),
                ).fetchall()
                biz_rows = conn.execute(
                    """
                    SELECT bp.id, bp.name, bm.enabled
                    FROM v31_business_mcp_bindings bm
                    JOIN v31_business_profiles bp ON bp.id = bm.business_id
                    WHERE bm.server_id = ? AND bp.archived = 0
                    """,
                    (sid,),
                ).fetchall()
                inv_cnt = int(r["invocation_count"] or 0)
                avg_lat = round(int(r["total_latency_ms"] or 0) / inv_cnt, 1) if inv_cnt else None
                items.append(
                    {
                        "id": sid,
                        "name": r["name"],
                        "description": r["description"] or "",
                        "transport": r["adapter_transport"] or r["transport"],
                        "endpoint_or_cmd": r["endpoint_or_cmd"],
                        "executable": r["executable"] or "",
                        "args": json.loads(r["args_json"] or "[]"),
                        "working_dir": r["working_dir"] or "",
                        "env_allowlist": json.loads(r["env_allowlist_json"] or "[]"),
                        "secret_refs": json.loads(r["secret_refs_json"] or "[]"),
                        "version": r["version"],
                        "capability_tags": json.loads(r["capability_tags_json"] or "[]"),
                        "trust_tier": r["trust_tier"],
                        "approval_state": r["approval_state"],
                        "allowed_project_ids": json.loads(r["allowed_project_ids_json"] or "[]"),
                        "health_status": r["health_status"],
                        "last_latency_ms": r["last_latency_ms"],
                        "last_checked_at": r["last_checked_at"],
                        "last_error_code": r["last_error_code"],
                        "last_error_message": r["last_error_message"],
                        "last_success_at": r["last_success_at"],
                        "protocol_version": r["protocol_version"] or "",
                        "timeout_sec": r["timeout_sec"] or 30,
                        "concurrency_limit": r["concurrency_limit"] or 4,
                        "ram_limit_mb": r["ram_limit_mb"] or 512,
                        "cpu_limit_pct": r["cpu_limit_pct"] or 50,
                        "activation_policy": r["activation_policy"] or "on_demand",
                        "enabled": bool(r["ext_enabled"]),
                        "archived": bool(r["ext_archived"]),
                        "process_status": r["process_status"] or "stopped",
                        "process_pid": r["process_pid"],
                        "metrics": {
                            "invocation_count": inv_cnt,
                            "success_count": int(r["success_count"] or 0),
                            "failure_count": int(r["failure_count"] or 0),
                            "avg_latency_ms": avg_lat,
                        },
                        "used_by_businesses": [{"id": b["id"], "name": b["name"], "enabled": bool(b["enabled"])} for b in biz_rows],
                        "tools": [
                            {
                                "id": t["id"],
                                "tool_name": t["tool_name"],
                                "description": t["description"],
                                "input_schema_hash": t["input_schema_hash"],
                                "input_schema": json.loads(t["input_schema_json"] or "{}"),
                                "capability_tags": json.loads(t["capability_tags_json"] or "[]"),
                                "side_effect_level": t["side_effect_level"],
                                "requires_approval": bool(t["requires_approval"]),
                                "enabled": bool(t["tool_enabled"]),
                                "removed": bool(t["tool_removed"]),
                                "schema_changed_at": t["schema_changed_at"],
                                "discovered_at": t["discovered_at"],
                            }
                            for t in tools
                        ],
                    }
                )
            return {"items": items, "total": len(items)}

    def create_server(self, req: McpGatewayServerCreateRequest, actor: str = "admin") -> dict[str, Any]:
        endpoint_or_cmd = self._validate_server_config(req.adapter_transport, req.endpoint, req.executable, req.args)
        base_transport = "stdio" if req.adapter_transport in ("stdio", "native_cli", "local_tool") else "streamable_http"
        now = utc_now_iso()
        sid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM v3_mcp_servers WHERE name = ?", (req.name.strip(),)).fetchone():
                raise BridgeError(409, "mcp_server_exists", f"MCP server '{req.name}' already exists.")
            conn.execute(
                """
                INSERT INTO v3_mcp_servers (
                    id, name, transport, endpoint_or_cmd, version, trust_tier, approval_state,
                    allowed_project_ids_json, health_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'pending_approval', ?, 'pending_approval', ?, ?)
                """,
                (sid, req.name.strip(), base_transport, endpoint_or_cmd, req.version, req.trust_tier, json.dumps(req.allowed_project_ids), now, now),
            )
            conn.execute(
                """
                INSERT INTO v31_mcp_server_ext (
                    server_id, description, adapter_transport, executable, args_json, working_dir,
                    env_allowlist_json, secret_refs_json, capability_tags_json, timeout_sec,
                    concurrency_limit, ram_limit_mb, cpu_limit_pct, activation_policy, enabled, archived, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?)
                """,
                (
                    sid, req.description, req.adapter_transport, req.executable, json.dumps(req.args),
                    req.working_dir, json.dumps(req.env_allowlist), json.dumps(req.secret_refs),
                    json.dumps(req.capability_tags), req.timeout_sec, req.concurrency_limit,
                    req.ram_limit_mb, req.cpu_limit_pct, req.activation_policy, now,
                ),
            )
            self.v3_service._audit_mcp(conn, action="gateway_register", status="pending_approval", actor=actor, server_id=sid, details={"name": req.name, "transport": req.adapter_transport})
        return {"id": sid, "name": req.name.strip(), "approval_state": "pending_approval", "health_status": "pending_approval"}

    def update_server(self, server_id: str, req: McpGatewayServerUpdateRequest, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        reapproval_needed = False
        with self.db.write_tx() as conn:
            srv = conn.execute("SELECT * FROM v3_mcp_servers WHERE id = ?", (server_id,)).fetchone()
            if not srv:
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            ext = conn.execute("SELECT * FROM v31_mcp_server_ext WHERE server_id = ?", (server_id,)).fetchone()
            transport = ext["adapter_transport"] if ext else srv["transport"]
            new_exec = req.executable if req.executable is not None else (ext["executable"] if ext else "")
            new_args = req.args if req.args is not None else json.loads(ext["args_json"] if ext else "[]")
            new_ep = req.endpoint if req.endpoint is not None else srv["endpoint_or_cmd"]
            if req.executable is not None or req.args is not None or req.endpoint is not None:
                new_ep_cmd = self._validate_server_config(transport, new_ep, new_exec, new_args)
                if srv["trust_tier"] != "builtin" and (req.executable is not None or req.args is not None):
                    reapproval_needed = True
                conn.execute("UPDATE v3_mcp_servers SET endpoint_or_cmd = ?, updated_at = ? WHERE id = ?", (new_ep_cmd, now, server_id))
            if req.version is not None:
                conn.execute("UPDATE v3_mcp_servers SET version = ?, updated_at = ? WHERE id = ?", (req.version, now, server_id))
            if req.allowed_project_ids is not None:
                conn.execute("UPDATE v3_mcp_servers SET allowed_project_ids_json = ?, updated_at = ? WHERE id = ?", (json.dumps(req.allowed_project_ids), now, server_id))
            if reapproval_needed:
                conn.execute("UPDATE v3_mcp_servers SET approval_state = 'pending_approval', health_status = 'pending_approval', updated_at = ? WHERE id = ?", (now, server_id))

            if not ext:
                conn.execute(
                    "INSERT INTO v31_mcp_server_ext (server_id, adapter_transport, updated_at) VALUES (?, ?, ?)",
                    (server_id, transport, now),
                )
            ext_updates: dict[str, Any] = {}
            if req.description is not None:
                ext_updates["description"] = req.description
            if req.executable is not None:
                ext_updates["executable"] = req.executable
            if req.args is not None:
                ext_updates["args_json"] = json.dumps(req.args)
            if req.working_dir is not None:
                ext_updates["working_dir"] = req.working_dir
            if req.env_allowlist is not None:
                ext_updates["env_allowlist_json"] = json.dumps(req.env_allowlist)
            if req.secret_refs is not None:
                ext_updates["secret_refs_json"] = json.dumps(req.secret_refs)
            if req.capability_tags is not None:
                ext_updates["capability_tags_json"] = json.dumps(req.capability_tags)
            if req.timeout_sec is not None:
                ext_updates["timeout_sec"] = req.timeout_sec
            if req.concurrency_limit is not None:
                ext_updates["concurrency_limit"] = req.concurrency_limit
            if req.ram_limit_mb is not None:
                ext_updates["ram_limit_mb"] = req.ram_limit_mb
            if req.cpu_limit_pct is not None:
                ext_updates["cpu_limit_pct"] = req.cpu_limit_pct
            if req.activation_policy is not None:
                ext_updates["activation_policy"] = req.activation_policy
            if req.enabled is not None:
                ext_updates["enabled"] = 1 if req.enabled else 0
                if not req.enabled:
                    conn.execute("UPDATE v3_mcp_servers SET health_status = 'disabled', updated_at = ? WHERE id = ?", (now, server_id))
            if ext_updates:
                clause = ", ".join(f"{k} = ?" for k in ext_updates)
                conn.execute(f"UPDATE v31_mcp_server_ext SET {clause}, updated_at = ? WHERE server_id = ?", [*ext_updates.values(), now, server_id])
            self.v3_service._audit_mcp(conn, action="gateway_update", status="reapproval_required" if reapproval_needed else "updated", actor=actor, server_id=server_id)
        return {"id": server_id, "updated": True, "reapproval_required": reapproval_needed}

    def archive_or_restore_server(self, server_id: str, *, archived: bool, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        if archived:
            self.stop_server_process(server_id)
        with self.db.write_tx() as conn:
            srv = conn.execute("SELECT trust_tier FROM v3_mcp_servers WHERE id = ?", (server_id,)).fetchone()
            if not srv:
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            if archived and srv["trust_tier"] == "builtin":
                raise BridgeError(403, "builtin_protected", "Built-in Hub MCP servers cannot be archived.")
            conn.execute(
                """
                INSERT INTO v31_mcp_server_ext (server_id, archived, enabled, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET archived = excluded.archived, enabled = excluded.enabled, updated_at = excluded.updated_at
                """,
                (server_id, 1 if archived else 0, 0 if archived else 1, now),
            )
            conn.execute(
                "UPDATE v3_mcp_servers SET health_status = ?, updated_at = ? WHERE id = ?",
                ("disabled" if archived else "unknown", now, server_id),
            )
            self.v3_service._audit_mcp(conn, action="archive" if archived else "restore", status="ok", actor=actor, server_id=server_id)
        return {"id": server_id, "archived": archived}

    # --- Lifecycle ---
    def _get_or_create_stdio(self, server_id: str, executable: str, args: list[str], cwd: str, env: dict[str, str]) -> _StdioMcpProcess:
        with self._pool_lock:
            proc = self._stdio_pool.get(server_id)
            if proc is None or not proc.is_alive():
                proc = _StdioMcpProcess(server_id, executable, args, cwd, env)
                self._stdio_pool[server_id] = proc
            return proc

    def _host_bridge_config(self) -> tuple[str, str] | None:
        url = (os.environ.get("HOST_BRAIN_URL") or "").strip().rstrip("/")
        tok = (os.environ.get("BRIDGE_RUNNER_TOKEN") or "").strip()
        if not tok and self.cfg and getattr(self.cfg, "runner_tokens", None):
            tok = next(iter(self.cfg.runner_tokens), "")
        if url and tok:
            return url, tok
        return None

    def _should_use_host_stdio_bridge(self, exe: str) -> bool:
        """Delegate STDIO MCP to Windows Host Runner when running in Docker without the binary locally."""
        if not _IN_DOCKER:
            return False
        if self._which(exe):
            return False
        return self._host_bridge_config() is not None

    def _call_host_stdio_bridge(self, action: str, payload: dict[str, Any], timeout: float = 35.0) -> dict[str, Any]:
        cfg_pair = self._host_bridge_config()
        if not cfg_pair:
            raise RuntimeError("Host Brain Agent bridge is not configured (HOST_BRAIN_URL / BRIDGE_RUNNER_TOKEN).")
        base_url, tok = cfg_pair
        with httpx.Client(timeout=timeout) as client:
            r = client.post(
                f"{base_url}/host/mcp/{action}",
                headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"},
                json=payload,
            )
            data = r.json() if r.content else {}
            if r.status_code == 404 and (data.get("error") or {}).get("code") == "not_installed_on_host":
                raise FileNotFoundError((data.get("error") or {}).get("message") or "Executable not installed on host")
            if r.status_code == 504:
                raise TimeoutError((data.get("error") or {}).get("message") or "Host MCP timeout")
            if r.status_code >= 400:
                msg = (data.get("error") or {}).get("message") or f"Host MCP bridge HTTP {r.status_code}"
                raise RuntimeError(msg)
            return data

    def stop_server_process(self, server_id: str) -> dict[str, Any]:
        """Gracefully stop ONLY Hub-owned STDIO process (local or via Host Runner bridge)."""
        with self._pool_lock:
            proc = self._stdio_pool.pop(server_id, None)
        if proc is not None:
            proc.stop()
        if _IN_DOCKER and self._host_bridge_config() is not None:
            try:
                self._call_host_stdio_bridge("stop", {"server_id": server_id}, timeout=8.0)
            except Exception:
                pass
        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE v31_mcp_server_ext SET process_pid = NULL, process_status = 'stopped', updated_at = ? WHERE server_id = ?",
                (utc_now_iso(), server_id),
            )
        return {"server_id": server_id, "process_status": "stopped"}

    def reap_idle_processes(self, idle_sec: int = 300) -> list[str]:
        now_m = time.monotonic()
        stopped: list[str] = []
        with self._pool_lock:
            idle_ids = [sid for sid, p in self._stdio_pool.items() if now_m - p.last_used > idle_sec]
        for sid in idle_ids:
            self.stop_server_process(sid)
            stopped.append(sid)
        return stopped

    # --- P2.5 Real Health Check ---
    def check_server_health(self, server_id: str, actor: str = "system") -> dict[str, Any]:
        self.v3_service._ensure_seeded()
        now = utc_now_iso()
        with self.db.read_conn() as conn:
            srv = conn.execute("SELECT * FROM v3_mcp_servers WHERE id = ?", (server_id,)).fetchone()
            if not srv:
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            ext = conn.execute("SELECT * FROM v31_mcp_server_ext WHERE server_id = ?", (server_id,)).fetchone()

        transport = ext["adapter_transport"] if ext else srv["transport"]
        enabled = bool(ext["enabled"]) if ext else True
        archived = bool(ext["archived"]) if ext else False
        timeout = float(ext["timeout_sec"] if ext else 10)
        secret_refs = json.loads(ext["secret_refs_json"] if ext else "[]")

        status = "unknown"
        err_code: str | None = None
        err_msg: str | None = None
        latency_ms: int | None = None
        proc_pid: int | None = None
        proc_status = "stopped"

        if archived or not enabled:
            status = "disabled"
        elif srv["approval_state"] == "revoked":
            status = "revoked"
        elif srv["approval_state"] == "pending_approval":
            status = "pending_approval"
        else:
            t0 = time.monotonic()
            try:
                if transport == "local_tool":
                    has_tools = any(sname == srv["name"] for sname, _ in self._local_tools)
                    status = "ready" if has_tools else "not_configured"
                    if not has_tools:
                        err_code = "no_local_handlers"
                        err_msg = "No local deterministic tool handlers registered for this server."
                elif transport == "native_cli":
                    exe = ext["executable"] if ext else srv["endpoint_or_cmd"]
                    if not exe or (not Path(exe).exists() and not self._which(exe)):
                        status = "not_configured"
                        err_code = "executable_not_found"
                        err_msg = f"Executable '{exe}' not found on host."
                    else:
                        status = "ready"
                elif transport == "stdio":
                    exe = ext["executable"] if ext else srv["endpoint_or_cmd"]
                    args = json.loads(ext["args_json"] if ext else "[]")
                    env_allow = json.loads(ext["env_allowlist_json"] if ext else "[]")
                    cwd = ext["working_dir"] if ext else ""
                    if not exe:
                        status = "not_configured"
                        err_code = "missing_executable"
                    elif self._should_use_host_stdio_bridge(exe):
                        try:
                            hres = self._call_host_stdio_bridge(
                                "health",
                                {
                                    "server_id": server_id,
                                    "executable": exe,
                                    "args": args,
                                    "working_dir": cwd,
                                    "env_allowlist": env_allow,
                                    "secret_refs": secret_refs,
                                    "timeout_sec": max(timeout, 25.0),
                                },
                                timeout=max(timeout, 30.0) + 5.0,
                            )
                            status = hres.get("health_status") or "ready"
                            proc_pid = hres.get("process_pid")
                            proc_status = hres.get("process_status") or "running"
                        except FileNotFoundError as exc:
                            status = "not_installed"
                            err_code = "not_installed_on_host"
                            err_msg = str(exc)
                    else:
                        proc = self._get_or_create_stdio(server_id, exe, args, cwd, self._build_stdio_env(env_allow, secret_refs))
                        proc.ensure_initialized(timeout=min(timeout, 10.0))
                        status = "ready"
                        proc_pid = proc.proc.pid if proc.proc else None
                        proc_status = "running"
                elif transport in ("streamable_http", "rest_api"):
                    ep, headers = self._resolve_effective_http_target(srv, secret_refs)
                    if _IN_DOCKER and ("127.0.0.1" in ep or "localhost" in ep) and srv["trust_tier"] != "builtin":
                        err_msg = "Warning: localhost inside Docker container does not reach Windows host; use host.docker.internal."
                    if transport == "streamable_http":
                        _http_mcp_handshake(ep, headers, min(timeout, 8.0))
                        status = "ready"
                    else:
                        with httpx.Client(timeout=min(timeout, 8.0)) as client:
                            r = client.get(ep, headers=headers)
                            if r.status_code in (401, 403):
                                status = "auth_failed"
                                err_code = f"http_{r.status_code}"
                            elif r.status_code < 500:
                                status = "ready"
                            else:
                                status = "degraded"
                                err_code = f"http_{r.status_code}"
            except PermissionError as exc:
                status = "auth_failed"
                err_code = "auth_failed"
                err_msg = str(exc)
            except (TimeoutError, httpx.TimeoutException) as exc:
                status = "timeout"
                err_code = "timeout"
                err_msg = str(exc)
            except Exception as exc:
                status = "offline"
                err_code = "connection_failed"
                err_msg = str(exc)[:400]
            latency_ms = int((time.monotonic() - t0) * 1000)

        with self.db.write_tx() as conn:
            conn.execute(
                "UPDATE v3_mcp_servers SET health_status = ?, last_latency_ms = ?, last_checked_at = ?, updated_at = ? WHERE id = ?",
                (status, latency_ms, now, now, server_id),
            )
            conn.execute(
                """
                INSERT INTO v31_mcp_server_ext (server_id, adapter_transport, last_error_code, last_error_message, process_pid, process_status, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET
                    last_error_code = excluded.last_error_code,
                    last_error_message = excluded.last_error_message,
                    process_pid = excluded.process_pid,
                    process_status = excluded.process_status,
                    updated_at = excluded.updated_at
                """,
                (server_id, transport, err_code, err_msg, proc_pid, proc_status, now),
            )
            self.v3_service._audit_mcp(conn, action="health_check", status=status, actor=actor, server_id=server_id, latency_ms=latency_ms, error_code=err_code)
        return {
            "server_id": server_id,
            "name": srv["name"],
            "health_status": status,
            "latency_ms": latency_ms,
            "checked_at": now,
            "error_code": err_code,
            "error_message": err_msg,
            "process_status": proc_status,
            "process_pid": proc_pid,
        }

    @staticmethod
    def _which(exe: str) -> str | None:
        import shutil
        return shutil.which(exe)

    # --- P2.4 Real MCP Discovery ---
    def discover_server_tools(self, server_id: str, actor: str = "admin") -> dict[str, Any]:
        self.v3_service._ensure_seeded()
        now = utc_now_iso()
        with self.db.read_conn() as conn:
            srv = conn.execute("SELECT * FROM v3_mcp_servers WHERE id = ?", (server_id,)).fetchone()
            if not srv:
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            ext = conn.execute("SELECT * FROM v31_mcp_server_ext WHERE server_id = ?", (server_id,)).fetchone()

        transport = ext["adapter_transport"] if ext else srv["transport"]
        timeout = float(ext["timeout_sec"] if ext else 15)
        secret_refs = json.loads(ext["secret_refs_json"] if ext else "[]")
        proto_ver = ""
        discovered: list[dict[str, Any]] = []

        if transport == "local_tool":
            proto_ver = "local-v3.1"
            for (sname, _), (meta, _) in self._local_tools.items():
                if sname == srv["name"]:
                    discovered.append(meta)
        elif transport == "stdio":
            exe = ext["executable"] if ext else srv["endpoint_or_cmd"]
            args = json.loads(ext["args_json"] if ext else "[]")
            env_allow = json.loads(ext["env_allowlist_json"] if ext else "[]")
            cwd = ext["working_dir"] if ext else ""
            if self._should_use_host_stdio_bridge(exe):
                try:
                    dres = self._call_host_stdio_bridge(
                        "discover",
                        {
                            "server_id": server_id,
                            "executable": exe,
                            "args": args,
                            "working_dir": cwd,
                            "env_allowlist": env_allow,
                            "secret_refs": secret_refs,
                            "timeout_sec": max(timeout, 30.0),
                        },
                        timeout=max(timeout, 35.0) + 5.0,
                    )
                    discovered = dres.get("tools") or []
                    proto_ver = str(dres.get("protocol_version") or "2024-11-05")
                except Exception as exc:
                    raise BridgeError(502, "host_mcp_discovery_failed", f"Host STDIO MCP discovery failed: {exc}") from exc
            else:
                proc = self._get_or_create_stdio(server_id, exe, args, cwd, self._build_stdio_env(env_allow, secret_refs))
                discovered = proc.list_tools(timeout=timeout)
                proto_ver = proc.protocol_version
        elif transport == "streamable_http":
            ep, headers = self._resolve_effective_http_target(srv, secret_refs)
            try:
                proto_ver, discovered = _http_mcp_list_tools(ep, headers, timeout)
            except Exception as exc:
                raise BridgeError(502, "mcp_discovery_failed", f"HTTP MCP discovery failed: {exc}") from exc
        else:
            raise BridgeError(422, "discovery_unsupported", f"Transport '{transport}' does not support live MCP tools/list discovery.")

        added: list[str] = []
        changed: list[str] = []
        removed: list[str] = []
        seen_names: set[str] = set()
        reapproval_triggered = False

        with self.db.write_tx() as conn:
            existing_rows = {
                r["tool_name"]: r
                for r in conn.execute("SELECT * FROM v3_mcp_tools WHERE server_id = ?", (server_id,)).fetchall()
            }
            for item in discovered:
                tname = str(item.get("name") or "").strip()
                if not tname:
                    continue
                seen_names.add(tname)
                desc = str(item.get("description") or "")[:500]
                in_schema = item.get("inputSchema") or item.get("input_schema") or {}
                out_schema = item.get("outputSchema") or {}
                shash = _schema_hash(in_schema)
                tags = item.get("capability_tags") or json.loads(ext["capability_tags_json"] if ext else "[]")
                side_eff = item.get("side_effect_level") if item.get("side_effect_level") in ("read_only", "write_scoped", "restricted") else "write_scoped"
                # Never trust third-party readOnlyHint blindly as security proof
                if srv["trust_tier"] != "builtin" and side_eff == "read_only" and transport in ("stdio", "streamable_http"):
                    side_eff = "write_scoped"

                if tname not in existing_rows:
                    tid = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO v3_mcp_tools (
                            id, server_id, tool_name, description, input_schema_hash,
                            capability_tags_json, side_effect_level, requires_approval, discovered_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (tid, server_id, tname, desc, shash, json.dumps(tags), side_eff, 1 if side_eff == "restricted" else 0, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO v31_mcp_tool_ext (tool_id, input_schema_json, output_schema_json, enabled, removed, updated_at)
                        VALUES (?, ?, ?, 1, 0, ?)
                        """,
                        (tid, json.dumps(in_schema, ensure_ascii=False), json.dumps(out_schema, ensure_ascii=False), now),
                    )
                    added.append(tname)
                else:
                    ex = existing_rows[tname]
                    tid = ex["id"]
                    old_hash = ex["input_schema_hash"]
                    schema_changed = bool(old_hash and old_hash != shash)
                    conn.execute(
                        "UPDATE v3_mcp_tools SET description = ?, input_schema_hash = ?, discovered_at = ? WHERE id = ?",
                        (desc, shash, now, tid),
                    )
                    conn.execute(
                        """
                        INSERT INTO v31_mcp_tool_ext (tool_id, input_schema_json, output_schema_json, enabled, removed, schema_changed_at, updated_at)
                        VALUES (?, ?, ?, 1, 0, ?, ?)
                        ON CONFLICT(tool_id) DO UPDATE SET
                            input_schema_json = excluded.input_schema_json,
                            output_schema_json = excluded.output_schema_json,
                            removed = 0,
                            schema_changed_at = COALESCE(excluded.schema_changed_at, v31_mcp_tool_ext.schema_changed_at),
                            updated_at = excluded.updated_at
                        """,
                        (tid, json.dumps(in_schema, ensure_ascii=False), json.dumps(out_schema, ensure_ascii=False), now if schema_changed else None, now),
                    )
                    if schema_changed:
                        changed.append(tname)

            for old_name, old_row in existing_rows.items():
                if old_name not in seen_names:
                    conn.execute(
                        """
                        INSERT INTO v31_mcp_tool_ext (tool_id, removed, enabled, updated_at) VALUES (?, 1, 0, ?)
                        ON CONFLICT(tool_id) DO UPDATE SET removed = 1, enabled = 0, updated_at = excluded.updated_at
                        """,
                        (old_row["id"], now),
                    )
                    removed.append(old_name)

            if changed and srv["trust_tier"] != "builtin" and srv["approval_state"] == "approved":
                reapproval_triggered = True
                conn.execute("UPDATE v3_mcp_servers SET approval_state = 'pending_approval', updated_at = ? WHERE id = ?", (now, server_id))

            conn.execute(
                """
                INSERT INTO v31_mcp_server_ext (server_id, adapter_transport, protocol_version, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(server_id) DO UPDATE SET protocol_version = excluded.protocol_version, updated_at = excluded.updated_at
                """,
                (server_id, transport, proto_ver, now),
            )
            self.v3_service._audit_mcp(
                conn,
                action="discover",
                status="reapproval_required" if reapproval_triggered else "ok",
                actor=actor,
                server_id=server_id,
                details={"discovered": len(seen_names), "added": added, "changed": changed, "removed": removed, "protocol_version": proto_ver},
            )
        return {
            "server_id": server_id,
            "protocol_version": proto_ver,
            "discovered_count": len(seen_names),
            "added": added,
            "changed": changed,
            "removed": removed,
            "reapproval_triggered": reapproval_triggered,
        }

    # --- P2.6 Real Invocation Gateway (14-step enforcement) ---
    def invoke_tool(self, req: McpInvokeRequest, actor: str = "agent") -> dict[str, Any]:
        self.v3_service._ensure_seeded()
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        sid = str(req.session_id) if req.session_id else None
        tid = str(req.trace_id) if req.trace_id else None
        inv_id = str(uuid.uuid4())
        request_id = str(uuid.uuid4())
        started_at = utc_now_iso()
        t0 = time.monotonic()

        def _record(status: str, out_sum: str = "", err_code: str | None = None, err_msg: str | None = None) -> None:
            lat = int((time.monotonic() - t0) * 1000)
            ended = utc_now_iso()
            in_sum = json.dumps(redact_payload(req.arguments), ensure_ascii=False)[:1000]
            with self.db.write_tx() as wconn:
                wconn.execute(
                    """
                    INSERT INTO v31_mcp_invocations (
                        id, request_id, correlation_id, project_id, worktree_id, business_id, session_id, trace_id,
                        server_id, tool_name, capability, actor, status, input_summary, output_summary,
                        error_code, error_message, latency_ms, approval_ref, idempotency_key, started_at, ended_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inv_id, request_id, req.correlation_id, pid, wid, req.business_id, sid, tid,
                        req.server_id, req.tool_name, req.capability, actor, status, in_sum, out_sum[:2000],
                        err_code, (err_msg or "")[:500] if err_msg else None, lat, req.approval_ref,
                        req.idempotency_key if status == "completed" else None, started_at, ended,
                    ),
                )
                wconn.execute(
                    """
                    UPDATE v31_mcp_server_ext
                    SET invocation_count = invocation_count + 1,
                        success_count = success_count + ?,
                        failure_count = failure_count + ?,
                        total_latency_ms = total_latency_ms + ?,
                        last_success_at = CASE WHEN ? = 1 THEN ? ELSE last_success_at END,
                        updated_at = ?
                    WHERE server_id = ?
                    """,
                    (1 if status == "completed" else 0, 0 if status == "completed" else 1, lat, 1 if status == "completed" else 0, ended, ended, req.server_id),
                )
                self.v3_service._audit_mcp(
                    wconn,
                    action="invoke",
                    status=status,
                    actor=actor,
                    project_id=pid,
                    worktree_id=wid,
                    server_id=req.server_id,
                    tool_name=req.tool_name,
                    latency_ms=lat,
                    error_code=err_code,
                    details={"business_id": req.business_id, "invocation_id": inv_id, "request_id": request_id},
                )

        # Idempotency check
        with self.db.read_conn() as conn:
            if req.idempotency_key:
                prev = conn.execute(
                    "SELECT * FROM v31_mcp_invocations WHERE server_id = ? AND tool_name = ? AND idempotency_key = ?",
                    (req.server_id, req.tool_name, req.idempotency_key),
                ).fetchone()
                if prev:
                    return {
                        "invocation_id": prev["id"],
                        "request_id": prev["request_id"],
                        "status": prev["status"],
                        "idempotent_replay": True,
                        "output_summary": prev["output_summary"],
                        "latency_ms": prev["latency_ms"],
                    }

            # 2-3. Project & worktree scope
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                _record("denied", err_code="project_not_found", err_msg="Project not found")
                raise BridgeError(404, "project_not_found", "Project not found.")
            if wid and not conn.execute("SELECT 1 FROM worktrees WHERE id = ? AND project_id = ?", (wid, pid)).fetchone():
                _record("denied", err_code="worktree_scope_denied", err_msg="Worktree does not belong to project")
                raise BridgeError(403, "worktree_scope_denied", "Worktree does not belong to project.")

            # 4. Business workspace check
            biz = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (req.business_id,)).fetchone()
            if not biz or biz["archived"] or not biz["enabled"]:
                _record("denied", err_code="business_disabled", err_msg="Business workspace is archived or disabled")
                raise BridgeError(403, "business_disabled", "Business workspace is archived or disabled.")

            # 5. Server approval & enabled check
            srv = conn.execute("SELECT * FROM v3_mcp_servers WHERE id = ?", (req.server_id,)).fetchone()
            if not srv:
                _record("denied", err_code="mcp_server_not_found", err_msg="MCP server not found")
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            ext = conn.execute("SELECT * FROM v31_mcp_server_ext WHERE server_id = ?", (req.server_id,)).fetchone()
            if srv["approval_state"] != "approved" or (ext and (ext["archived"] or not ext["enabled"])):
                _record("denied", err_code="server_not_approved", err_msg=f"Server approval={srv['approval_state']}")
                raise BridgeError(403, "server_not_approved", "MCP server is not approved or is disabled.")

            # Cross-project access check
            allowed_projs = json.loads(srv["allowed_project_ids_json"] or "[]")
            if srv["trust_tier"] != "builtin" and allowed_projs and pid not in allowed_projs:
                _record("denied", err_code="cross_project_denied", err_msg="Project not in server allowlist")
                raise BridgeError(403, "cross_project_denied", "Server is not authorized for this project.")

            # Business binding check
            bind = conn.execute(
                "SELECT * FROM v31_business_mcp_bindings WHERE business_id = ? AND server_id = ?",
                (req.business_id, req.server_id),
            ).fetchone()
            if not bind or not bind["enabled"]:
                _record("denied", err_code="server_not_bound", err_msg="Server is not enabled for this business workspace")
                raise BridgeError(403, "server_not_bound", "MCP server is not bound/enabled in this business workspace.")

            # Project-level MCP override check (project may disable/restrict, never escalate)
            proj_ovr = conn.execute(
                "SELECT mode FROM mcp321_project_mcp_overrides WHERE project_id = ? AND business_id = ? AND server_id = ?",
                (pid, req.business_id, req.server_id),
            ).fetchone()
            if proj_ovr and proj_ovr["mode"] == "disabled":
                _record("denied", err_code="project_override_disabled", err_msg="Server disabled by project-level MCP override")
                raise BridgeError(403, "project_override_disabled", "MCP server is disabled for this project.")

            # 6-7. Tool lookup & default-deny permission enforcement
            tool = conn.execute(
                "SELECT * FROM v3_mcp_tools WHERE server_id = ? AND tool_name = ?",
                (req.server_id, req.tool_name),
            ).fetchone()
            if not tool:
                _record("denied", err_code="tool_not_found", err_msg="Tool not discovered on server")
                raise BridgeError(404, "tool_not_found", f"Tool '{req.tool_name}' not found on server.")
            text_row = conn.execute("SELECT * FROM v31_mcp_tool_ext WHERE tool_id = ?", (tool["id"],)).fetchone()
            if text_row and (text_row["removed"] or not text_row["enabled"]):
                _record("denied", err_code="tool_disabled", err_msg="Tool is disabled or removed")
                raise BridgeError(403, "tool_disabled", f"Tool '{req.tool_name}' is disabled or removed.")

            tp = conn.execute(
                "SELECT policy FROM v31_business_tools WHERE business_id = ? AND tool_id = ?",
                (req.business_id, tool["id"]),
            ).fetchone()
            effective_policy = tp["policy"] if tp else "deny"  # Strict default-deny!
            if effective_policy == "deny":
                _record("denied", err_code="tool_permission_denied", err_msg="Default-deny or explicit deny policy")
                raise BridgeError(403, "tool_permission_denied", f"Tool '{req.tool_name}' is denied by business policy.")
            if (effective_policy == "approval" or tool["requires_approval"]) and not req.approval_ref:
                _record("approval_required", err_code="approval_required", err_msg="Explicit approval_ref required")
                raise BridgeError(403, "approval_required", f"Tool '{req.tool_name}' requires explicit approval_ref.")

            # 9. Input schema validation
            in_schema = json.loads(text_row["input_schema_json"] if text_row else "{}")
            try:
                _validate_arguments_against_schema(req.arguments, in_schema)
            except BridgeError as exc:
                _record("failed", err_code=exc.code, err_msg=exc.message)
                raise

        # 8 & 12. Concurrency guard + real transport execution
        transport = ext["adapter_transport"] if ext else srv["transport"]
        timeout = float(req.timeout_sec or (ext["timeout_sec"] if ext else 30))
        conc_limit = int(ext["concurrency_limit"] if ext else 4)
        secret_refs = json.loads(ext["secret_refs_json"] if ext else "[]")
        srv_sem = self._get_server_sem(req.server_id, conc_limit)

        if not self._global_sem.acquire(blocking=False):
            _record("failed", err_code="global_concurrency_limit", err_msg="Global max concurrent MCP limit reached")
            raise BridgeError(429, "global_concurrency_limit", "Global concurrent MCP limit reached; try again or stop idle servers.")
        try:
            if not srv_sem.acquire(blocking=False):
                _record("failed", err_code="server_concurrency_limit", err_msg="Per-server concurrency limit reached")
                raise BridgeError(429, "server_concurrency_limit", "Server concurrency limit reached.")
            try:
                max_attempts = 2 if tool["side_effect_level"] == "read_only" else 1  # Never blind-retry side-effect tools!
                last_exc: Exception | None = None
                result_obj: Any = None
                for attempt in range(max_attempts):
                    try:
                        if transport == "local_tool":
                            entry = self._local_tools.get((srv["name"], req.tool_name))
                            if not entry:
                                raise BridgeError(501, "unsupported_local_tool", f"No local handler for {srv['name']}.{req.tool_name}")
                            _, handler = entry
                            result_obj = handler(req.arguments)
                        elif transport == "stdio":
                            exe = ext["executable"] if ext else srv["endpoint_or_cmd"]
                            args = json.loads(ext["args_json"] if ext else "[]")
                            env_allow = json.loads(ext["env_allowlist_json"] if ext else "[]")
                            cwd = ext["working_dir"] if ext else ""
                            if self._should_use_host_stdio_bridge(exe):
                                ires = self._call_host_stdio_bridge(
                                    "invoke",
                                    {
                                        "server_id": req.server_id,
                                        "executable": exe,
                                        "args": args,
                                        "working_dir": cwd,
                                        "env_allowlist": env_allow,
                                        "secret_refs": secret_refs,
                                        "tool_name": req.tool_name,
                                        "arguments": req.arguments,
                                        "timeout_sec": max(timeout, 30.0),
                                    },
                                    timeout=max(timeout, 45.0) + 5.0,
                                )
                                result_obj = ires.get("result") or {}
                            else:
                                proc = self._get_or_create_stdio(req.server_id, exe, args, cwd, self._build_stdio_env(env_allow, secret_refs))
                                result_obj = proc.call_tool(req.tool_name, req.arguments, timeout=timeout)
                        elif transport == "streamable_http":
                            ep, headers = self._resolve_effective_http_target(srv, secret_refs)
                            result_obj = _http_mcp_call_tool(ep, headers, req.tool_name, req.arguments, timeout=timeout)
                        else:
                            raise BridgeError(501, "transport_unsupported", f"Invocation for transport '{transport}' requires local_tool/stdio/streamable_http.")
                        last_exc = None
                        break
                    except (TimeoutError, httpx.TimeoutException) as exc:
                        last_exc = exc
                        if attempt + 1 >= max_attempts:
                            _record("timeout", err_code="timeout", err_msg=str(exc))
                            raise BridgeError(504, "mcp_timeout", f"MCP invocation timed out after {timeout}s.") from exc
                    except BridgeError:
                        raise
                    except Exception as exc:
                        last_exc = exc
                        if attempt + 1 >= max_attempts:
                            _record("failed", err_code="invocation_error", err_msg=str(exc))
                            raise BridgeError(502, "mcp_invocation_failed", f"MCP invocation failed: {exc}") from exc

                # 13. Bound & redact output
                redacted = redact_payload(result_obj)
                serialized = json.dumps(redacted, ensure_ascii=False)
                truncated = len(serialized) > req.max_output_chars
                bounded_text = serialized[: req.max_output_chars]
                _record("completed", out_sum=bounded_text)
                return {
                    "invocation_id": inv_id,
                    "request_id": request_id,
                    "status": "completed",
                    "server_id": req.server_id,
                    "tool_name": req.tool_name,
                    "truncated": truncated,
                    "latency_ms": int((time.monotonic() - t0) * 1000),
                    "result": redacted if not truncated else {"truncated_excerpt": bounded_text, "original_chars": len(serialized)},
                }
            finally:
                srv_sem.release()
        finally:
            self._global_sem.release()

    # --- P3.3 & P3.4 & P3.6 Business-Aware Capability Routing + Task Policy Snapshot ---
    @staticmethod
    def _infer_capabilities_from_objective(objective: str) -> set[str]:
        """Deterministic (0 LLM) keyword-to-capability matcher for dynamic MCP routing."""
        text = (objective or "").lower()
        if not text:
            return set()
        kw_map = {
            "git_read": ("git ", "branch", "commit", "diff", "log "),
            "fs_read": ("file", "thư mục", "đọc tệp", "filesystem", "path"),
            "docs_lookup": ("docs", "tài liệu", "api ref", "context7", "library"),
            "github_read": ("github", "pull request", "pr ", "issue"),
            "glb_inspect": ("glb", "gltf", "3d", "mesh", "model 3d"),
            "blender_headless": ("blender", "render 3d", "fbx"),
            "browser_qa": ("playwright", "browser", "viewport", "screenshot", "ui qa", "trình duyệt"),
            "devtools_debug": ("devtools", "console", "webgl", "network", "fps", "performance"),
            "db_metadata": ("sql", "database", "schema", "oracle", "postgres", "table", "erd"),
            "openapi_inspect": ("openapi", "swagger", "endpoint", "contract"),
            "diagram_render": ("mermaid", "plantuml", "diagram", "kiến trúc", "c4"),
            "java_semantic": ("java", "spring", "maven", "pom.xml", "junit"),
            "ts_semantic": ("typescript", "tsconfig", ".ts", "phaser", "three.js"),
            "security_scan": ("security", "secret", "bảo mật", "audit"),
            "log_analyze": ("stacktrace", "exception", "error log", "phân tích log"),
        }
        inferred: set[str] = set()
        for cap, kws in kw_map.items():
            if any(k in text for k in kws):
                inferred.add(cap)
        return inferred

    def route_for_business(self, req: McpBusinessRouteRequest, actor: str = "planner") -> dict[str, Any]:
        self.v3_service._ensure_seeded()
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        sid = str(req.session_id) if req.session_id else None
        wanted = {c.strip().lower() for c in req.required_capabilities if c.strip()}
        if not wanted and req.task_objective:
            wanted = self._infer_capabilities_from_objective(req.task_objective)
        if not wanted:
            raise BridgeError(422, "capabilities_required", "Provide required_capabilities or task_objective with recognizable capabilities.")
        now = utc_now_iso()

        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            biz = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (req.business_id,)).fetchone()
            if not biz or biz["archived"] or not biz["enabled"]:
                raise BridgeError(403, "business_disabled", "Business workspace is archived or disabled.")

            bindings = conn.execute(
                """
                SELECT bm.*, s.name AS server_name, s.transport, s.approval_state, s.health_status,
                       s.trust_tier, s.allowed_project_ids_json,
                       COALESCE(ext.adapter_transport, s.transport) AS adapter_transport,
                       COALESCE(ext.enabled, 1) AS server_enabled,
                       COALESCE(ext.archived, 0) AS server_archived
                FROM v31_business_mcp_bindings bm
                JOIN v3_mcp_servers s ON s.id = bm.server_id
                LEFT JOIN v31_mcp_server_ext ext ON ext.server_id = s.id
                WHERE bm.business_id = ?
                ORDER BY bm.priority ASC, s.name ASC
                """,
                (req.business_id,),
            ).fetchall()

            proj_overrides = {
                r["server_id"]: {"mode": r["mode"], "priority": r["priority"]}
                for r in conn.execute(
                    "SELECT server_id, mode, priority FROM mcp321_project_mcp_overrides WHERE project_id = ? AND business_id = ?",
                    (pid, req.business_id),
                ).fetchall()
            }

            tool_policies = {
                r["tool_id"]: r["policy"]
                for r in conn.execute("SELECT tool_id, policy FROM v31_business_tools WHERE business_id = ?", (req.business_id,)).fetchall()
            }

            proposed_servers: list[dict[str, Any]] = []
            selected_tools: list[dict[str, Any]] = []
            selected_server_ids: set[str] = set()
            excluded_reasons: list[dict[str, Any]] = []
            matched_caps: set[str] = set()
            total_schema_bytes = 0

            # Task overrides cannot escalate beyond bound servers!
            bound_ids = {b["server_id"] for b in bindings}
            for inc in req.include_server_ids:
                if inc not in bound_ids:
                    excluded_reasons.append({"server_id": inc, "reason": "task_override_cannot_escalate_unbound_server"})

            for b in bindings:
                srv_id = b["server_id"]
                act_mode = b["activation_mode"] if "activation_mode" in b.keys() and b["activation_mode"] else ("required" if b["required"] else "on_demand")
                proposed_servers.append({"server_id": srv_id, "server_name": b["server_name"], "activation_mode": act_mode, "approval_state": b["approval_state"], "health_status": b["health_status"]})

                ovr = proj_overrides.get(srv_id)
                eff_priority = ovr["priority"] if (ovr and ovr["priority"] is not None) else b["priority"]
                if ovr and ovr["mode"] == "disabled":
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "disabled_by_project_override"})
                    continue
                if srv_id in req.exclude_server_ids:
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "excluded_by_task_override"})
                    continue
                if act_mode == "manual" and req.routing_mode == "automatic" and srv_id not in req.include_server_ids and srv_id not in req.required_server_ids:
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "manual_activation_only"})
                    continue
                if req.routing_mode == "manual" and req.include_server_ids and srv_id not in req.include_server_ids:
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "not_in_manual_include_list"})
                    continue
                if not b["enabled"] or not b["server_enabled"] or b["server_archived"]:
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "disabled_or_archived"})
                    continue
                if b["approval_state"] != "approved":
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": f"approval_{b['approval_state']}"})
                    continue
                if b["health_status"] in ("offline", "auth_failed", "timeout", "revoked", "disabled"):
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": f"health_{b['health_status']}"})
                    continue
                allowed_projs = json.loads(b["allowed_project_ids_json"] or "[]")
                if b["trust_tier"] != "builtin" and allowed_projs and pid not in allowed_projs:
                    excluded_reasons.append({"server_id": srv_id, "server_name": b["server_name"], "reason": "project_not_allowed"})
                    continue

                tools = conn.execute(
                    """
                    SELECT t.*, COALESCE(te.input_schema_json, '{}') AS input_schema_json
                    FROM v3_mcp_tools t
                    LEFT JOIN v31_mcp_tool_ext te ON te.tool_id = t.id
                    WHERE t.server_id = ?
                    ORDER BY t.tool_name
                    """,
                    (srv_id,),
                ).fetchall()
                for t in tools:
                    pol = tool_policies.get(t["id"], "deny")
                    if pol == "deny":
                        continue
                    if not req.allow_write and t["side_effect_level"] != "read_only":
                        continue
                    tags = {x.lower() for x in json.loads(t["capability_tags_json"] or "[]")}
                    overlap = sorted(wanted & tags)
                    if overlap or srv_id in req.required_server_ids:
                        matched_caps.update(overlap)
                        selected_server_ids.add(srv_id)
                        schema_bytes = len((t["input_schema_json"] or "{}").encode("utf-8")) + len((t["description"] or "").encode("utf-8"))
                        selected_tools.append(
                            {
                                "server_id": srv_id,
                                "server_name": b["server_name"],
                                "server_kind": "local_tool_pack" if b["adapter_transport"] == "local_tool" else "mcp_server",
                                "transport": b["adapter_transport"],
                                "tool_id": t["id"],
                                "tool_name": t["tool_name"],
                                "matched_capabilities": overlap,
                                "policy": pol,
                                "side_effect_level": t["side_effect_level"],
                                "requires_approval": pol == "approval" or bool(t["requires_approval"]),
                                "priority": eff_priority,
                                "schema_bytes": schema_bytes,
                            }
                        )

            selected_tools.sort(key=lambda x: (x["priority"], -len(x["matched_capabilities"]), x["server_name"], x["tool_name"]))
            selected_tools = selected_tools[: req.max_tools]
            total_schema_bytes = sum(t["schema_bytes"] for t in selected_tools)
            missing_caps = sorted(wanted - matched_caps)
            blockers = [f"Missing required capability: {c}" for c in missing_caps]
            for req_srv in req.required_server_ids:
                if req_srv not in selected_server_ids:
                    blockers.append(f"Required MCP server '{req_srv}' is unavailable or denied by policy.")

            actually_invoked = 0
            if sid:
                actually_invoked = conn.execute(
                    "SELECT COUNT(*) FROM v31_mcp_invocations WHERE session_id = ? AND status = 'completed'",
                    (sid,),
                ).fetchone()[0]

            # Save immutable P3.6 configuration snapshot
            snap_id = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO v31_task_policy_snapshots (
                    id, task_id, session_id, project_id, worktree_id, business_id, config_revision,
                    effective_policy_json, selected_servers_json, selected_tools_json, excluded_reasons_json,
                    model_policy_json, resource_policy_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap_id, req.task_id, sid, pid, wid, req.business_id, biz["revision"],
                    json.dumps({"routing_mode": req.routing_mode, "allow_write": req.allow_write, "required_capabilities": sorted(wanted), "tool_schema_bytes": total_schema_bytes}),
                    json.dumps(sorted(selected_server_ids)),
                    json.dumps(selected_tools, ensure_ascii=False),
                    json.dumps(excluded_reasons, ensure_ascii=False),
                    biz["model_policy_json"],
                    biz["resource_policy_json"],
                    now,
                ),
            )
            self.v3_service._audit_mcp(
                conn,
                action="business_route",
                status="blocked" if blockers else "matched",
                actor=actor,
                project_id=pid,
                worktree_id=wid,
                details={"business_id": req.business_id, "snapshot_id": snap_id, "selected_tools": len(selected_tools), "tool_schema_bytes": total_schema_bytes, "blockers": blockers},
            )

        return {
            "snapshot_id": snap_id,
            "business_id": req.business_id,
            "config_revision": biz["revision"],
            "proposed_servers": proposed_servers,
            "selected_servers": sorted(selected_server_ids),
            "selected_tools": selected_tools,
            "excluded_reasons": excluded_reasons,
            "blockers": blockers,
            "status": "blocked" if blockers else "ready",
            "telemetry": {
                "proposed_count": len(proposed_servers),
                "selected_server_count": len(selected_server_ids),
                "provided_tool_count": len(selected_tools),
                "actually_invoked_count": actually_invoked,
                "tool_schema_bytes": total_schema_bytes,
                "estimated_schema_tokens_subset": round(total_schema_bytes / 4) if total_schema_bytes else 0,
                "subset_of_input_not_added_to_total": True,
            },
        }

    # --- Monitoring & Invocation History (P9.4) ---
    def list_invocations(
        self,
        *,
        business_id: str | None = None,
        project_id: str | None = None,
        session_id: str | None = None,
        actor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        args: list[Any] = []
        if business_id:
            clauses.append("business_id = ?")
            args.append(business_id)
        if project_id:
            clauses.append("project_id = ?")
            args.append(project_id)
        if session_id:
            clauses.append("session_id = ?")
            args.append(session_id)
        if actor:
            clauses.append("actor = ?")
            args.append(actor)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM v31_mcp_invocations WHERE {' AND '.join(clauses)} ORDER BY started_at DESC LIMIT ?",
                [*args, min(max(1, limit), 200)],
            ).fetchall()
            return {"items": [dict(r) for r in rows]}

    def get_monitoring_summary(self) -> dict[str, Any]:
        srvs = self.list_servers(include_archived=False)["items"]
        mcp_online = sum(1 for s in srvs if s["transport"] != "local_tool" and s["approval_state"] == "approved" and s["health_status"] == "ready")
        local_packs_ready = sum(1 for s in srvs if s["transport"] == "local_tool" and s["approval_state"] == "approved" and s["health_status"] == "ready")
        online = mcp_online + local_packs_ready
        offline = sum(1 for s in srvs if s["health_status"] in ("offline", "timeout", "auth_failed", "down"))
        running = sum(1 for s in srvs if s["process_status"] == "running")
        pending = sum(1 for s in srvs if s["approval_state"] == "pending_approval")
        unused = sum(1 for s in srvs if s["metrics"]["invocation_count"] == 0)
        total_inv = sum(s["metrics"]["invocation_count"] for s in srvs)
        total_ok = sum(s["metrics"]["success_count"] for s in srvs)
        total_fail = sum(s["metrics"]["failure_count"] for s in srvs)
        return {
            "servers_total": len(srvs),
            "online": online,
            "mcp_online": mcp_online,
            "local_packs_ready": local_packs_ready,
            "offline": offline,
            "running_processes": running,
            "pending_approval": pending,
            "unused": unused,
            "invocations_total": total_inv,
            "invocations_success": total_ok,
            "invocations_failed": total_fail,
            "resource_enforcement_note": "Concurrency and timeout are actively enforced by Gateway; RAM/CPU limits are configured policies (OS cgroup enforcement applies only inside Docker containers).",
        }
