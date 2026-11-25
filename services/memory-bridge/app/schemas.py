"""Strict Pydantic v2 schemas for Memory Bridge V1 REST and MCP contracts."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    canonical_identity: str = Field(min_length=1, max_length=512)
    policy: dict[str, Any] = Field(default_factory=dict)
    hermes_approved: bool = False


class WorktreeCreateRequest(StrictModel):
    canonical_root: str = Field(min_length=1, max_length=1024)
    branch: str | None = Field(default=None, max_length=256)
    head_commit: str | None = Field(default=None, max_length=64)
    is_detached: bool = False
    dirty_fingerprint: str = Field(default="", max_length=128)


class MemorySearchRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=5, ge=1, le=10)
    max_chars: int = Field(default=4000, ge=256, le=8000)
    include_project_wide: bool = False
    include_candidates: bool = False


class CodeSearchRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    query: str = Field(min_length=1, max_length=512)
    limit: int = Field(default=5, ge=1, le=10)
    max_chars: int = Field(default=4000, ge=256, le=8000)


class IndexTriggerRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    mode: Literal["fast", "moderate", "full"] = "fast"
    force: bool = False


class EventCreateRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    task_id: str = Field(default="general", min_length=1, max_length=128)
    source_agent: str | None = Field(default=None, max_length=64)
    kind: str = Field(min_length=1, max_length=64)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=128)
    timestamp: str | int | float | None = None
    approved: bool = False


class MemoryCreateRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID | None = None
    type: Literal["decision", "bugfix", "requirement", "handoff", "experience", "note"] = "note"
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=16000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    source_agent: str = Field(min_length=1, max_length=64)
    source_commit: str | None = Field(default=None, max_length=64)
    evidence_ref: str | None = Field(default=None, max_length=512)
    supersedes: UUID | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return [t[:48] for t in v if t.strip()]


class MemoryVerifyRequest(StrictModel):
    action: Literal["verify", "supersede"] = "verify"
    supersedes: UUID | None = None


class HandoffCreateRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    task_id: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=8000)
    evidence: dict[str, Any] = Field(default_factory=dict)
    source_agent: str = Field(min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=128)


class WorkerResultRequest(StrictModel):
    project_id: UUID
    worktree_id: UUID
    event_id: UUID | None = None
    type: Literal["decision", "bugfix", "requirement", "handoff", "experience", "note"] = "experience"
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=16000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    evidence_ref: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(min_length=1, max_length=128)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0.0)


class RestoreRequest(StrictModel):
    backup_id: UUID
