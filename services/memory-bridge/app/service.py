"""Core business logic for Memory Bridge V1."""
from __future__ import annotations

import json
import time
import uuid
from typing import Any

import httpx

from .config import Settings
from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .schemas import (
    CodeSearchRequest,
    EventCreateRequest,
    HandoffCreateRequest,
    IndexTriggerRequest,
    MemoryCreateRequest,
    MemorySearchRequest,
    MemoryVerifyRequest,
    ProjectCreateRequest,
    WorkerResultRequest,
    WorktreeCreateRequest,
)
from .security import (
    AuthPrincipal,
    compute_payload_hash,
    redact_payload,
    redact_secrets,
    tokenize_fts_query,
    validate_safe_worktree_root,
)


def _enforce_serialized_budget(
    items: list[dict[str, Any]],
    max_chars: int,
    index_freshness: dict[str, Any] | None = None,
    already_truncated: bool = False,
) -> dict[str, Any]:
    """Strictly bound serialized JSON envelope to <= max_chars Unicode characters."""
    truncated = already_truncated
    current_items = [dict(item) for item in items]

    def build_envelope(lst: list[dict[str, Any]], is_trunc: bool) -> dict[str, Any]:
        env: dict[str, Any] = {"items": lst, "truncated": is_trunc}
        if index_freshness is not None:
            env["index_freshness"] = index_freshness
        return env

    def serialized_len(env: dict[str, Any]) -> int:
        return len(json.dumps(env, ensure_ascii=False))

    envelope = build_envelope(current_items, truncated)
    if serialized_len(envelope) <= max_chars:
        return envelope

    truncated = True
    # Drop trailing items until it fits or only 1 item remains
    while len(current_items) > 1 and serialized_len(build_envelope(current_items, True)) > max_chars:
        current_items.pop()

    # If single item still exceeds max_chars, truncate its excerpt
    if len(current_items) == 1:
        item = current_items[0]
        excerpt = str(item.get("excerpt", ""))
        while excerpt and serialized_len(build_envelope(current_items, True)) > max_chars:
            excess = serialized_len(build_envelope(current_items, True)) - max_chars
            cut = max(1, min(len(excerpt), excess + 16))
            excerpt = excerpt[:-cut]
            item["excerpt"] = (excerpt + "…") if excerpt else ""
        if serialized_len(build_envelope(current_items, True)) > max_chars:
            item["excerpt"] = ""
        if serialized_len(build_envelope(current_items, True)) > max_chars:
            current_items = []

    return build_envelope(current_items, True)


class MemoryBridgeService:
    def __init__(self, db: DatabaseManager, settings: Settings) -> None:
        self.db = db
        self.settings = settings

    # --- Scope helpers ---
    def _ensure_project(self, conn: Any, project_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise BridgeError(404, "project_not_found", "Project not found")
        return dict(row)

    def _ensure_worktree(self, conn: Any, project_id: str, worktree_id: str) -> dict[str, Any]:
        self._ensure_project(conn, project_id)
        row = conn.execute(
            "SELECT * FROM worktrees WHERE id = ? AND project_id = ?",
            (worktree_id, project_id),
        ).fetchone()
        if not row:
            raise BridgeError(404, "worktree_not_found", "Worktree not found for project")
        return dict(row)

    def _check_idempotency(
        self, conn: Any, scope_key: str, idempotency_key: str, payload_hash: str
    ) -> tuple[int, dict[str, Any]] | None:
        row = conn.execute(
            "SELECT payload_hash, response_json, status_code FROM idempotency_records WHERE scope_key = ? AND idempotency_key = ?",
            (scope_key, idempotency_key),
        ).fetchone()
        if not row:
            return None
        if row["payload_hash"] != payload_hash:
            raise BridgeError(409, "idempotency_conflict", "Idempotency key reused with different payload")
        return int(row["status_code"]), json.loads(row["response_json"])

    def _save_idempotency(
        self,
        conn: Any,
        scope_key: str,
        idempotency_key: str,
        payload_hash: str,
        status_code: int,
        response: dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO idempotency_records (scope_key, idempotency_key, payload_hash, response_json, status_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                scope_key,
                idempotency_key,
                payload_hash,
                json.dumps(response, ensure_ascii=False),
                status_code,
                utc_now_iso(),
            ),
        )

    def _record_metric(
        self,
        endpoint: str,
        latency_ms: float,
        project_id: str | None = None,
        worktree_id: str | None = None,
        result_count: int = 0,
        result_chars: int = 0,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_read_tokens: int | None = None,
        cost_usd: float | None = None,
    ) -> None:
        measured = 1 if any(v is not None for v in (input_tokens, output_tokens, cache_read_tokens, cost_usd)) else 0
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO metrics_events (
                    endpoint, project_id, worktree_id, latency_ms, result_count, result_chars,
                    input_tokens, output_tokens, cache_read_tokens, cost_usd, measured, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint,
                    project_id,
                    worktree_id,
                    round(latency_ms, 3),
                    result_count,
                    result_chars,
                    input_tokens,
                    output_tokens,
                    cache_read_tokens,
                    cost_usd,
                    measured,
                    utc_now_iso(),
                ),
            )

    # --- Projects & Worktrees ---
    def list_projects(self) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            rows = conn.execute(
                "SELECT id, name, canonical_identity, policy_json, hermes_approved, created_at, updated_at FROM projects ORDER BY created_at DESC"
            ).fetchall()
            items = [
                {
                    "project_id": r["id"],
                    "name": r["name"],
                    "canonical_identity": r["canonical_identity"],
                    "policy": json.loads(r["policy_json"]),
                    "hermes_approved": bool(r["hermes_approved"]),
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
            return {"items": items}

    def get_agents_status(self, project_id: str, worktree_id: str | None = None) -> dict[str, Any]:
        """Aggregate latest observed AI agent status per (project_id, worktree_id, source_agent)."""
        with self.db.read_conn() as conn:
            if worktree_id:
                self._ensure_worktree(conn, project_id, worktree_id)
                wt_clause = "AND e.worktree_id = ?"
                params: list[Any] = [project_id, worktree_id]
            else:
                self._ensure_project(conn, project_id)
                wt_clause = ""
                params = [project_id]

            rows = conn.execute(
                f"""
                SELECT
                    e.source_agent,
                    e.kind AS last_kind,
                    e.task_id,
                    e.worktree_id,
                    w.branch,
                    w.head_commit,
                    e.created_at AS last_seen_at
                FROM events e
                JOIN worktrees w ON w.id = e.worktree_id
                WHERE e.project_id = ? {wt_clause} AND e.source_agent IS NOT NULL AND e.source_agent != ''
                ORDER BY e.created_at DESC
                """,
                params,
            ).fetchall()

            seen: set[tuple[str, str]] = set()
            items: list[dict[str, Any]] = []
            for r in rows:
                key = (r["worktree_id"], r["source_agent"])
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "source_agent": r["source_agent"],
                        "last_kind": r["last_kind"],
                        "task_id": r["task_id"],
                        "worktree_id": r["worktree_id"],
                        "branch": r["branch"],
                        "head_commit": r["head_commit"],
                        "last_seen_at": r["last_seen_at"],
                    }
                )
            return {"items": items}

    def create_project(self, req: ProjectCreateRequest) -> dict[str, Any]:
        now = utc_now_iso()
        project_id = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            existing = conn.execute(
                "SELECT id FROM projects WHERE canonical_identity = ?",
                (req.canonical_identity,),
            ).fetchone()
            if existing:
                raise BridgeError(409, "project_exists", "Project with this canonical identity already exists")
            conn.execute(
                """
                INSERT INTO projects (id, name, canonical_identity, policy_json, hermes_approved, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    project_id,
                    req.name,
                    req.canonical_identity,
                    json.dumps(req.policy, ensure_ascii=False),
                    1 if req.hermes_approved else 0,
                    now,
                    now,
                ),
            )
        return {
            "project_id": project_id,
            "name": req.name,
            "canonical_identity": req.canonical_identity,
            "policy": req.policy,
            "hermes_approved": req.hermes_approved,
            "created_at": now,
            "updated_at": now,
        }

    def update_project_hermes(self, project_id: str, hermes_approved: bool) -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            proj = self._ensure_project(conn, project_id)
            conn.execute(
                "UPDATE projects SET hermes_approved = ?, updated_at = ? WHERE id = ?",
                (1 if hermes_approved else 0, now, project_id),
            )
            return {
                "project_id": project_id,
                "name": proj["name"],
                "hermes_approved": bool(hermes_approved),
                "updated_at": now,
            }

    def get_hermes_config(self) -> dict[str, Any]:
        default_cfg = {
            "enabled": False,
            "provider": "deepseek",
            "allow_paid_deepseek": False,
            "updated_at": None,
            "source": "default",
        }
        with self.db.read_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='runtime_settings'"
            ).fetchone()
            if not row:
                return default_cfg
            rec = conn.execute(
                "SELECT value_json, updated_at FROM runtime_settings WHERE key = 'hermes_config'"
            ).fetchone()
            if not rec:
                return default_cfg
            data = json.loads(rec["value_json"])
            return {
                "enabled": bool(data.get("enabled", False)),
                "provider": str(data.get("provider", "deepseek")),
                "allow_paid_deepseek": bool(data.get("allow_paid_deepseek", False)),
                "updated_at": rec["updated_at"],
                "source": "admin_override",
            }

    def update_hermes_config(
        self, *, enabled: bool, provider: str = "deepseek", allow_paid_deepseek: bool = False
    ) -> dict[str, Any]:
        now = utc_now_iso()
        # Safety invariant: disabling Hermes always disables paid DeepSeek fallback as well.
        effective_paid = bool(allow_paid_deepseek) if enabled else False
        payload = {
            "enabled": bool(enabled),
            "provider": provider,
            "allow_paid_deepseek": effective_paid,
        }
        with self.db.write_tx() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_settings (
                    key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                INSERT INTO runtime_settings (key, value_json, updated_at)
                VALUES ('hermes_config', ?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at
                """,
                (json.dumps(payload, ensure_ascii=False), now),
            )
        return {**payload, "updated_at": now, "source": "admin_override"}

    def list_worktrees(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            rows = conn.execute(
                """
                SELECT id, project_id, canonical_root, branch, head_commit, is_detached, dirty_fingerprint, mount_status, created_at, updated_at
                FROM worktrees WHERE project_id = ? ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
            items = [
                {
                    "worktree_id": r["id"],
                    "project_id": r["project_id"],
                    "canonical_root": r["canonical_root"],
                    "branch": r["branch"],
                    "head_commit": r["head_commit"],
                    "is_detached": bool(r["is_detached"]),
                    "dirty_fingerprint": r["dirty_fingerprint"],
                    "mount_status": r["mount_status"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
            return {"items": items}

    def create_worktree(self, project_id: str, req: WorktreeCreateRequest) -> dict[str, Any]:
        normalized_root = validate_safe_worktree_root(req.canonical_root)
        now = utc_now_iso()
        worktree_id = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            self._ensure_project(conn, project_id)
            existing = conn.execute(
                "SELECT id FROM worktrees WHERE canonical_root = ?",
                (normalized_root,),
            ).fetchone()
            if existing:
                raise BridgeError(409, "worktree_exists", "Worktree with this canonical root already exists")
            conn.execute(
                """
                INSERT INTO worktrees (
                    id, project_id, canonical_root, branch, head_commit, is_detached, dirty_fingerprint, mount_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending_host_setup', ?, ?)
                """,
                (
                    worktree_id,
                    project_id,
                    normalized_root,
                    req.branch,
                    req.head_commit,
                    1 if req.is_detached else 0,
                    req.dirty_fingerprint,
                    now,
                    now,
                ),
            )
        return {
            "worktree_id": worktree_id,
            "project_id": project_id,
            "canonical_root": normalized_root,
            "branch": req.branch,
            "head_commit": req.head_commit,
            "is_detached": req.is_detached,
            "dirty_fingerprint": req.dirty_fingerprint,
            "mount_status": "pending_host_setup",
            "created_at": now,
            "updated_at": now,
        }

    # --- Context ---
    def get_project_context(self, project_id: str, worktree_id: str) -> dict[str, Any]:
        t0 = time.perf_counter()
        with self.db.read_conn() as conn:
            project = self._ensure_project(conn, project_id)
            worktree = self._ensure_worktree(conn, project_id, worktree_id)
            rows = conn.execute(
                """
                SELECT id, type, title, body, source_agent, source_commit, evidence_ref, state, updated_at
                FROM memories
                WHERE project_id = ? AND (worktree_id = ? OR worktree_id IS NULL) AND state = 'verified'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (project_id, worktree_id, self.settings.default_limit),
            ).fetchall()

        items = [
            {
                "id": r["id"],
                "title": r["title"],
                "excerpt": r["body"][:400],
                "source_commit": r["source_commit"],
                "state": r["state"],
                "evidence_ref": r["evidence_ref"],
            }
            for r in rows
        ]
        index_freshness = {
            "head_commit": worktree["head_commit"],
            "dirty_fingerprint": worktree["dirty_fingerprint"],
            "mount_status": worktree["mount_status"],
            "stale": False,
        }
        envelope = _enforce_serialized_budget(items, self.settings.default_max_chars, index_freshness=index_freshness)
        envelope["project"] = {
            "project_id": project["id"],
            "name": project["name"],
            "hermes_approved": bool(project["hermes_approved"]),
        }
        envelope["worktree"] = {
            "worktree_id": worktree["id"],
            "branch": worktree["branch"],
            "head_commit": worktree["head_commit"],
            "dirty_fingerprint": worktree["dirty_fingerprint"],
            "mount_status": worktree["mount_status"],
        }
        self._record_metric(
            "get_project_context",
            (time.perf_counter() - t0) * 1000.0,
            project_id=project_id,
            worktree_id=worktree_id,
            result_count=len(envelope["items"]),
            result_chars=len(json.dumps(envelope, ensure_ascii=False)),
        )
        return envelope

    # --- Memories & FTS5 ---
    def create_memory(self, req: MemoryCreateRequest) -> tuple[int, dict[str, Any]]:
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id) if req.worktree_id else None
        scope_key = f"memory:{project_id}:{worktree_id or 'project'}"
        payload_hash = compute_payload_hash(req.model_dump(mode="json", exclude={"idempotency_key"}))

        with self.db.write_tx() as conn:
            if worktree_id:
                self._ensure_worktree(conn, project_id, worktree_id)
            else:
                self._ensure_project(conn, project_id)

            replay = self._check_idempotency(conn, scope_key, req.idempotency_key, payload_hash)
            if replay is not None:
                return replay

            memory_id = str(uuid.uuid4())
            now = utc_now_iso()
            tags_json = json.dumps(req.tags, ensure_ascii=False)
            # Writes always create 'candidate' state by default
            conn.execute(
                """
                INSERT INTO memories (
                    id, project_id, worktree_id, type, title, body, tags_json,
                    source_agent, source_commit, evidence_ref, state, supersedes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?)
                """,
                (
                    memory_id,
                    project_id,
                    worktree_id,
                    req.type,
                    req.title,
                    req.body,
                    tags_json,
                    req.source_agent,
                    req.source_commit,
                    req.evidence_ref,
                    str(req.supersedes) if req.supersedes else None,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO memories_fts (memory_id, project_id, worktree_id, title, body, tags)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (memory_id, project_id, worktree_id or "", req.title, req.body, " ".join(req.tags)),
            )

            resp = {
                "memory_id": memory_id,
                "project_id": project_id,
                "worktree_id": worktree_id,
                "type": req.type,
                "title": req.title,
                "state": "candidate",
                "source_agent": req.source_agent,
                "source_commit": req.source_commit,
                "evidence_ref": req.evidence_ref,
                "created_at": now,
            }
            self._save_idempotency(conn, scope_key, req.idempotency_key, payload_hash, 201, resp)
            return 201, resp

    def verify_memory(self, memory_id: str, req: MemoryVerifyRequest) -> dict[str, Any]:
        now = utc_now_iso()
        new_state = "verified" if req.action == "verify" else "superseded"
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
            if not row:
                raise BridgeError(404, "memory_not_found", "Memory not found")
            if req.supersedes:
                conn.execute(
                    "UPDATE memories SET state = 'superseded', updated_at = ? WHERE id = ? AND project_id = ?",
                    (now, str(req.supersedes), row["project_id"]),
                )
            conn.execute(
                "UPDATE memories SET state = ?, updated_at = ? WHERE id = ?",
                (new_state, now, memory_id),
            )
            return {
                "memory_id": memory_id,
                "project_id": row["project_id"],
                "worktree_id": row["worktree_id"],
                "state": new_state,
                "updated_at": now,
            }

    def list_memories(self, project_id: str, worktree_id: str | None = None, state: str | None = None, limit: int = 50) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            clauses = ["project_id = ?"]
            params: list[Any] = [project_id]
            if worktree_id:
                clauses.append("worktree_id = ?")
                params.append(worktree_id)
            if state:
                clauses.append("state = ?")
                params.append(state)
            params.append(max(1, min(limit, 100)))
            sql = f"""
                SELECT id, project_id, worktree_id, type, title, body, tags_json, source_agent, source_commit, evidence_ref, state, supersedes, created_at, updated_at
                FROM memories
                WHERE {' AND '.join(clauses)}
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, params).fetchall()
            items = [
                {
                    "id": r["id"],
                    "project_id": r["project_id"],
                    "worktree_id": r["worktree_id"],
                    "type": r["type"],
                    "title": r["title"],
                    "body": r["body"],
                    "tags": json.loads(r["tags_json"]),
                    "source_agent": r["source_agent"],
                    "source_commit": r["source_commit"],
                    "evidence_ref": r["evidence_ref"],
                    "state": r["state"],
                    "supersedes": r["supersedes"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in rows
            ]
            return {"items": items}

    def search_memory(self, req: MemorySearchRequest) -> dict[str, Any]:
        t0 = time.perf_counter()
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        fts_query = tokenize_fts_query(req.query)

        with self.db.read_conn() as conn:
            worktree = self._ensure_worktree(conn, project_id, worktree_id)
            index_freshness = {
                "head_commit": worktree["head_commit"],
                "dirty_fingerprint": worktree["dirty_fingerprint"],
                "stale": False,
            }
            if not fts_query:
                envelope = _enforce_serialized_budget([], req.max_chars, index_freshness=index_freshness)
                self._record_metric(
                    "search_memory",
                    (time.perf_counter() - t0) * 1000.0,
                    project_id=project_id,
                    worktree_id=worktree_id,
                    result_count=0,
                    result_chars=len(json.dumps(envelope, ensure_ascii=False)),
                )
                return envelope

            state_filter = "m.state IN ('verified', 'candidate')" if req.include_candidates else "m.state = 'verified'"
            scope_filter = (
                "(m.worktree_id = ? OR m.worktree_id IS NULL)"
                if req.include_project_wide
                else "m.worktree_id = ?"
            )
            fetch_limit = req.limit + 1
            sql = f"""
                SELECT m.id, m.title, m.body, m.source_commit, m.state, m.evidence_ref, m.source_agent
                FROM memories_fts f
                JOIN memories m ON m.id = f.memory_id
                WHERE memories_fts MATCH ?
                  AND m.project_id = ?
                  AND {scope_filter}
                  AND {state_filter}
                ORDER BY bm25(memories_fts), m.updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (fts_query, project_id, worktree_id, fetch_limit)).fetchall()

        already_truncated = len(rows) > req.limit
        items = [
            {
                "id": r["id"],
                "title": r["title"],
                "excerpt": r["body"],
                "source_commit": r["source_commit"],
                "state": r["state"],
                "evidence_ref": r["evidence_ref"],
                "source_agent": r["source_agent"],
            }
            for r in rows[: req.limit]
        ]
        envelope = _enforce_serialized_budget(
            items, req.max_chars, index_freshness=index_freshness, already_truncated=already_truncated
        )
        self._record_metric(
            "search_memory",
            (time.perf_counter() - t0) * 1000.0,
            project_id=project_id,
            worktree_id=worktree_id,
            result_count=len(envelope["items"]),
            result_chars=len(json.dumps(envelope, ensure_ascii=False)),
        )
        return envelope

    # --- Events & Worker Queue ---
    def create_event(self, req: EventCreateRequest, principal: AuthPrincipal | None = None) -> tuple[int, dict[str, Any]]:
        if req.approved and (principal is None or "admin" not in principal.scopes):
            raise BridgeError(
                403,
                "forbidden",
                "Only admin scope may approve events for external processing",
            )
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        scope_key = f"event:{project_id}:{worktree_id}"
        payload_hash = compute_payload_hash(req.model_dump(mode="json", exclude={"idempotency_key"}))

        with self.db.write_tx() as conn:
            self._ensure_worktree(conn, project_id, worktree_id)
            replay = self._check_idempotency(conn, scope_key, req.idempotency_key, payload_hash)
            if replay is not None:
                return replay

            event_id = str(uuid.uuid4())
            now = utc_now_iso()
            redacted = redact_payload(req.payload)
            source_agent = req.source_agent or (
                str(req.payload.get("source_agent"))[:64] if req.payload.get("source_agent") else None
            )
            conn.execute(
                """
                INSERT INTO events (
                    id, idempotency_key, payload_hash, project_id, worktree_id, task_id,
                    source_agent, kind, payload_json, approved, redacted_payload_json, processed_by_worker, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (
                    event_id,
                    req.idempotency_key,
                    payload_hash,
                    project_id,
                    worktree_id,
                    req.task_id,
                    source_agent,
                    req.kind,
                    json.dumps(req.payload, ensure_ascii=False),
                    1 if req.approved else 0,
                    json.dumps(redacted, ensure_ascii=False),
                    now,
                ),
            )
            resp = {
                "event_id": event_id,
                "project_id": project_id,
                "worktree_id": worktree_id,
                "task_id": req.task_id,
                "source_agent": source_agent,
                "kind": req.kind,
                "approved": req.approved,
                "created_at": now,
            }
            self._save_idempotency(conn, scope_key, req.idempotency_key, payload_hash, 201, resp)
            return 201, resp

    def list_events(self, project_id: str, worktree_id: str | None = None, limit: int = 50) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            clauses = ["project_id = ?"]
            params: list[Any] = [project_id]
            if worktree_id:
                clauses.append("worktree_id = ?")
                params.append(worktree_id)
            params.append(max(1, min(limit, 100)))
            rows = conn.execute(
                f"""
                SELECT id, project_id, worktree_id, task_id, kind, redacted_payload_json, approved, processed_by_worker, created_at
                FROM events WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
            items = [
                {
                    "event_id": r["id"],
                    "project_id": r["project_id"],
                    "worktree_id": r["worktree_id"],
                    "task_id": r["task_id"],
                    "kind": r["kind"],
                    "payload": json.loads(r["redacted_payload_json"] or "{}"),
                    "approved": bool(r["approved"]),
                    "processed_by_worker": bool(r["processed_by_worker"]),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return {"items": items}

    def approve_event(self, event_id: str) -> dict[str, Any]:
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT id, project_id, worktree_id, task_id, kind, approved FROM events WHERE id = ?",
                (event_id,),
            ).fetchone()
            if not row:
                raise BridgeError(404, "event_not_found", "Event not found")
            conn.execute("UPDATE events SET approved = 1 WHERE id = ?", (event_id,))
            return {
                "event_id": row["id"],
                "project_id": row["project_id"],
                "worktree_id": row["worktree_id"],
                "task_id": row["task_id"],
                "kind": row["kind"],
                "approved": True,
            }

    def get_worker_events(self, project_id: str | None = None, limit: int = 10) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            clauses = ["e.approved = 1", "e.processed_by_worker = 0", "p.hermes_approved = 1"]
            params: list[Any] = []
            if project_id:
                project = self._ensure_project(conn, project_id)
                if not project["hermes_approved"]:
                    raise BridgeError(403, "hermes_not_approved", "Project has not approved external Hermes processing")
                clauses.append("e.project_id = ?")
                params.append(project_id)
            params.append(max(1, min(limit, 50)))
            rows = conn.execute(
                f"""
                SELECT e.id, e.project_id, e.worktree_id, e.task_id, e.source_agent, e.kind,
                       e.redacted_payload_json, e.approved, p.hermes_approved, e.created_at
                FROM events e
                JOIN projects p ON p.id = e.project_id
                WHERE {' AND '.join(clauses)}
                ORDER BY e.created_at ASC
                LIMIT ?
                """,
                params,
            ).fetchall()
            items = [
                {
                    "event_id": r["id"],
                    "project_id": r["project_id"],
                    "worktree_id": r["worktree_id"],
                    "task_id": r["task_id"],
                    "source_agent": r["source_agent"],
                    "kind": r["kind"],
                    "approved": bool(r["approved"]),
                    "external_llm_approved": bool(r["hermes_approved"]),
                    "payload": json.loads(r["redacted_payload_json"] or "{}"),
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return {"items": items}

    def submit_worker_result(self, req: WorkerResultRequest) -> tuple[int, dict[str, Any]]:
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        scope_key = f"worker_result:{project_id}:{worktree_id}"
        payload_hash = compute_payload_hash(req.model_dump(mode="json", exclude={"idempotency_key"}))

        with self.db.write_tx() as conn:
            project = self._ensure_project(conn, project_id)
            if not project["hermes_approved"]:
                raise BridgeError(403, "hermes_not_approved", "Project has not approved Hermes worker writes")
            self._ensure_worktree(conn, project_id, worktree_id)

            replay = self._check_idempotency(conn, scope_key, req.idempotency_key, payload_hash)
            if replay is not None:
                return replay

            if req.event_id:
                ev_row = conn.execute(
                    "SELECT approved FROM events WHERE id = ? AND project_id = ? AND worktree_id = ?",
                    (str(req.event_id), project_id, worktree_id),
                ).fetchone()
                if ev_row and not ev_row["approved"]:
                    raise BridgeError(403, "event_not_approved", "Event is not approved for worker processing")

            safe_title = redact_secrets(req.title)
            safe_body = redact_secrets(req.body)
            memory_id = str(uuid.uuid4())
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO memories (
                    id, project_id, worktree_id, type, title, body, tags_json,
                    source_agent, source_commit, evidence_ref, state, supersedes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'hermes-worker', NULL, ?, 'candidate', NULL, ?, ?)
                """,
                (
                    memory_id,
                    project_id,
                    worktree_id,
                    req.type,
                    safe_title,
                    safe_body,
                    json.dumps(req.tags, ensure_ascii=False),
                    req.evidence_ref,
                    now,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO memories_fts (memory_id, project_id, worktree_id, title, body, tags) VALUES (?, ?, ?, ?, ?, ?)",
                (memory_id, project_id, worktree_id, safe_title, safe_body, " ".join(req.tags)),
            )
            if req.event_id:
                conn.execute(
                    "UPDATE events SET processed_by_worker = 1 WHERE id = ? AND project_id = ? AND worktree_id = ?",
                    (str(req.event_id), project_id, worktree_id),
                )

            resp = {
                "memory_id": memory_id,
                "project_id": project_id,
                "worktree_id": worktree_id,
                "state": "candidate",
                "created_at": now,
            }
            self._save_idempotency(conn, scope_key, req.idempotency_key, payload_hash, 201, resp)

        if any(v is not None for v in (req.input_tokens, req.output_tokens, req.cache_read_tokens, req.cost_usd)):
            self._record_metric(
                "worker_result",
                0.0,
                project_id=project_id,
                worktree_id=worktree_id,
                input_tokens=req.input_tokens,
                output_tokens=req.output_tokens,
                cache_read_tokens=req.cache_read_tokens,
                cost_usd=req.cost_usd,
            )
        return 201, resp

    # --- Handoffs ---
    def save_handoff(self, req: HandoffCreateRequest) -> tuple[int, dict[str, Any]]:
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        scope_key = f"handoff:{project_id}:{worktree_id}"
        payload_hash = compute_payload_hash(req.model_dump(mode="json", exclude={"idempotency_key"}))

        with self.db.write_tx() as conn:
            self._ensure_worktree(conn, project_id, worktree_id)
            replay = self._check_idempotency(conn, scope_key, req.idempotency_key, payload_hash)
            if replay is not None:
                return replay

            handoff_id = str(uuid.uuid4())
            now = utc_now_iso()
            conn.execute(
                """
                INSERT INTO handoffs (
                    id, project_id, worktree_id, task_id, summary, evidence_json,
                    source_agent, state, idempotency_key, payload_hash, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
                """,
                (
                    handoff_id,
                    project_id,
                    worktree_id,
                    req.task_id,
                    req.summary,
                    json.dumps(redact_payload(req.evidence), ensure_ascii=False),
                    req.source_agent,
                    req.idempotency_key,
                    payload_hash,
                    now,
                    now,
                ),
            )
            resp = {
                "handoff_id": handoff_id,
                "project_id": project_id,
                "worktree_id": worktree_id,
                "task_id": req.task_id,
                "state": "candidate",
                "source_agent": req.source_agent,
                "created_at": now,
            }
            self._save_idempotency(conn, scope_key, req.idempotency_key, payload_hash, 201, resp)
            return 201, resp

    def get_handoff(self, task_id: str, project_id: str, worktree_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_worktree(conn, project_id, worktree_id)
            rows = conn.execute(
                """
                SELECT id, project_id, worktree_id, task_id, summary, evidence_json, source_agent, state, created_at, updated_at
                FROM handoffs
                WHERE project_id = ? AND worktree_id = ? AND task_id = ?
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (project_id, worktree_id, task_id),
            ).fetchall()
            if not rows:
                raise BridgeError(404, "handoff_not_found", "Handoff not found for task")
            items = [
                {
                    "handoff_id": r["id"],
                    "project_id": r["project_id"],
                    "worktree_id": r["worktree_id"],
                    "task_id": r["task_id"],
                    "summary": r["summary"],
                    "evidence": json.loads(r["evidence_json"]),
                    "source_agent": r["source_agent"],
                    "state": r["state"],
                    "created_at": r["created_at"],
                }
                for r in rows
            ]
            return {"task_id": task_id, "items": items}

    # --- Codebase proxy & index job sync ---
    def _codebase_headers(self, *, admin: bool = False) -> dict[str, str]:
        token = (
            (self.settings.codebase_admin_token or self.settings.codebase_token)
            if admin
            else (self.settings.codebase_read_token or self.settings.codebase_token)
        )
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _sync_index_job_row(self, job_data: dict[str, Any]) -> None:
        job_id = str(job_data.get("job_id") or "")
        project_id = str(job_data.get("project_id") or "")
        worktree_id = str(job_data.get("worktree_id") or "")
        if not (job_id and project_id and worktree_id):
            return
        status = str(job_data.get("state") or job_data.get("status") or "unknown")
        head_commit = job_data.get("head_commit")
        dirty_fp = job_data.get("dirty_fingerprint") or ""
        updated_at = str(job_data.get("updated_at") or utc_now_iso())
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO index_jobs (id, project_id, worktree_id, status, head_commit, dirty_fingerprint, stale, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                ON CONFLICT(id) DO UPDATE SET
                    status = excluded.status,
                    head_commit = excluded.head_commit,
                    dirty_fingerprint = excluded.dirty_fingerprint,
                    updated_at = excluded.updated_at
                """,
                (job_id, project_id, worktree_id, status, head_commit, dirty_fp, updated_at),
            )
            if status == "succeeded":
                conn.execute(
                    """
                    UPDATE worktrees
                    SET mount_status = 'mounted',
                        head_commit = COALESCE(?, head_commit),
                        dirty_fingerprint = COALESCE(?, dirty_fingerprint),
                        updated_at = ?
                    WHERE id = ? AND project_id = ?
                    """,
                    (head_commit, dirty_fp, updated_at, worktree_id, project_id),
                )

    def search_code(self, req: CodeSearchRequest) -> dict[str, Any]:
        t0 = time.perf_counter()
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        with self.db.read_conn() as conn:
            self._ensure_worktree(conn, project_id, worktree_id)

        if self.settings.codebase_url:
            headers = self._codebase_headers(admin=False)
            try:
                with httpx.Client(timeout=30.0) as client:
                    resp = client.post(
                        f"{self.settings.codebase_url.rstrip('/')}/v1/search/code",
                        json=req.model_dump(mode="json"),
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        self._record_metric(
                            "search_code",
                            (time.perf_counter() - t0) * 1000.0,
                            project_id=project_id,
                            worktree_id=worktree_id,
                            result_count=len(data.get("items", [])),
                            result_chars=len(json.dumps(data, ensure_ascii=False)),
                        )
                        return data
                    if resp.status_code in (404, 422):
                        err = resp.json().get("error", {}) if resp.content else {}
                        raise BridgeError(
                            resp.status_code,
                            str(err.get("code") or "codebase_error"),
                            str(err.get("message") or "Codebase search rejected request"),
                        )
            except httpx.HTTPError:
                pass

        raise BridgeError(503, "codebase_unavailable", "Codebase search service is unavailable")

    def trigger_index(self, req: IndexTriggerRequest) -> dict[str, Any]:
        project_id = str(req.project_id)
        worktree_id = str(req.worktree_id)
        with self.db.read_conn() as conn:
            worktree = self._ensure_worktree(conn, project_id, worktree_id)

        if self.settings.codebase_url:
            headers = self._codebase_headers(admin=True)
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        f"{self.settings.codebase_url.rstrip('/')}/v1/index",
                        json=req.model_dump(mode="json"),
                        headers=headers,
                    )
                    if resp.status_code in (200, 202):
                        data = resp.json()
                        self._sync_index_job_row(data)
                        if "status" not in data and "state" in data:
                            data["status"] = data["state"]
                        return data
            except httpx.HTTPError:
                pass

        job_id = str(uuid.uuid4())
        now = utc_now_iso()
        status = "pending_host_setup" if worktree["mount_status"] == "pending_host_setup" else "queued_local"
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO index_jobs (id, project_id, worktree_id, status, head_commit, dirty_fingerprint, stale, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (job_id, project_id, worktree_id, status, worktree["head_commit"], worktree["dirty_fingerprint"], now),
            )
        return {
            "job_id": job_id,
            "project_id": project_id,
            "worktree_id": worktree_id,
            "status": status,
            "updated_at": now,
        }

    def get_index_job(
        self, job_id: str, project_id: str | None = None, worktree_id: str | None = None
    ) -> dict[str, Any]:
        local_row: dict[str, Any] | None = None
        with self.db.read_conn() as conn:
            if project_id:
                row = conn.execute(
                    "SELECT * FROM index_jobs WHERE id = ? AND project_id = ?",
                    (job_id, project_id),
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM index_jobs WHERE id = ?", (job_id,)).fetchone()
            if row:
                local_row = dict(row)

        eff_pid = project_id or (local_row["project_id"] if local_row else None)
        eff_wid = worktree_id or (local_row["worktree_id"] if local_row else None)
        if self.settings.codebase_url and eff_pid and eff_wid:
            headers = self._codebase_headers(admin=False)
            try:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(
                        f"{self.settings.codebase_url.rstrip('/')}/v1/index/{job_id}",
                        params={"project_id": eff_pid, "worktree_id": eff_wid},
                        headers=headers,
                    )
                    if resp.status_code == 200:
                        remote = resp.json()
                        self._sync_index_job_row(remote)
                        if "status" not in remote and "state" in remote:
                            remote["status"] = remote["state"]
                        return remote
            except httpx.HTTPError:
                pass

        if local_row is None:
            raise BridgeError(404, "index_job_not_found", "Index job not found")
        local_row.setdefault("job_id", local_row["id"])
        return local_row

    # --- Metrics (real measurements only) ---
    def get_metrics(self) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            agg = conn.execute(
                """
                SELECT
                    COUNT(*) AS query_count,
                    COALESCE(AVG(latency_ms), 0.0) AS avg_latency_ms,
                    COALESCE(AVG(result_chars), 0.0) AS avg_result_chars,
                    SUM(CASE WHEN measured = 1 THEN 1 ELSE 0 END) AS measured_token_records,
                    SUM(CASE WHEN measured = 1 THEN COALESCE(input_tokens, 0) ELSE 0 END) AS input_tokens,
                    SUM(CASE WHEN measured = 1 THEN COALESCE(output_tokens, 0) ELSE 0 END) AS output_tokens,
                    SUM(CASE WHEN measured = 1 THEN COALESCE(cache_read_tokens, 0) ELSE 0 END) AS cache_read_tokens,
                    SUM(CASE WHEN measured = 1 THEN COALESCE(cost_usd, 0.0) ELSE 0.0 END) AS cost_usd
                FROM metrics_events
                """
            ).fetchone()
            counts = conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM projects) AS projects,
                    (SELECT COUNT(*) FROM worktrees) AS worktrees,
                    (SELECT COUNT(*) FROM memories WHERE state = 'verified') AS verified_memories,
                    (SELECT COUNT(*) FROM memories WHERE state = 'candidate') AS candidate_memories,
                    (SELECT COUNT(*) FROM events) AS events
                """
            ).fetchone()

        has_measured_tokens = int(agg["measured_token_records"] or 0) > 0
        return {
            "query_count": int(agg["query_count"] or 0),
            "avg_latency_ms": round(float(agg["avg_latency_ms"] or 0.0), 2),
            "avg_result_chars": round(float(agg["avg_result_chars"] or 0.0), 1),
            "measured_tokens_available": has_measured_tokens,
            "token_usage": (
                {
                    "input_tokens": int(agg["input_tokens"] or 0),
                    "output_tokens": int(agg["output_tokens"] or 0),
                    "cache_read_tokens": int(agg["cache_read_tokens"] or 0),
                    "cost_usd": round(float(agg["cost_usd"] or 0.0), 6),
                }
                if has_measured_tokens
                else None
            ),
            "counts": dict(counts),
        }
