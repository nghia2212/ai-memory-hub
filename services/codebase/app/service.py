"""Core indexing and bounded code search service."""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from .cbm_client import CBMBackend, CBMClientError
from .config import Settings
from .models import (
    IndexJobResponse,
    IndexRequest,
    SearchCodeItem,
    SearchCodeRequest,
    SearchCodeResponse,
    WorktreeEntry,
)
from .registry import WorktreeRegistry, cbm_project_name
from .security import (
    UnsafePathError,
    inspect_worktree_git,
    is_excluded_path,
    resolve_safe_path,
)
from .store import AdapterStore


def _parse_line_range(lines_str: Any) -> tuple[int, int]:
    if isinstance(lines_str, str) and "-" in lines_str:
        parts = lines_str.split("-", 1)
        try:
            start = max(1, int(parts[0]))
            end = max(start, int(parts[1]))
            return start, end
        except ValueError:
            pass
    if isinstance(lines_str, int) and lines_str >= 1:
        return lines_str, lines_str
    return 1, 1


def _read_excerpt(file_path: Path, line_start: int, line_end: int, max_lines: int = 20) -> str:
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    start_idx = max(0, line_start - 1)
    end_idx = min(len(lines), max(start_idx + 1, min(line_end, start_idx + max_lines)))
    return "\n".join(lines[start_idx:end_idx])


def _enforce_budget(response: SearchCodeResponse, max_chars: int) -> SearchCodeResponse:
    """Trim excerpts/items so the serialized JSON envelope never exceeds max_chars."""
    serialized = response.model_dump_json(exclude_none=True)
    if len(serialized) <= max_chars:
        return response

    items = [item.model_copy() for item in response.items]
    truncated = True

    # First pass: shrink excerpts proportionally
    for item in items:
        if len(item.excerpt) > 120:
            item.excerpt = item.excerpt[:117] + "..."
    candidate = response.model_copy(update={"items": items, "truncated": truncated})
    if len(candidate.model_dump_json(exclude_none=True)) <= max_chars:
        return candidate

    # Second pass: drop items from the end until envelope fits
    while items:
        items.pop()
        candidate = response.model_copy(update={"items": items, "truncated": True})
        if len(candidate.model_dump_json(exclude_none=True)) <= max_chars:
            return candidate

    return response.model_copy(update={"items": [], "truncated": True})


class CodebaseService:
    def __init__(
        self,
        settings: Settings,
        registry: WorktreeRegistry,
        store: AdapterStore,
        cbm: CBMBackend,
    ) -> None:
        self._settings = settings
        self._registry = registry
        self._store = store
        self._cbm = cbm
        self._scope_locks: dict[str, asyncio.Lock] = {}

    def _get_scope_lock(self, cbm_project: str) -> asyncio.Lock:
        lock = self._scope_locks.get(cbm_project)
        if lock is None:
            lock = asyncio.Lock()
            self._scope_locks[cbm_project] = lock
        return lock

    async def _require_worktree(self, project_id: uuid.UUID, worktree_id: uuid.UUID) -> WorktreeEntry:
        entry = await self._registry.get(project_id, worktree_id)
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Unknown project_id or worktree_id"},
            )
        root = Path(entry.container_root)
        if not root.is_dir():
            raise HTTPException(
                status_code=503,
                detail={"code": "unavailable", "message": "Worktree root is not mounted"},
            )
        allowed = self._settings.cbm_allowed_root
        if allowed:
            try:
                Path(os.path.realpath(root)).relative_to(Path(os.path.realpath(allowed)))
            except ValueError as exc:
                raise HTTPException(
                    status_code=422,
                    detail={"code": "invalid_scope", "message": "Worktree root outside allowed root"},
                ) from exc
        return entry

    def _row_to_job_response(self, row: dict[str, Any], *, deduplicated: bool = False) -> IndexJobResponse:
        return IndexJobResponse(
            job_id=row["job_id"],
            project_id=row["project_id"],
            worktree_id=row["worktree_id"],
            state=row["state"],
            mode=row["mode"],
            head_commit=row["head_commit"],
            dirty_fingerprint=row["dirty_fingerprint"],
            deduplicated=deduplicated or bool(row["deduplicated"]),
            no_op=bool(row["no_op"]),
            nodes=row["nodes"],
            edges=row["edges"],
            error=row["error"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def trigger_index(self, req: IndexRequest) -> IndexJobResponse:
        entry = await self._require_worktree(req.project_id, req.worktree_id)
        cbm_project = cbm_project_name(req.project_id, req.worktree_id)
        root = Path(entry.container_root)
        git_state = inspect_worktree_git(
            root,
            fallback_head=entry.head_commit,
            fallback_branch=entry.branch,
            fallback_dirty=entry.dirty_fingerprint,
            extra_excludes=entry.exclude,
        )

        # Deduplicate active job for the same (project_id, worktree_id)
        active = self._store.get_active_job(str(req.project_id), str(req.worktree_id))
        if active is not None:
            return self._row_to_job_response(active, deduplicated=True)

        async with self._get_scope_lock(cbm_project):
            active = self._store.get_active_job(str(req.project_id), str(req.worktree_id))
            if active is not None:
                return self._row_to_job_response(active, deduplicated=True)

            snapshot = self._store.get_snapshot(cbm_project)
            if (
                not req.force
                and snapshot is not None
                and snapshot["head_commit"] == git_state.head_commit
                and snapshot["dirty_fingerprint"] == git_state.dirty_fingerprint
            ):
                job_id = str(uuid.uuid4())
                row = self._store.create_job(
                    job_id=job_id,
                    project_id=str(req.project_id),
                    worktree_id=str(req.worktree_id),
                    cbm_project=cbm_project,
                    state="succeeded",
                    mode=req.mode,
                    head_commit=git_state.head_commit,
                    dirty_fingerprint=git_state.dirty_fingerprint,
                    no_op=True,
                    nodes=snapshot["nodes"],
                    edges=snapshot["edges"],
                )
                return self._row_to_job_response(row)

            job_id = str(uuid.uuid4())
            self._store.create_job(
                job_id=job_id,
                project_id=str(req.project_id),
                worktree_id=str(req.worktree_id),
                cbm_project=cbm_project,
                state="running",
                mode=req.mode,
                head_commit=git_state.head_commit,
                dirty_fingerprint=git_state.dirty_fingerprint,
            )

            try:
                res = await self._cbm.index_repository(
                    repo_path=str(root),
                    project_name=cbm_project,
                    mode=req.mode,
                    timeout=self._settings.index_timeout_sec,
                )
                nodes = int(res.get("nodes", 0))
                edges = int(res.get("edges", 0))
                self._store.upsert_snapshot(
                    cbm_project=cbm_project,
                    project_id=str(req.project_id),
                    worktree_id=str(req.worktree_id),
                    head_commit=git_state.head_commit,
                    dirty_fingerprint=git_state.dirty_fingerprint,
                    mode=req.mode,
                    nodes=nodes,
                    edges=edges,
                )
                updated = self._store.update_job(job_id, state="succeeded", nodes=nodes, edges=edges)
                assert updated is not None
                return self._row_to_job_response(updated)
            except (CBMClientError, asyncio.TimeoutError) as exc:
                self._store.update_job(job_id, state="failed", error=type(exc).__name__)
                raise HTTPException(
                    status_code=503,
                    detail={"code": "index_failed", "message": "Indexing backend failed"},
                ) from exc

    async def get_index_job(
        self, job_id: str, project_id: uuid.UUID, worktree_id: uuid.UUID
    ) -> IndexJobResponse:
        await self._require_worktree(project_id, worktree_id)
        row = self._store.get_job(job_id, str(project_id), str(worktree_id))
        if row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "Index job not found in scope"},
            )
        return self._row_to_job_response(row)

    async def search_code(self, req: SearchCodeRequest) -> SearchCodeResponse:
        started = time.perf_counter()
        entry = await self._require_worktree(req.project_id, req.worktree_id)
        cbm_project = cbm_project_name(req.project_id, req.worktree_id)
        root = Path(entry.container_root)

        git_state = inspect_worktree_git(
            root,
            fallback_head=entry.head_commit,
            fallback_branch=entry.branch,
            fallback_dirty=entry.dirty_fingerprint,
            extra_excludes=entry.exclude,
        )
        snapshot = self._store.get_snapshot(cbm_project)
        if snapshot is None:
            freshness: str = "unindexed"
            source_commit = git_state.head_commit
        elif (
            snapshot["head_commit"] == git_state.head_commit
            and snapshot["dirty_fingerprint"] == git_state.dirty_fingerprint
        ):
            freshness = "fresh"
            source_commit = snapshot["head_commit"]
        else:
            freshness = "stale"
            source_commit = snapshot["head_commit"]

        if freshness == "unindexed":
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            return SearchCodeResponse(
                items=[],
                truncated=False,
                index_freshness="unindexed",
                head_commit=git_state.head_commit,
                dirty_fingerprint=git_state.dirty_fingerprint,
                elapsed_ms=elapsed_ms,
            )

        escaped_pattern = f".*{re.escape(req.query)}.*"
        try:
            graph_res, code_res = await asyncio.gather(
                self._cbm.search_graph(
                    project_name=cbm_project,
                    name_pattern=escaped_pattern,
                    limit=req.limit,
                    timeout=self._settings.query_timeout_sec,
                ),
                self._cbm.search_code(
                    project_name=cbm_project,
                    pattern=req.query,
                    limit=req.limit,
                    timeout=self._settings.query_timeout_sec,
                ),
            )
        except (CBMClientError, asyncio.TimeoutError) as exc:
            raise HTTPException(
                status_code=503,
                detail={"code": "search_unavailable", "message": "Code search backend unavailable"},
            ) from exc

        items: list[SearchCodeItem] = []
        seen: set[tuple[str, int, str]] = set()
        upstream_truncated = bool(graph_res.get("truncated") or code_res.get("truncated"))

        def _try_add(raw_file: str, line_start: int, line_end: int, title: str, item_id: str) -> None:
            if len(items) >= req.limit:
                return
            try:
                rel_posix, resolved_file = resolve_safe_path(root, raw_file)
            except UnsafePathError:
                return
            if is_excluded_path(rel_posix, entry.exclude) or not resolved_file.is_file():
                return
            key = (rel_posix, line_start, title)
            if key in seen:
                return
            seen.add(key)
            excerpt = _read_excerpt(resolved_file, line_start, line_end)
            items.append(
                SearchCodeItem(
                    id=item_id,
                    path=rel_posix,
                    line_start=line_start,
                    line_end=line_end,
                    title=title,
                    excerpt=excerpt,
                    source_commit=source_commit,
                    state="indexed",
                )
            )

        # Parse search_graph groups: groups=[{qn_prefix, file, rows: [[name, label, lines, in, out]]}]
        for group in graph_res.get("groups") or []:
            raw_file = group.get("file", "")
            qn_prefix = group.get("qn_prefix", "")
            for row in group.get("rows") or []:
                if not row:
                    continue
                name = str(row[0])
                lines_val = row[2] if len(row) > 2 else "1-1"
                l_start, l_end = _parse_line_range(lines_val)
                qn = f"{qn_prefix}.{name}" if qn_prefix else name
                _try_add(raw_file, l_start, l_end, name, qn)

        # Parse search_code rows: cols=["qn","label","file","lines",...]
        cols = code_res.get("cols") or []
        col_idx = {c: i for i, c in enumerate(cols)}
        for row in code_res.get("rows") or []:
            if not isinstance(row, list):
                continue
            qn = str(row[col_idx["qn"]]) if "qn" in col_idx and len(row) > col_idx["qn"] else ""
            raw_file = str(row[col_idx["file"]]) if "file" in col_idx and len(row) > col_idx["file"] else ""
            lines_val = row[col_idx["lines"]] if "lines" in col_idx and len(row) > col_idx["lines"] else "1-1"
            l_start, l_end = _parse_line_range(lines_val)
            title = qn.rsplit(".", 1)[-1] if qn else Path(raw_file).name
            _try_add(raw_file, l_start, l_end, title, qn or f"{raw_file}:{l_start}")

        # Parse raw_matches fallback if still under limit
        raw_matches = code_res.get("raw_matches") or {}
        rm_cols = raw_matches.get("cols") or []
        rm_idx = {c: i for i, c in enumerate(rm_cols)}
        for row in raw_matches.get("rows") or []:
            if not isinstance(row, list):
                continue
            raw_file = str(row[rm_idx["file"]]) if "file" in rm_idx and len(row) > rm_idx["file"] else ""
            line_no = int(row[rm_idx["line"]]) if "line" in rm_idx and len(row) > rm_idx["line"] else 1
            _try_add(raw_file, line_no, line_no, Path(raw_file).name, f"{raw_file}:{line_no}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        resp = SearchCodeResponse(
            items=items,
            truncated=upstream_truncated or (len(items) >= req.limit and bool(graph_res.get("has_more") or code_res.get("has_more"))),
            index_freshness=freshness,  # type: ignore[arg-type]
            head_commit=git_state.head_commit,
            dirty_fingerprint=git_state.dirty_fingerprint,
            elapsed_ms=elapsed_ms,
        )
        return _enforce_budget(resp, req.max_chars)
