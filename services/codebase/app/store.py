"""Persistent SQLite WAL state store for index jobs and snapshot freshness."""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdapterStore:
    """Single-writer SQLite WAL store for codebase adapter metadata."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None

    def init(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        with conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS indexed_snapshots (
                    cbm_project TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    worktree_id TEXT NOT NULL,
                    head_commit TEXT NOT NULL,
                    dirty_fingerprint TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    nodes INTEGER NOT NULL DEFAULT 0,
                    edges INTEGER NOT NULL DEFAULT 0,
                    indexed_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS index_jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    worktree_id TEXT NOT NULL,
                    cbm_project TEXT NOT NULL,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    head_commit TEXT NOT NULL,
                    dirty_fingerprint TEXT NOT NULL,
                    deduplicated INTEGER NOT NULL DEFAULT 0,
                    no_op INTEGER NOT NULL DEFAULT 0,
                    nodes INTEGER,
                    edges INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_jobs_scope_state
                ON index_jobs (project_id, worktree_id, state)
                """
            )
            # Reconcile orphaned jobs interrupted by a container restart
            conn.execute(
                """
                UPDATE index_jobs
                SET state = 'failed', error = 'interrupted_by_restart', updated_at = ?
                WHERE state IN ('queued', 'running')
                """,
                (utc_now_iso(),),
            )
        self._conn = conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AdapterStore is not initialized")
        return self._conn

    def get_snapshot(self, cbm_project: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                "SELECT * FROM indexed_snapshots WHERE cbm_project = ?",
                (cbm_project,),
            ).fetchone()
            return dict(row) if row else None

    def upsert_snapshot(
        self,
        *,
        cbm_project: str,
        project_id: str,
        worktree_id: str,
        head_commit: str,
        dirty_fingerprint: str,
        mode: str,
        nodes: int,
        edges: int,
    ) -> None:
        now = utc_now_iso()
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO indexed_snapshots (
                        cbm_project, project_id, worktree_id, head_commit,
                        dirty_fingerprint, mode, nodes, edges, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cbm_project) DO UPDATE SET
                        head_commit = excluded.head_commit,
                        dirty_fingerprint = excluded.dirty_fingerprint,
                        mode = excluded.mode,
                        nodes = excluded.nodes,
                        edges = excluded.edges,
                        indexed_at = excluded.indexed_at
                    """,
                    (
                        cbm_project,
                        project_id,
                        worktree_id,
                        head_commit,
                        dirty_fingerprint,
                        mode,
                        nodes,
                        edges,
                        now,
                    ),
                )

    def get_active_job(self, project_id: str, worktree_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                """
                SELECT * FROM index_jobs
                WHERE project_id = ? AND worktree_id = ? AND state IN ('queued', 'running')
                ORDER BY created_at DESC LIMIT 1
                """,
                (project_id, worktree_id),
            ).fetchone()
            return dict(row) if row else None

    def get_job(self, job_id: str, project_id: str, worktree_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._require_conn().execute(
                """
                SELECT * FROM index_jobs
                WHERE job_id = ? AND project_id = ? AND worktree_id = ?
                """,
                (job_id, project_id, worktree_id),
            ).fetchone()
            return dict(row) if row else None

    def create_job(
        self,
        *,
        job_id: str,
        project_id: str,
        worktree_id: str,
        cbm_project: str,
        state: str,
        mode: str,
        head_commit: str,
        dirty_fingerprint: str,
        no_op: bool = False,
        nodes: int | None = None,
        edges: int | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute(
                    """
                    INSERT INTO index_jobs (
                        job_id, project_id, worktree_id, cbm_project, state, mode,
                        head_commit, dirty_fingerprint, deduplicated, no_op,
                        nodes, edges, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        job_id,
                        project_id,
                        worktree_id,
                        cbm_project,
                        state,
                        mode,
                        head_commit,
                        dirty_fingerprint,
                        1 if no_op else 0,
                        nodes,
                        edges,
                        now,
                        now,
                    ),
                )
            row = conn.execute("SELECT * FROM index_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row)

    def update_job(
        self,
        job_id: str,
        *,
        state: str,
        nodes: int | None = None,
        edges: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        now = utc_now_iso()
        with self._lock:
            conn = self._require_conn()
            with conn:
                conn.execute(
                    """
                    UPDATE index_jobs
                    SET state = ?, nodes = COALESCE(?, nodes), edges = COALESCE(?, edges),
                        error = ?, updated_at = ?
                    WHERE job_id = ?
                    """,
                    (state, nodes, edges, error, now, job_id),
                )
            row = conn.execute("SELECT * FROM index_jobs WHERE job_id = ?", (job_id,)).fetchone()
            return dict(row) if row else None
