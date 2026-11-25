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

from .trace_schema import TRACE_SQL
from .trace_lifecycle import agent_finished
from .v3_schema import V3_CONV_SQL, V3_MCP_SQL

CURRENT_SCHEMA_VERSION = 6

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
    (
        2,
        "add_events_source_agent",
        """
        ALTER TABLE events ADD COLUMN source_agent TEXT;
        CREATE INDEX IF NOT EXISTS idx_events_agent_status ON events(project_id, worktree_id, source_agent, created_at);
        """,
    ),
    (
        3,
        "v2_orchestrator_and_session_analytics",
        """
        CREATE TABLE IF NOT EXISTS v2_catalog_snapshots (
            id TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            source_urls_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_stale INTEGER NOT NULL DEFAULT 0,
            checksum TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_models (
            id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES v2_catalog_snapshots(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            cli_family TEXT,
            cli_version TEXT,
            auth_mode TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('candidate', 'verified', 'deprecated', 'unavailable')),
            context_limit INTEGER,
            output_limit INTEGER,
            capabilities_json TEXT NOT NULL DEFAULT '{}',
            price_input_per_1m REAL,
            price_output_per_1m REAL,
            price_cache_read_per_1m REAL,
            price_cache_write_per_1m REAL,
            currency TEXT,
            effective_date TEXT,
            source_url TEXT,
            verified_at TEXT,
            expires_at TEXT,
            UNIQUE(snapshot_id, provider, model_id, auth_mode)
        );
        CREATE INDEX IF NOT EXISTS idx_v2_models_lookup ON v2_models(provider, model_id, status);

        CREATE TABLE IF NOT EXISTS v2_model_evaluations (
            id TEXT PRIMARY KEY,
            project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
            model_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            effort TEXT,
            task_type TEXT NOT NULL,
            complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
            evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('observed', 'synthetic_benchmark', 'hypothesis')),
            correctness_score REAL,
            acceptance_rate REAL,
            avg_latency_ms REAL,
            avg_effective_cost_usd REAL,
            failure_rate REAL,
            sample_count INTEGER NOT NULL DEFAULT 0,
            provenance_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v2_eval_lookup ON v2_model_evaluations(task_type, complexity, model_id);

        CREATE TABLE IF NOT EXISTS v2_policies (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            approval_mode TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED' CHECK(approval_mode IN ('REVIEW_REQUIRED', 'AUTO_SCOPED')),
            auto_expires_at TEXT,
            auto_persist_across_restart INTEGER NOT NULL DEFAULT 0,
            kill_switch INTEGER NOT NULL DEFAULT 0,
            max_concurrency INTEGER NOT NULL DEFAULT 2,
            max_task_cost_usd REAL,
            max_daily_cost_usd REAL,
            max_daily_planner_cost_usd REAL,
            allow_paid_api_fallback INTEGER NOT NULL DEFAULT 0,
            allowed_auth_modes_json TEXT NOT NULL DEFAULT '["subscription_cli"]',
            retention_days INTEGER NOT NULL DEFAULT 180,
            allow_cross_project_aggregates INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_brain_roles (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            role_name TEXT NOT NULL CHECK(role_name IN ('tier0_rules', 'tier1_light', 'tier2_primary', 'tier3_advanced', 'secondary_fallback')),
            preferred_label TEXT,
            effective_provider TEXT,
            effective_model_id TEXT,
            effective_effort TEXT,
            auth_mode TEXT,
            availability_status TEXT NOT NULL DEFAULT 'unverified',
            availability_reason TEXT,
            fallback_order_json TEXT NOT NULL DEFAULT '[]',
            max_escalations INTEGER NOT NULL DEFAULT 1,
            last_verified_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(project_id, role_name)
        );

        CREATE TABLE IF NOT EXISTS v2_plans (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            objective TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('DRAFT', 'REVIEW_REQUIRED', 'APPROVED', 'RUNNING', 'PAUSED', 'COMPLETED', 'FAILED', 'CANCELLED', 'REJECTED')),
            current_revision_id TEXT,
            approved_revision_id TEXT,
            catalog_snapshot_id TEXT REFERENCES v2_catalog_snapshots(id),
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v2_plans_scope ON v2_plans(project_id, worktree_id, status);

        CREATE TABLE IF NOT EXISTS v2_plan_revisions (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
            revision_number INTEGER NOT NULL,
            revision_hash TEXT NOT NULL,
            plan_json TEXT NOT NULL,
            forecast_id TEXT,
            planner_session_id TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(plan_id, revision_number)
        );

        CREATE TABLE IF NOT EXISTS v2_plan_approvals (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
            revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id) ON DELETE CASCADE,
            revision_hash TEXT NOT NULL,
            decision TEXT NOT NULL CHECK(decision IN ('approved', 'rejected', 'revoked')),
            actor TEXT NOT NULL,
            mode TEXT NOT NULL CHECK(mode IN ('manual', 'auto_scoped')),
            reason TEXT,
            expires_at TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_tasks (
            id TEXT PRIMARY KEY,
            plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
            revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            task_key TEXT NOT NULL,
            title TEXT NOT NULL,
            objective TEXT NOT NULL,
            task_type TEXT NOT NULL,
            complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
            assigned_agent TEXT NOT NULL,
            assigned_provider TEXT NOT NULL,
            assigned_model_id TEXT NOT NULL,
            assigned_effort TEXT,
            auth_mode TEXT NOT NULL,
            owned_paths_json TEXT NOT NULL DEFAULT '[]',
            acceptance_checks_json TEXT NOT NULL DEFAULT '[]',
            budget_input_tokens INTEGER,
            budget_output_tokens INTEGER,
            budget_cost_usd REAL,
            max_retries INTEGER NOT NULL DEFAULT 2,
            state TEXT NOT NULL CHECK(state IN ('queued', 'leased', 'running', 'checkpointed', 'succeeded', 'failed', 'cancelled', 'blocked')),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v2_tasks_plan ON v2_tasks(plan_id, revision_id, state);

        CREATE TABLE IF NOT EXISTS v2_task_dependencies (
            task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
            depends_on_task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
            PRIMARY KEY(task_id, depends_on_task_id)
        );

        CREATE TABLE IF NOT EXISTS v2_task_attempts (
            id TEXT PRIMARY KEY,
            task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            plan_revision_id TEXT NOT NULL REFERENCES v2_plan_revisions(id),
            plan_revision_hash TEXT NOT NULL,
            runner_id TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('claimed', 'running', 'checkpointed', 'succeeded', 'failed', 'cancelled', 'budget_exceeded', 'orphaned')),
            started_at TEXT NOT NULL,
            ended_at TEXT,
            error_code TEXT,
            error_summary TEXT,
            UNIQUE(task_id, attempt_number)
        );

        CREATE TABLE IF NOT EXISTS v2_job_leases (
            lease_id TEXT PRIMARY KEY,
            attempt_id TEXT NOT NULL UNIQUE REFERENCES v2_task_attempts(id) ON DELETE CASCADE,
            task_id TEXT NOT NULL REFERENCES v2_tasks(id) ON DELETE CASCADE,
            runner_id TEXT NOT NULL,
            claimed_at TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            released_at TEXT
        );

        CREATE TABLE IF NOT EXISTS v2_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            plan_id TEXT REFERENCES v2_plans(id) ON DELETE SET NULL,
            plan_revision_id TEXT REFERENCES v2_plan_revisions(id) ON DELETE SET NULL,
            task_id TEXT REFERENCES v2_tasks(id) ON DELETE SET NULL,
            attempt_id TEXT REFERENCES v2_task_attempts(id) ON DELETE SET NULL,
            parent_session_id TEXT REFERENCES v2_sessions(id) ON DELETE SET NULL,
            role TEXT NOT NULL CHECK(role IN ('planner', 'executor', 'reviewer', 'manual_cli')),
            provider TEXT NOT NULL,
            model_id TEXT NOT NULL,
            cli_version TEXT,
            reasoning_effort TEXT,
            auth_mode TEXT NOT NULL,
            account_bucket TEXT,
            upstream_session_id TEXT,
            git_head TEXT,
            dirty_fingerprint TEXT,
            status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'checkpointed', 'failed', 'cancelled', 'partial')),
            unlinked INTEGER NOT NULL DEFAULT 0,
            incomplete_coverage INTEGER NOT NULL DEFAULT 0,
            started_at TEXT NOT NULL,
            ended_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_v2_sessions_cohort ON v2_sessions(project_id, provider, model_id, auth_mode, started_at);

        CREATE TABLE IF NOT EXISTS v2_task_features (
            id TEXT PRIMARY KEY,
            session_id TEXT REFERENCES v2_sessions(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES v2_tasks(id) ON DELETE CASCADE,
            phase TEXT NOT NULL CHECK(phase IN ('planned', 'actual')),
            rubric_version TEXT NOT NULL,
            task_type TEXT NOT NULL,
            complexity TEXT NOT NULL CHECK(complexity IN ('XS', 'S', 'M', 'L', 'XL')),
            language TEXT,
            file_count INTEGER,
            total_file_bytes INTEGER,
            expected_edits INTEGER,
            expected_tests INTEGER,
            dependency_count INTEGER,
            context_pack_tokens INTEGER,
            context_pack_is_approx INTEGER NOT NULL DEFAULT 1,
            uncertainty_level TEXT CHECK(uncertainty_level IN ('low', 'medium', 'high')),
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_session_outcomes (
            session_id TEXT PRIMARY KEY REFERENCES v2_sessions(id) ON DELETE CASCADE,
            accepted INTEGER,
            tests_passed INTEGER,
            duration_ms INTEGER,
            retries INTEGER NOT NULL DEFAULT 0,
            rework_count INTEGER NOT NULL DEFAULT 0,
            actual_changed_files INTEGER,
            peak_context_tokens INTEGER,
            handoff_summary TEXT,
            recorded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_usage_events (
            id TEXT PRIMARY KEY,
            source_event_id TEXT NOT NULL,
            session_id TEXT NOT NULL REFERENCES v2_sessions(id) ON DELETE CASCADE,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE CASCADE,
            sequence_no INTEGER NOT NULL DEFAULT 0,
            counter_mode TEXT NOT NULL CHECK(counter_mode IN ('delta', 'cumulative')),
            measurement_kind TEXT NOT NULL CHECK(measurement_kind IN ('measured', 'estimated', 'unavailable')),
            input_tokens INTEGER,
            output_tokens INTEGER,
            cache_read_tokens INTEGER,
            cache_write_tokens INTEGER,
            reasoning_tokens INTEGER,
            cost_usd REAL,
            pricing_version TEXT,
            unit TEXT NOT NULL DEFAULT 'tokens',
            source TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            UNIQUE(session_id, source_event_id)
        );
        CREATE INDEX IF NOT EXISTS idx_v2_usage_session ON v2_usage_events(session_id, sequence_no);

        CREATE TABLE IF NOT EXISTS v2_quota_snapshots (
            id TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            account_bucket TEXT,
            quota_kind TEXT NOT NULL CHECK(quota_kind IN ('subscription_quota', 'api_budget', 'rate_limit', 'context_window')),
            unit TEXT NOT NULL,
            numerator REAL,
            denominator REAL,
            window_label TEXT,
            reset_at TEXT,
            source TEXT NOT NULL,
            confounded INTEGER NOT NULL DEFAULT 0,
            observed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v2_quotas_latest ON v2_quota_snapshots(provider, auth_mode, quota_kind, observed_at DESC);

        CREATE TABLE IF NOT EXISTS v2_budget_reservations (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            plan_id TEXT NOT NULL REFERENCES v2_plans(id) ON DELETE CASCADE,
            task_id TEXT REFERENCES v2_tasks(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            auth_mode TEXT NOT NULL,
            reserved_cost_usd REAL,
            reserved_tokens INTEGER,
            actual_cost_usd REAL,
            actual_tokens INTEGER,
            status TEXT NOT NULL CHECK(status IN ('reserved', 'settled', 'released')),
            created_at TEXT NOT NULL,
            settled_at TEXT
        );

        CREATE TABLE IF NOT EXISTS v2_forecast_snapshots (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            plan_id TEXT REFERENCES v2_plans(id) ON DELETE SET NULL,
            plan_revision_id TEXT REFERENCES v2_plan_revisions(id) ON DELETE SET NULL,
            forecast_version TEXT NOT NULL,
            confidence TEXT NOT NULL CHECK(confidence IN ('high', 'medium', 'low', 'cold_start', 'uncalibrated')),
            sample_count INTEGER NOT NULL,
            cohort_criteria_json TEXT NOT NULL,
            forecast_json TEXT NOT NULL,
            assumptions_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_forecast_actuals (
            forecast_id TEXT PRIMARY KEY REFERENCES v2_forecast_snapshots(id) ON DELETE CASCADE,
            actual_input_tokens INTEGER,
            actual_output_tokens INTEGER,
            actual_cache_read_tokens INTEGER,
            actual_cache_write_tokens INTEGER,
            actual_total_tokens INTEGER,
            actual_cost_usd REAL,
            within_p90 INTEGER,
            abs_token_error INTEGER,
            reconciled_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS v2_audit_log (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            actor TEXT NOT NULL,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_v2_audit_time ON v2_audit_log(project_id, created_at DESC);
        """,
    ),
    (4, "execution_traces_v22", TRACE_SQL),
    (5, "dynamic_mcp_registry_v30", V3_MCP_SQL),
    (6, "conversation_history_fts_v32", V3_CONV_SQL),
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def reconcile_expired_job_leases(conn: sqlite3.Connection, now: str) -> int:
    """Caller owns write transaction. Unknown outcomes must never auto-requeue."""
    rows = conn.execute(
        """SELECT l.lease_id, l.attempt_id, l.task_id, t.plan_id
           FROM v2_job_leases l JOIN v2_tasks t ON t.id = l.task_id
           WHERE l.released_at IS NULL AND julianday(l.expires_at) <= julianday(?)""",
        (now,),
    ).fetchall()
    for row in rows:
        lease_id, attempt_id, task_id, plan_id = row
        conn.execute("UPDATE v2_job_leases SET released_at = ? WHERE lease_id = ?", (now, lease_id))
        conn.execute(
            """UPDATE v2_task_attempts SET status = 'orphaned', ended_at = ?,
               error_code = 'lease_expired', error_summary = 'Outcome unknown; review before retry'
               WHERE id = ? AND status IN ('claimed', 'running')""",
            (now, attempt_id),
        )
        conn.execute(
            """UPDATE v2_sessions SET status = 'partial', incomplete_coverage = 1, ended_at = ?
               WHERE attempt_id = ? AND status = 'running'""", (now, attempt_id),
        )
        changed = conn.execute(
            "UPDATE v2_tasks SET state = 'blocked', updated_at = ? WHERE id = ? AND state IN ('leased', 'running')",
            (now, task_id),
        ).rowcount
        if changed:
            conn.execute(
                "UPDATE v2_plans SET status = 'PAUSED', updated_at = ? WHERE id = ? AND status IN ('APPROVED', 'RUNNING')",
                (now, plan_id),
            )
        agent_finished(conn, attempt_id, "unknown", now, "lease_expired", "Outcome unknown; review before retry")
        # Preserve budget reservation: usage/outcome is not known.
    return len(rows)


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
                self._reconcile_on_startup(conn)
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
                # Strip SQL line comments and split statements safely inside transaction
                stripped_lines = [line for line in sql_script.splitlines() if not line.strip().startswith("--")]
                for stmt in "\n".join(stripped_lines).split(";"):
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

    def _reconcile_on_startup(self, conn: sqlite3.Connection) -> None:
        """Reset non-persistent auto mode; preserve live leases and quarantine expired attempts."""
        now = utc_now_iso()
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                """
                UPDATE v2_policies
                SET approval_mode = 'REVIEW_REQUIRED', auto_expires_at = NULL, updated_at = ?
                WHERE approval_mode = 'AUTO_SCOPED' AND auto_persist_across_restart = 0
                """,
                (now,),
            )
            reconcile_expired_job_leases(conn, now)
            # Keep valid managed leases across Bridge restarts. Unleased external
            # sessions have no live proof after restart.
            conn.execute(
                """UPDATE v2_sessions SET status = 'partial', incomplete_coverage = 1, ended_at = ?
                   WHERE status = 'running' AND NOT EXISTS (
                     SELECT 1 FROM v2_job_leases l
                     WHERE l.attempt_id = v2_sessions.attempt_id AND l.released_at IS NULL
                       AND julianday(l.expires_at) > julianday(?)
                   )""",
                (now, now),
            )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise

    @staticmethod
    def expire_leases(conn: sqlite3.Connection, now: str) -> None:
        reconcile_expired_job_leases(conn, now)

    def reconcile_expired_leases(self) -> None:
        with self.write_tx() as conn:
            self.expire_leases(conn, utc_now_iso())

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
