"""Pydantic request/response models conforming to INTERFACE-V1.md."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ErrorDetail(StrictModel):
    code: str
    message: str


class ErrorEnvelope(StrictModel):
    error: ErrorDetail


class SearchCodeRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=5, ge=1, le=10)
    max_chars: int = Field(default=4000, ge=1, le=8000)


class SearchCodeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    title: str
    excerpt: str
    source_commit: str
    state: Literal["indexed"] = "indexed"


class SearchCodeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[SearchCodeItem]
    truncated: bool
    index_freshness: Literal["fresh", "stale", "unindexed"]
    head_commit: str | None = None
    dirty_fingerprint: str | None = None
    elapsed_ms: int | None = None


class IndexRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    mode: Literal["fast", "moderate", "full"] = "fast"
    force: bool = False


class IndexJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_id: str
    worktree_id: str
    state: Literal["queued", "running", "succeeded", "failed"]
    mode: str
    head_commit: str
    dirty_fingerprint: str
    deduplicated: bool = False
    no_op: bool = False
    nodes: int | None = None
    edges: int | None = None
    error: str | None = None
    created_at: str
    updated_at: str


class WorktreeEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: UUID
    worktree_id: UUID
    canonical_repo_id: str
    container_root: str
    host_root: str
    branch: str | None = None
    head_commit: str | None = None
    dirty_fingerprint: str | None = None
    exclude: list[str] = Field(default_factory=list)


class JsonRpcRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    jsonrpc: str = "2.0"
    id: Any = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
