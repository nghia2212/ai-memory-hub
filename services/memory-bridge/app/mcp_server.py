"""FastMCP server for Memory Bridge V1 custom tools."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastmcp import FastMCP

from .schemas import (
    CodeSearchRequest,
    HandoffCreateRequest,
    MemoryCreateRequest,
    MemorySearchRequest,
)
from .service import MemoryBridgeService


def create_mcp_server(service: MemoryBridgeService) -> FastMCP:
    mcp = FastMCP("ai-memory-bridge")

    @mcp.tool()
    def get_project_context(project_id: str, worktree_id: str) -> dict[str, Any]:
        """Get bounded verified context for a registered project and worktree."""
        return service.get_project_context(str(UUID(project_id)), str(UUID(worktree_id)))

    @mcp.tool()
    def search_memory(
        project_id: str,
        worktree_id: str,
        query: str,
        limit: int = 5,
        max_chars: int = 4000,
        include_project_wide: bool = False,
    ) -> dict[str, Any]:
        """Search verified memories using Unicode FTS5 within strict project/worktree scope."""
        req = MemorySearchRequest(
            project_id=UUID(project_id),
            worktree_id=UUID(worktree_id),
            query=query,
            limit=limit,
            max_chars=max_chars,
            include_project_wide=include_project_wide,
            include_candidates=False,
        )
        return service.search_memory(req)

    @mcp.tool()
    def get_handoff(project_id: str, worktree_id: str, task_id: str) -> dict[str, Any]:
        """Retrieve task handoffs scoped to project_id and worktree_id."""
        return service.get_handoff(task_id=task_id, project_id=str(UUID(project_id)), worktree_id=str(UUID(worktree_id)))

    @mcp.tool()
    def save_task_result(
        project_id: str,
        worktree_id: str,
        task_id: str,
        summary: str,
        evidence: dict[str, Any],
        idempotency_key: str,
        source_agent: str = "mcp",
    ) -> dict[str, Any]:
        """Save a candidate task handoff result with idempotency_key."""
        req = HandoffCreateRequest(
            project_id=UUID(project_id),
            worktree_id=UUID(worktree_id),
            task_id=task_id,
            summary=summary,
            evidence=evidence,
            source_agent=source_agent,
            idempotency_key=idempotency_key,
        )
        _, result = service.save_handoff(req)
        return result

    @mcp.tool()
    def save_decision(
        project_id: str,
        worktree_id: str,
        title: str,
        summary: str,
        evidence: str,
        idempotency_key: str,
        source_agent: str = "mcp",
    ) -> dict[str, Any]:
        """Save a candidate decision memory with idempotency_key."""
        req = MemoryCreateRequest(
            project_id=UUID(project_id),
            worktree_id=UUID(worktree_id),
            type="decision",
            title=title,
            body=summary,
            evidence_ref=evidence,
            source_agent=source_agent,
            idempotency_key=idempotency_key,
        )
        _, result = service.create_memory(req)
        return result

    @mcp.tool()
    def search_code(
        project_id: str,
        worktree_id: str,
        query: str,
        limit: int = 5,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        """Search code symbols via the Codebase Memory adapter."""
        req = CodeSearchRequest(
            project_id=UUID(project_id),
            worktree_id=UUID(worktree_id),
            query=query,
            limit=limit,
            max_chars=max_chars,
        )
        return service.search_code(req)

    return mcp
