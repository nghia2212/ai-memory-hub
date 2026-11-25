"""Sole SQLite writer, migrations, FTS5 unicode61, online backup and verified restore."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .errors import BridgeError

CURRENT_SCHEMA_VERSION = 1

MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial_v1_schema",
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            canonical_identity TEXT NOT NULL UNIQUE,
            policy_json TEXT NOT NULL DEFAULT '{}',
            hermes_approved INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS worktrees (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            canonical_root TEXT NOT NULL UNIQUE,
            branch TEXT,
            head_commit TEXT,
            is_detached INTEGER NOT NULL DEFAULT 0,
            dirty_fingerprint TEXT NOT NULL DEFAULT '',
            mount_status TEXT NOT NULL DEFAULT 'pending_host_setup',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_worktrees_project ON worktrees(project_id);

        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT REFERENCES worktrees(id) ON DELETE CASCADE,
            type TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            source_agent TEXT NOT NULL,
            source_commit TEXT,
            evidence_ref TEXT,
            state TEXT NOT NULL CHECK(state IN ('candidate', 'verified', 'superseded')),
            supersedes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_memories_scope_state ON memories(project_id, worktree_id, state);

        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            memory_id UNINDEXED,
            project_id UNINDEXED,
            worktree_id UNINDEXED,
            title,
            body,
            tags,
            tokenize='unicode61'
        );

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            approved INTEGER NOT NULL DEFAULT 0,
            redacted_payload_json TEXT,
            processed_by_worker INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            UNIQUE(project_id, worktree_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_events_worker ON events(approved, processed_by_worker, project_id);

        CREATE TABLE IF NOT EXISTS handoffs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            source_agent TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN ('candidate', 'verified', 'superseded')),
            idempotency_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id, worktree_id, idempotency_key)
        );
        CREATE INDEX IF NOT EXISTS idx_handoffs_task ON handoffs(project_id, worktree_id, task_id);

        CREATE TABLE IF NOT EXISTS idempotency_records (
            scope_key TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            response_json TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(scope_key, idempotency_key)
        );

        CREATE TABLE IF NOT EXISTS metrics_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            project_id TEXT,
            worktree_id TEXT,
            latency_ms REAL NOT NULL,
            result_count INTEGER NOT NULL DEFAULT 0,
            result_chars INTEGER NOT NULL DEFAULT 0,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cost_usd REAL,
            measured INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS backups (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            size_bytes INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS index_jobs (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            head_commit TEXT,
            dirty_fingerprint TEXT,
            stale INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        """,
    ),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class DatabaseManager:
    """Thread-safe sole SQLite writer with WAL, migrations, online backup and atomic restore."""

    def __init__(self, db_path: Path, backup_dir: Path) -> None:
        self.db_path = db_path
        self.backup_dir = backup_dir
        self._lock = threading.RLock()
        self._maintenance_lock = False
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready and not self._maintenance_lock

    def initialize(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                self._apply_pragmas(conn)
                self._run_migrations(conn)
            finally:
                conn.close()
            self._ready = True

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _apply_pragmas(self, conn: sqlite3.Connection) -> None:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")

    def _run_migrations(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);"
        )
        applied = {
            row["version"]
            for row in conn.execute("SELECT version FROM schema_migrations").fetchall()
        }
        for version, name, sql_script in MIGRATIONS:
            if version in applied:
                continue
            conn.execute("BEGIN IMMEDIATE;")
            try:
                # executescript commits implicitly if not careful; split statements instead
                for stmt in sql_script.split(";"):
                    cleaned = stmt.strip()
                    if cleaned:
                        conn.execute(cleaned)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                    (version, name, utc_now_iso()),
                )
                conn.execute("COMMIT;")
            except Exception:
                conn.execute("ROLLBACK;")
                raise

    def check_health(self) -> tuple[bool, int]:
        if not self.is_ready:
            return False, 0
        try:
            with self.read_conn() as conn:
                row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
                version = int(row["v"] or 0) if row else 0
                return version >= CURRENT_SCHEMA_VERSION, version
        except Exception:
            return False, 0

    @contextmanager
    def read_conn(self) -> Iterator[sqlite3.Connection]:
        if self._maintenance_lock:
            raise BridgeError(503, "maintenance_lock", "Database is temporarily locked for restore")
        with self._lock:
            conn = self._connect()
            try:
                self._apply_pragmas(conn)
                yield conn
            finally:
                conn.close()

    @contextmanager
    def write_tx(self) -> Iterator[sqlite3.Connection]:
        if self._maintenance_lock:
            raise BridgeError(503, "maintenance_lock", "Database is temporarily locked for restore")
        with self._lock:
            conn = self._connect()
            try:
                self._apply_pragmas(conn)
                conn.execute("BEGIN IMMEDIATE;")
                yield conn
                conn.execute("COMMIT;")
            except Exception:
                try:
                    conn.execute("ROLLBACK;")
                except sqlite3.Error:
                    pass
                raise
            finally:
                conn.close()

    def create_backup(self) -> dict[str, object]:
        with self._lock:
            if self._maintenance_lock:
                raise BridgeError(503, "maintenance_lock", "Database is temporarily locked for restore")
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            backup_id = str(uuid.uuid4())
            filename = f"{backup_id}.db"
            dest_path = self.backup_dir / filename
            manifest_path = self.backup_dir / f"{backup_id}.manifest.json"

            src_conn = self._connect()
            try:
                self._apply_pragmas(src_conn)
                row = src_conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
                schema_version = int(row["v"] or 0) if row else 0
                dst_conn = sqlite3.connect(str(dest_path))
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()

            sha256 = _sha256_file(dest_path)
            size_bytes = dest_path.stat().st_size
            created_at = utc_now_iso()
            manifest = {
                "backup_id": backup_id,
                "sha256": sha256,
                "schema_version": schema_version,
                "size_bytes": size_bytes,
                "created_at": created_at,
            }
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

            with self.write_tx() as conn:
                conn.execute(
                    "INSERT INTO backups (id, filename, sha256, schema_version, size_bytes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (backup_id, filename, sha256, schema_version, size_bytes, created_at),
                )
            return manifest

    def list_backups(self) -> list[dict[str, object]]:
        with self.read_conn() as conn:
            rows = conn.execute(
                "SELECT id AS backup_id, sha256, schema_version, size_bytes, created_at FROM backups ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def restore_backup(self, backup_id: str) -> dict[str, object]:
        with self._lock:
            # Look up backup metadata or manifest
            backup_file = self.backup_dir / f"{backup_id}.db"
            manifest_file = self.backup_dir / f"{backup_id}.manifest.json"
            if not backup_file.exists() or not manifest_file.exists():
                raise BridgeError(404, "backup_not_found", "Backup ID not found")

            manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            expected_sha = manifest.get("sha256")
            actual_sha = _sha256_file(backup_file)
            if not expected_sha or actual_sha != expected_sha:
                raise BridgeError(422, "backup_corrupted", "Backup checksum mismatch")

            # Verify integrity of backup file before touching active DB
            try:
                chk_conn = sqlite3.connect(str(backup_file))
                try:
                    chk_row = chk_conn.execute("PRAGMA integrity_check;").fetchone()
                    if not chk_row or chk_row[0] != "ok":
                        raise BridgeError(422, "backup_corrupted", "Backup failed SQLite integrity check")
                    ver_row = chk_conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
                    if not ver_row or int(ver_row[0] or 0) < 1:
                        raise BridgeError(422, "backup_corrupted", "Backup missing valid schema_migrations")
                finally:
                    chk_conn.close()
            except BridgeError:
                raise
            except sqlite3.Error as exc:
                raise BridgeError(422, "backup_corrupted", f"Backup unreadable: {exc.__class__.__name__}") from exc

            self._maintenance_lock = True
            safety_copy = self.db_path.with_suffix(".pre_restore.bak")
            try:
                # Save pre-restore safety snapshot via SQLite online backup API
                if self.db_path.exists():
                    cur_conn = self._connect()
                    try:
                        saf_conn = sqlite3.connect(str(safety_copy))
                        try:
                            cur_conn.backup(saf_conn)
                        finally:
                            saf_conn.close()
                    finally:
                        cur_conn.close()

                # Restore from verified backup into active DB via SQLite online backup API
                bkp_conn = sqlite3.connect(str(backup_file))
                try:
                    dst_conn = self._connect()
                    try:
                        bkp_conn.backup(dst_conn)
                        self._apply_pragmas(dst_conn)
                        res_chk = dst_conn.execute("PRAGMA integrity_check;").fetchone()
                        if not res_chk or res_chk[0] != "ok":
                            raise RuntimeError("Restored DB failed integrity check")
                        self._run_migrations(dst_conn)
                    finally:
                        dst_conn.close()
                finally:
                    bkp_conn.close()
            except Exception as exc:
                # Rollback from pre-restore safety snapshot
                if safety_copy.exists():
                    saf_conn = sqlite3.connect(str(safety_copy))
                    try:
                        dst_conn = self._connect()
                        try:
                            saf_conn.backup(dst_conn)
                        finally:
                            dst_conn.close()
                    finally:
                        saf_conn.close()
                if isinstance(exc, BridgeError):
                    raise
                raise BridgeError(500, "restore_failed", "Restore failed and rolled back cleanly") from exc
            finally:
                if safety_copy.exists():
                    try:
                        os.remove(safety_copy)
                    except OSError:
                        pass
                self._maintenance_lock = False

            return {
                "restored": True,
                "backup_id": backup_id,
                "sha256": actual_sha,
                "schema_version": manifest.get("schema_version"),
            }
