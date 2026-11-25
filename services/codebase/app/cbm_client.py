"""MCP stdio client for pinned codebase-memory-mcp v0.12.1 binary."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shlex
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class CBMClientError(RuntimeError):
    """Raised when the upstream codebase-memory-mcp process fails."""


class CBMBackend(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    def is_ready(self) -> bool: ...
    async def index_repository(
        self, *, repo_path: str, project_name: str, mode: str = "fast", timeout: float = 120.0
    ) -> dict[str, Any]: ...
    async def search_graph(
        self, *, project_name: str, name_pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]: ...
    async def search_code(
        self, *, project_name: str, pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]: ...
    async def index_status(self, *, project_name: str, timeout: float = 10.0) -> dict[str, Any]: ...


def _extract_tool_payload(rpc_resp: dict[str, Any]) -> dict[str, Any]:
    if "error" in rpc_resp:
        err = rpc_resp["error"]
        raise CBMClientError(f"MCP error {err.get('code')}: {err.get('message')}")
    result = rpc_resp.get("result") or {}
    if result.get("isError"):
        content = result.get("content") or []
        msg = content[0].get("text", "Tool call failed") if content else "Tool call failed"
        raise CBMClientError(msg)
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    for item in result.get("content") or []:
        if item.get("type") == "text":
            text = item.get("text", "")
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    return parsed
            except json.JSONDecodeError:
                return {"raw_text": text}
    return {}


class StdioCBMClient:
    """Persistent MCP stdio JSON-RPC client for codebase-memory-mcp."""

    def __init__(
        self,
        command: str | list[str],
        cache_dir: Path,
        allowed_root: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self._cmd = shlex.split(command) if isinstance(command, str) else list(command)
        self._cache_dir = cache_dir
        self._allowed_root = allowed_root
        self._extra_env = extra_env or {}
        self._proc: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._next_id = 1
        self._initialized = False

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["CBM_CACHE_DIR"] = str(self._cache_dir)
        env["CBM_LOG_LEVEL"] = "warn"
        if self._allowed_root is not None:
            env["CBM_ALLOWED_ROOT"] = str(self._allowed_root)
        env.update(self._extra_env)
        return env

    def is_ready(self) -> bool:
        if self._cmd and self._cmd[0].startswith("/"):
            return Path(self._cmd[0]).is_file() and os.access(self._cmd[0], os.X_OK)
        return bool(self._cmd)

    async def _configure_safe_defaults(self) -> None:
        """Disable auto-index, auto-watch, and background watcher so no unapproved indexing occurs."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        env = self._build_env()
        for key, val in (("auto_index", "false"), ("auto_watch", "false"), ("watcher_enabled", "false")):
            proc = await asyncio.create_subprocess_exec(
                *self._cmd,
                "config",
                "set",
                key,
                val,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            await asyncio.wait_for(proc.communicate(), timeout=5.0)

    async def start(self) -> None:
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None and self._initialized:
                return
            await self._configure_safe_defaults()
            await self._spawn_and_init()

    async def _spawn_and_init(self) -> None:
        self._proc = await asyncio.create_subprocess_exec(
            *self._cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self._build_env(),
        )
        self._initialized = False
        init_resp = await self._send_rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "ai-memory-hub-codebase-adapter", "version": "1.0"},
            },
            timeout=10.0,
        )
        server_info = (init_resp.get("result") or {}).get("serverInfo", {})
        logger.info("Connected to %s %s", server_info.get("name"), server_info.get("version"))
        await self._send_notification("notifications/initialized")
        self._initialized = True

    async def close(self) -> None:
        async with self._lock:
            if self._proc is not None and self._proc.returncode is None:
                if self._proc.stdin:
                    self._proc.stdin.close()
                try:
                    await asyncio.wait_for(self._proc.wait(), timeout=3.0)
                except asyncio.TimeoutError:
                    self._proc.kill()
                    await self._proc.wait()
            self._proc = None
            self._initialized = False

    async def _send_notification(self, method: str, params: dict[str, Any] | None = None) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise CBMClientError("CBM process not running")
        payload = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            payload["params"] = params
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

    async def _send_rpc(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        if self._proc is None or self._proc.stdin is None or self._proc.stdout is None:
            raise CBMClientError("CBM process not running")
        req_id = self._next_id
        self._next_id += 1
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        line = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        self._proc.stdin.write(line)
        await self._proc.stdin.drain()

        async def _read_matching() -> dict[str, Any]:
            assert self._proc is not None and self._proc.stdout is not None
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    raise CBMClientError("CBM stdout closed unexpectedly")
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                msg = json.loads(text)
                if msg.get("id") == req_id:
                    return msg

        return await asyncio.wait_for(_read_matching(), timeout=timeout)

    async def call_tool(self, name: str, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
        async with self._lock:
            if self._proc is None or self._proc.returncode is not None or not self._initialized:
                await self._spawn_and_init()
            try:
                resp = await self._send_rpc(
                    "tools/call",
                    {"name": name, "arguments": arguments},
                    timeout=timeout,
                )
            except (CBMClientError, BrokenPipeError, ConnectionResetError):
                await self._spawn_and_init()
                resp = await self._send_rpc(
                    "tools/call",
                    {"name": name, "arguments": arguments},
                    timeout=timeout,
                )
            return _extract_tool_payload(resp)

    async def index_repository(
        self, *, repo_path: str, project_name: str, mode: str = "fast", timeout: float = 120.0
    ) -> dict[str, Any]:
        return await self.call_tool(
            "index_repository",
            {
                "repo_path": repo_path,
                "name": project_name,
                "mode": mode,
                "persistence": False,
            },
            timeout=timeout,
        )

    async def search_graph(
        self, *, project_name: str, name_pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]:
        return await self.call_tool(
            "search_graph",
            {
                "project": project_name,
                "name_pattern": name_pattern,
                "limit": limit,
                "format": "json",
            },
            timeout=timeout,
        )

    async def search_code(
        self, *, project_name: str, pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]:
        return await self.call_tool(
            "search_code",
            {
                "project": project_name,
                "pattern": pattern,
                "result_limit": limit,
                "raw_limit": limit,
                "format": "json",
            },
            timeout=timeout,
        )

    async def index_status(self, *, project_name: str, timeout: float = 10.0) -> dict[str, Any]:
        return await self.call_tool(
            "index_status",
            {"project": project_name, "format": "json"},
            timeout=timeout,
        )
