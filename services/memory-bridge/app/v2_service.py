"""Memory Bridge V2 service: catalog, routing, policies, adaptive brain, plans/DAG, jobs, sessions, forecasts, usage & quotas."""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import DatabaseManager, reconcile_expired_job_leases, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .trace_usage import sync_usage_trace
from .trace_lifecycle import trace_key, plan_created, agent_claimed, agent_heartbeat, agent_finished
from .v2_schemas import (
    BrainConfigUpdateRequest,
    BrainRoleConfig,
    BrainTokenAccounting,
    CatalogRefreshRequest,
    EvaluationCreateRequest,
    ForecastComputeRequest,
    JobCheckpointRequest,
    JobClaimRequest,
    JobCompleteRequest,
    JobEventRequest,
    JobHeartbeatRequest,
    PlanApproveRequest,
    PlanCreateRequest,
    PlanLifecycleRequest,
    PlanRevisionUpdateRequest,
    PolicyUpdateRequest,
    QuotaSnapshotCreateRequest,
    RoutingRecommendRequest,
    SessionUpsertRequest,
    TaskForecast,
    UsageEventCreateRequest,
)


def _percentile(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = (len(sorted_vals) - 1) * q
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (idx - lo)


def _default_brain_roles() -> list[dict[str, Any]]:
    """Verified host CLI defaults per docs/evidence/host-brain-discovery-v2.md."""
    return [
        {
            "role_name": "tier0_rules",
            "preferred_label": "Quy tắc cục bộ (0 LLM)",
            "effective_provider": "local",
            "effective_model_id": "deterministic-rules",
            "effective_effort": None,
            "auth_mode": "unknown",
            "availability_status": "verified",
            "availability_reason": "Luôn sẵn sàng cục bộ, không tốn token",
            "fallback_order": [],
            "max_escalations": 0,
            "last_verified_at": None,
        },
        {
            "role_name": "tier1_light",
            "preferred_label": "Gemini 4 Hydrogen (tạm đề xuất: Claude Sonnet 5.5 Low)",
            "effective_provider": "agy",
            "effective_model_id": "claude-sonnet-5-5-low",
            "effective_effort": "low",
            "auth_mode": "subscription_cli",
            "availability_status": "verified",
            "availability_reason": "Gemini 4 Hydrogen chưa có trong agy models v1.1.27; tạm dùng claude-sonnet-5-5-low đã xác minh qua CLI.",
            "fallback_order": ["claude-sonnet-5-5-low", "gemini-3.8-flash-low", "gemini-3.8-flash-medium"],
            "max_escalations": 1,
            "last_verified_at": None,
        },
        {
            "role_name": "tier2_primary",
            "preferred_label": "Gemini 4 Neon Medium",
            "effective_provider": "agy",
            "effective_model_id": "claude-sonnet-5-5-medium",
            "effective_effort": "medium",
            "auth_mode": "subscription_cli",
            "availability_status": "unavailable",
            "availability_reason": "Gemini 4 Neon chưa có trong agy models v1.1.27; tạm dùng claude-sonnet-5-5-medium đã xác minh qua CLI.",
            "fallback_order": ["claude-sonnet-5-5-medium", "gemini-3.8-flash-high", "claude-sonnet-5-5-high"],
            "max_escalations": 1,
            "last_verified_at": None,
        },
        {
            "role_name": "tier3_advanced",
            "preferred_label": "Gemini 4 Carbon / Claude Opus 5.5 Medium",
            "effective_provider": "agy",
            "effective_model_id": "claude-opus-5-5-medium",
            "effective_effort": "medium",
            "auth_mode": "subscription_cli",
            "availability_status": "verified",
            "availability_reason": "Gemini 4 Carbon chưa có trên CLI; claude-opus-5-5-medium đã xác minh qua agy models.",
            "fallback_order": ["claude-opus-5-5-medium", "claude-opus-5-5-high"],
            "max_escalations": 1,
            "last_verified_at": None,
        },
        {
            "role_name": "secondary_fallback",
            "preferred_label": "Claude Sonnet 5.5 Medium (DeepSeek opt-in)",
            "effective_provider": "agy",
            "effective_model_id": "claude-sonnet-5-5-medium",
            "effective_effort": "medium",
            "auth_mode": "subscription_cli",
            "availability_status": "verified",
            "availability_reason": "Đã xác minh qua agy models; DeepSeek API tắt mặc định",
            "fallback_order": ["claude-sonnet-5-5-medium"],
            "max_escalations": 1,
            "last_verified_at": None,
        },
    ]


class V2BridgeService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db

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

    def _audit(
        self,
        conn: Any,
        *,
        project_id: str | None,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO v2_audit_log (id, project_id, actor, action, target_type, target_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                actor,
                action,
                target_type,
                target_id,
                json.dumps(redact_payload(details or {}), ensure_ascii=False),
                utc_now_iso(),
            ),
        )

    # --- 1. Catalog & Evaluations ---
    def get_catalog(self, snapshot_id: str | None = None) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            if snapshot_id:
                snap = conn.execute("SELECT * FROM v2_catalog_snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            else:
                snap = conn.execute(
                    "SELECT * FROM v2_catalog_snapshots ORDER BY fetched_at DESC LIMIT 1"
                ).fetchone()
            if not snap:
                return {
                    "snapshot_id": None,
                    "version": 0,
                    "is_stale": True,
                    "fetched_at": None,
                    "expires_at": None,
                    "source_urls": [],
                    "models": [],
                    "new_models": [],
                    "retiring_models": [],
                    "gemini4_neon_available": False,
                    "gemini4_neon_models": [],
                    "gemini4_neon_recommendation": None,
                }
            models = conn.execute(
                "SELECT * FROM v2_models WHERE snapshot_id = ? ORDER BY provider, model_id",
                (snap["id"],),
            ).fetchall()
            exec_verified_rows = conn.execute(
                """
                SELECT DISTINCT model_id FROM v2_model_evaluations
                WHERE evidence_kind = 'observed' AND sample_count > 0 AND (failure_rate IS NULL OR failure_rate < 1.0)
                """
            ).fetchall()
            exec_verified_ids = {r["model_id"] for r in exec_verified_rows}

            prev_snap = conn.execute(
                "SELECT id FROM v2_catalog_snapshots WHERE fetched_at < ? ORDER BY fetched_at DESC LIMIT 1",
                (snap["fetched_at"],),
            ).fetchone()
            prev_ids: set[str] = set()
            if prev_snap:
                prev_rows = conn.execute(
                    "SELECT model_id FROM v2_models WHERE snapshot_id = ?", (prev_snap["id"],)
                ).fetchall()
                prev_ids = {r["model_id"] for r in prev_rows}

            enriched_models: list[dict[str, Any]] = []
            cur_ids: set[str] = set()
            retiring_models: list[str] = []
            neon_live_models: list[str] = []
            for m in models:
                caps = json.loads(m["capabilities_json"])
                mid = m["model_id"]
                cur_ids.add(mid)
                if mid in exec_verified_ids:
                    stage = "execution_verified"
                else:
                    stage = str(
                        caps.get("verification_stage")
                        or ("cli_available" if m["status"] == "verified" else "catalog_listed")
                    )
                if m["status"] == "deprecated" or caps.get("retiring_soon"):
                    retiring_models.append(mid)
                if "gemini-4-neon" in mid.lower() and stage in {"cli_available", "execution_verified"}:
                    neon_live_models.append(mid)
                enriched_models.append(
                    {
                        "id": m["id"],
                        "provider": m["provider"],
                        "model_id": mid,
                        "cli_family": m["cli_family"],
                        "cli_version": m["cli_version"],
                        "auth_mode": m["auth_mode"],
                        "status": m["status"],
                        "verification_stage": stage,
                        "context_limit": m["context_limit"],
                        "output_limit": m["output_limit"],
                        "capabilities": caps,
                        "price_input_per_1m": m["price_input_per_1m"],
                        "price_output_per_1m": m["price_output_per_1m"],
                        "price_cache_read_per_1m": m["price_cache_read_per_1m"],
                        "price_cache_write_per_1m": m["price_cache_write_per_1m"],
                        "currency": m["currency"],
                        "effective_date": m["effective_date"],
                        "source_url": m["source_url"],
                        "verified_at": m["verified_at"],
                        "expires_at": m["expires_at"],
                    }
                )

            new_models = sorted(cur_ids - prev_ids) if prev_ids else []
            removed_models = sorted(prev_ids - cur_ids) if prev_ids else []
            for rm in removed_models:
                if rm not in retiring_models:
                    retiring_models.append(rm)

            neon_available = bool(neon_live_models)
            neon_rec = (
                f"Phát hiện Gemini 4 Neon ({', '.join(neon_live_models)}) đã khả dụng qua CLI! Đề xuất chuyển bộ não chính (tier2_primary) sang {neon_live_models[0]}."
                if neon_available
                else None
            )
            return {
                "snapshot_id": snap["id"],
                "version": snap["version"],
                "is_stale": bool(snap["is_stale"]),
                "fetched_at": snap["fetched_at"],
                "expires_at": snap["expires_at"],
                "source_urls": json.loads(snap["source_urls_json"]),
                "models": enriched_models,
                "new_models": new_models,
                "retiring_models": retiring_models,
                "gemini4_neon_available": neon_available,
                "gemini4_neon_models": neon_live_models,
                "gemini4_neon_recommendation": neon_rec,
            }

    def refresh_catalog(self, req: CatalogRefreshRequest, actor: str = "admin") -> dict[str, Any]:
        snap_id = str(uuid.uuid4())
        now = utc_now_iso()
        expires_at = req.expires_at or (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        raw_payload = json.dumps([m.model_dump(mode="json") for m in req.models], sort_keys=True, ensure_ascii=False)
        checksum = hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()

        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO v2_catalog_snapshots (id, version, source_urls_json, fetched_at, expires_at, is_stale, checksum)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snap_id,
                    req.version,
                    json.dumps(req.source_urls, ensure_ascii=False),
                    now,
                    expires_at,
                    1 if req.is_stale else 0,
                    checksum,
                ),
            )
            for m in req.models:
                conn.execute(
                    """
                    INSERT INTO v2_models (
                        id, snapshot_id, provider, model_id, cli_family, cli_version, auth_mode, status,
                        context_limit, output_limit, capabilities_json, price_input_per_1m, price_output_per_1m,
                        price_cache_read_per_1m, price_cache_write_per_1m, currency, effective_date, source_url,
                        verified_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        snap_id,
                        m.provider,
                        m.model_id,
                        m.cli_family,
                        m.cli_version,
                        m.auth_mode,
                        m.status,
                        m.context_limit,
                        m.output_limit,
                        json.dumps(m.capabilities, ensure_ascii=False),
                        m.price_input_per_1m,
                        m.price_output_per_1m,
                        m.price_cache_read_per_1m,
                        m.price_cache_write_per_1m,
                        m.currency,
                        m.effective_date,
                        m.source_url,
                        m.verified_at,
                        m.expires_at,
                    ),
                )
            self._audit(
                conn,
                project_id=None,
                actor=actor,
                action="catalog_refresh",
                target_type="catalog_snapshot",
                target_id=snap_id,
                details={"model_count": len(req.models), "is_stale": req.is_stale},
            )
        return self.get_catalog(snap_id)

    def create_evaluation(self, req: EvaluationCreateRequest, actor: str = "planner") -> dict[str, Any]:
        eval_id = str(uuid.uuid4())
        now = utc_now_iso()
        pid = str(req.project_id) if req.project_id else None
        with self.db.write_tx() as conn:
            if pid:
                self._ensure_project(conn, pid)
            conn.execute(
                """
                INSERT INTO v2_model_evaluations (
                    id, project_id, model_id, provider, auth_mode, effort, task_type, complexity,
                    evidence_kind, correctness_score, acceptance_rate, avg_latency_ms, avg_effective_cost_usd,
                    failure_rate, sample_count, provenance_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    eval_id,
                    pid,
                    req.model_id,
                    req.provider,
                    req.auth_mode,
                    req.effort,
                    req.task_type,
                    req.complexity,
                    req.evidence_kind,
                    req.correctness_score,
                    req.acceptance_rate,
                    req.avg_latency_ms,
                    req.avg_effective_cost_usd,
                    req.failure_rate,
                    req.sample_count,
                    json.dumps(req.provenance, ensure_ascii=False),
                    now,
                ),
            )
            self._audit(
                conn,
                project_id=pid,
                actor=actor,
                action="evaluation_recorded",
                target_type="model_evaluation",
                target_id=eval_id,
                details={"model_id": req.model_id, "task_type": req.task_type, "evidence_kind": req.evidence_kind},
            )
        return {"evaluation_id": eval_id, "created_at": now}

    def list_evaluations(
        self,
        task_type: str | None = None,
        model_id: str | None = None,
        auth_mode: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            clauses = ["1=1"]
            params: list[Any] = []
            if project_id:
                self._ensure_project(conn, project_id)
                clauses.append("(project_id = ? OR project_id IS NULL)")
                params.append(project_id)
            if task_type:
                clauses.append("task_type = ?")
                params.append(task_type)
            if model_id:
                clauses.append("model_id = ?")
                params.append(model_id)
            if auth_mode:
                clauses.append("auth_mode = ?")
                params.append(auth_mode)
            rows = conn.execute(
                f"SELECT * FROM v2_model_evaluations WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT 100",
                params,
            ).fetchall()
            return {
                "items": [
                    {
                        "id": r["id"],
                        "project_id": r["project_id"],
                        "model_id": r["model_id"],
                        "provider": r["provider"],
                        "auth_mode": r["auth_mode"],
                        "effort": r["effort"],
                        "task_type": r["task_type"],
                        "complexity": r["complexity"],
                        "evidence_kind": r["evidence_kind"],
                        "correctness_score": r["correctness_score"],
                        "acceptance_rate": r["acceptance_rate"],
                        "avg_latency_ms": r["avg_latency_ms"],
                        "avg_effective_cost_usd": r["avg_effective_cost_usd"],
                        "failure_rate": r["failure_rate"],
                        "sample_count": r["sample_count"],
                        "provenance": json.loads(r["provenance_json"]),
                        "created_at": r["created_at"],
                    }
                    for r in rows
                ]
            }

    def recommend_routing(self, req: RoutingRecommendRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        with self.db.read_conn() as conn:
            self._ensure_project(conn, pid)
            policy = self._get_policy_row(conn, pid)
            allowed_modes = json.loads(policy["allowed_auth_modes_json"])
            if req.allowed_auth_modes is not None:
                allowed_modes = [mode for mode in allowed_modes if mode in req.allowed_auth_modes]
            if not policy["allow_paid_api_fallback"]:
                allowed_modes = [m for m in allowed_modes if m != "api_key"]

            if req.task_type == "routine":
                return {
                    "task_type": req.task_type,
                    "complexity": req.complexity,
                    "provider": "local",
                    "model_id": "deterministic-rules",
                    "effort": None,
                    "auth_mode": "unknown",
                    "rationale": "Tác vụ định kỳ dùng quy tắc cục bộ (0 LLM)",
                    "evidence_kind": "observed",
                    "cold_start": False,
                    "alternatives": [],
                }

            snap = conn.execute(
                "SELECT id, expires_at FROM v2_catalog_snapshots ORDER BY fetched_at DESC LIMIT 1"
            ).fetchone()
            if not snap:
                raise BridgeError(503, "catalog_empty", "No verified model catalog snapshot available")

            now = utc_now_iso()
            if snap["expires_at"] and conn.execute("SELECT julianday(?) <= julianday(?)", (snap["expires_at"], now)).fetchone()[0]:
                raise BridgeError(409, "catalog_stale", "Refresh model catalog before routing")
            models = conn.execute(
                "SELECT * FROM v2_models WHERE snapshot_id = ? AND status = 'verified'",
                (snap["id"],),
            ).fetchall()
            eligible = [dict(m) for m in models if m["auth_mode"] in allowed_modes
                        and not json.loads(m["capabilities_json"]).get("retiring_soon")
                        and (not m["expires_at"] or conn.execute("SELECT julianday(?) > julianday(?)", (m["expires_at"], now)).fetchone()[0])]
            if not eligible:
                raise BridgeError(422, "no_eligible_model", "No eligible verified model matches policy constraints")

            evals = conn.execute(
                """
                SELECT * FROM v2_model_evaluations
                WHERE task_type = ? AND complexity = ? AND (project_id = ? OR project_id IS NULL)
                ORDER BY (project_id IS NOT NULL) DESC, sample_count DESC, created_at DESC
                """,
                (req.task_type, req.complexity, pid),
            ).fetchall()
            eval_map = {}
            for e in evals:
                if e["sample_count"] > 0 and e["evidence_kind"] != "hypothesis":
                    eval_map.setdefault((e["provider"], e["model_id"], e["auth_mode"]), dict(e))
            role = "tier1_light" if req.complexity in ("XS", "S") else "tier2_primary" if req.complexity == "M" else "tier3_advanced"
            configured = conn.execute("SELECT effective_provider, effective_model_id, effective_effort FROM v2_brain_roles WHERE project_id=? AND role_name=?", (pid, role)).fetchone()
            preferred = (configured["effective_provider"], configured["effective_model_id"]) if configured else (None, None)
            limit = req.max_cost_usd
            if policy["max_task_cost_usd"] is not None:
                limit = min(limit, policy["max_task_cost_usd"]) if limit is not None else policy["max_task_cost_usd"]
            if limit is not None:
                eligible = [m for m in eligible if (ev := eval_map.get((m["provider"], m["model_id"], m["auth_mode"]))) and ev["avg_effective_cost_usd"] is not None and ev["avg_effective_cost_usd"] <= limit]
            if not eligible:
                raise BridgeError(422, "no_budget_evidence", "No eligible model has verified cost evidence within the requested budget")

            def score(m: dict[str, Any]):
                ev = eval_map.get((m["provider"], m["model_id"], m["auth_mode"]))
                quality = ev["correctness_score"] if ev and ev["correctness_score"] is not None else None
                cost = ev["avg_effective_cost_usd"] if ev else None
                return (quality is None, -(quality if quality is not None else 0),
                        (m["provider"], m["model_id"]) != preferred,
                        cost is None, cost if cost is not None else 0,
                        m["provider"], m["model_id"])

            ranked = sorted(eligible, key=score)
            chosen = ranked[0]
            ev = eval_map.get((chosen["provider"], chosen["model_id"], chosen["auth_mode"]))
            return {
                "task_type": req.task_type,
                "complexity": req.complexity,
                "provider": chosen["provider"],
                "model_id": chosen["model_id"],
                "effort": ev["effort"] if ev else None,
                "auth_mode": chosen["auth_mode"],
                "rationale": (
                    f"Chọn từ catalog xác minh dựa trên {ev['sample_count']} mẫu ({ev['evidence_kind']})"
                    if ev
                    else "Cold-start: chưa có mẫu đánh giá; ưu tiên role cấu hình nếu hợp lệ, không giả định chi phí bằng 0"
                ),
                "evidence_kind": ev["evidence_kind"] if ev else "hypothesis",
                "cold_start": ev is None,
                "alternatives": [
                    {"provider": m["provider"], "model_id": m["model_id"], "auth_mode": m["auth_mode"]}
                    for m in ranked[1:4]
                ],
            }

    # --- 2. Policies & Adaptive Brain ---
    def _get_policy_row(self, conn: Any, project_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM v2_policies WHERE project_id = ?", (project_id,)).fetchone()
        if row:
            return dict(row)
        return {
            "project_id": project_id,
            "approval_mode": "REVIEW_REQUIRED",
            "auto_expires_at": None,
            "auto_persist_across_restart": 0,
            "kill_switch": 0,
            "max_concurrency": 2,
            "max_task_cost_usd": None,
            "max_daily_cost_usd": None,
            "max_daily_planner_cost_usd": None,
            "allow_paid_api_fallback": 0,
            "allowed_auth_modes_json": '["subscription_cli"]',
            "retention_days": 180,
            "allow_cross_project_aggregates": 0,
            "updated_at": None,
        }

    def get_policy(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            r = self._get_policy_row(conn, project_id)
            return {
                "project_id": r["project_id"],
                "approval_mode": r["approval_mode"],
                "auto_expires_at": r["auto_expires_at"],
                "auto_persist_across_restart": bool(r["auto_persist_across_restart"]),
                "kill_switch": bool(r["kill_switch"]),
                "max_concurrency": r["max_concurrency"],
                "max_task_cost_usd": r["max_task_cost_usd"],
                "max_daily_cost_usd": r["max_daily_cost_usd"],
                "max_daily_planner_cost_usd": r["max_daily_planner_cost_usd"],
                "allow_paid_api_fallback": bool(r["allow_paid_api_fallback"]),
                "allowed_auth_modes": json.loads(r["allowed_auth_modes_json"]),
                "retention_days": r["retention_days"],
                "allow_cross_project_aggregates": bool(r["allow_cross_project_aggregates"]),
                "updated_at": r["updated_at"],
            }

    def update_policy(self, req: PolicyUpdateRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            self._ensure_project(conn, pid)
            conn.execute(
                """
                INSERT INTO v2_policies (
                    project_id, approval_mode, auto_expires_at, auto_persist_across_restart, kill_switch,
                    max_concurrency, max_task_cost_usd, max_daily_cost_usd, max_daily_planner_cost_usd,
                    allow_paid_api_fallback, allowed_auth_modes_json, retention_days, allow_cross_project_aggregates, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    approval_mode = excluded.approval_mode,
                    auto_expires_at = excluded.auto_expires_at,
                    auto_persist_across_restart = excluded.auto_persist_across_restart,
                    kill_switch = excluded.kill_switch,
                    max_concurrency = excluded.max_concurrency,
                    max_task_cost_usd = excluded.max_task_cost_usd,
                    max_daily_cost_usd = excluded.max_daily_cost_usd,
                    max_daily_planner_cost_usd = excluded.max_daily_planner_cost_usd,
                    allow_paid_api_fallback = excluded.allow_paid_api_fallback,
                    allowed_auth_modes_json = excluded.allowed_auth_modes_json,
                    retention_days = excluded.retention_days,
                    allow_cross_project_aggregates = excluded.allow_cross_project_aggregates,
                    updated_at = excluded.updated_at
                """,
                (
                    pid,
                    req.approval_mode,
                    req.auto_expires_at,
                    1 if req.auto_persist_across_restart else 0,
                    1 if req.kill_switch else 0,
                    req.max_concurrency,
                    req.max_task_cost_usd,
                    req.max_daily_cost_usd,
                    req.max_daily_planner_cost_usd,
                    1 if req.allow_paid_api_fallback else 0,
                    json.dumps(req.allowed_auth_modes),
                    req.retention_days,
                    1 if req.allow_cross_project_aggregates else 0,
                    now,
                ),
            )
            self._audit(
                conn,
                project_id=pid,
                actor=req.actor,
                action="policy_updated",
                target_type="policy",
                target_id=pid,
                details={"approval_mode": req.approval_mode, "kill_switch": req.kill_switch},
            )
        return self.get_policy(pid)

    def get_brain_config(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            policy = self._get_policy_row(conn, project_id)
            rows = conn.execute(
                "SELECT * FROM v2_brain_roles WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            if rows:
                roles = [
                    {
                        "role_name": r["role_name"],
                        "preferred_label": r["preferred_label"],
                        "effective_provider": r["effective_provider"],
                        "effective_model_id": r["effective_model_id"],
                        "effective_effort": r["effective_effort"],
                        "auth_mode": r["auth_mode"] or "unknown",
                        "availability_status": r["availability_status"],
                        "availability_reason": r["availability_reason"],
                        "fallback_order": json.loads(r["fallback_order_json"]),
                        "max_escalations": r["max_escalations"],
                        "last_verified_at": r["last_verified_at"],
                    }
                    for r in rows
                ]
            else:
                roles = _default_brain_roles()

            return {
                "available": True,
                "project_id": project_id,
                "approval_mode": policy["approval_mode"],
                "daily_planner_budget_usd": policy["max_daily_planner_cost_usd"],
                "allow_paid_api_fallback": bool(policy["allow_paid_api_fallback"]),
                "allowed_auth_modes": json.loads(policy["allowed_auth_modes_json"]),
                "roles": roles,
            }

    def update_brain_config(self, req: BrainConfigUpdateRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            self._ensure_project(conn, pid)
            conn.execute(
                """
                INSERT INTO v2_policies (
                    project_id, approval_mode, max_daily_planner_cost_usd, allow_paid_api_fallback, allowed_auth_modes_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    approval_mode = excluded.approval_mode,
                    max_daily_planner_cost_usd = excluded.max_daily_planner_cost_usd,
                    allow_paid_api_fallback = excluded.allow_paid_api_fallback,
                    allowed_auth_modes_json = excluded.allowed_auth_modes_json,
                    updated_at = excluded.updated_at
                """,
                (
                    pid,
                    req.approval_mode,
                    req.daily_planner_budget_usd,
                    1 if req.allow_paid_api_fallback else 0,
                    json.dumps(req.allowed_auth_modes),
                    now,
                ),
            )
            for role in req.roles:
                conn.execute(
                    """
                    INSERT INTO v2_brain_roles (
                        id, project_id, role_name, preferred_label, effective_provider, effective_model_id,
                        effective_effort, auth_mode, availability_status, availability_reason,
                        fallback_order_json, max_escalations, last_verified_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, role_name) DO UPDATE SET
                        preferred_label = excluded.preferred_label,
                        effective_provider = excluded.effective_provider,
                        effective_model_id = excluded.effective_model_id,
                        effective_effort = excluded.effective_effort,
                        auth_mode = excluded.auth_mode,
                        availability_status = excluded.availability_status,
                        availability_reason = excluded.availability_reason,
                        fallback_order_json = excluded.fallback_order_json,
                        max_escalations = excluded.max_escalations,
                        last_verified_at = excluded.last_verified_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(uuid.uuid4()),
                        pid,
                        role.role_name,
                        role.preferred_label,
                        role.effective_provider,
                        role.effective_model_id,
                        role.effective_effort,
                        role.auth_mode,
                        role.availability_status,
                        role.availability_reason,
                        json.dumps(role.fallback_order, ensure_ascii=False),
                        role.max_escalations,
                        role.last_verified_at,
                        now,
                    ),
                )
            self._audit(
                conn,
                project_id=pid,
                actor=req.actor,
                action="brain_config_updated",
                target_type="brain_config",
                target_id=pid,
                details={"role_count": len(req.roles), "allow_paid_api_fallback": req.allow_paid_api_fallback},
            )
        return self.get_brain_config(pid)

    # --- 3. Plans, DAG & Atomic Approval ---
    def _validate_dag_and_hash(self, objective: str, tasks: list[Any]) -> str:
        keys = [t.task_key for t in tasks]
        if len(set(keys)) != len(keys):
            raise BridgeError(422, "duplicate_task_key", "Task keys in a plan revision must be unique")
        key_set = set(keys)
        adj: dict[str, list[str]] = {}
        for t in tasks:
            for dep in t.dependencies:
                if dep not in key_set:
                    raise BridgeError(422, "invalid_dependency", f"Unknown dependency task_key '{dep}'")
                if dep == t.task_key:
                    raise BridgeError(422, "dag_cycle", f"Self-dependency on '{dep}'")
            adj[t.task_key] = list(t.dependencies)

        # Cycle check via DFS
        state: dict[str, int] = {}
        def visit(node: str) -> None:
            st = state.get(node, 0)
            if st == 1:
                raise BridgeError(422, "dag_cycle", "Cycle detected in task DAG")
            if st == 2:
                return
            state[node] = 1
            for nxt in adj[node]:
                visit(nxt)
            state[node] = 2

        for k in keys:
            visit(k)

        canonical = json.dumps(
            {"objective": objective, "tasks": [t.model_dump(mode="json") for t in tasks]},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def _insert_revision_tasks(
        self,
        conn: Any,
        *,
        plan_id: str,
        revision_id: str,
        project_id: str,
        worktree_id: str,
        tasks: list[Any],
        now: str,
    ) -> None:
        key_to_task_id: dict[str, str] = {}
        for t in tasks:
            tid = str(uuid.uuid4())
            key_to_task_id[t.task_key] = tid
            conn.execute(
                """
                INSERT INTO v2_tasks (
                    id, plan_id, revision_id, project_id, worktree_id, task_key, title, objective,
                    task_type, complexity, assigned_agent, assigned_provider, assigned_model_id,
                    assigned_effort, auth_mode, owned_paths_json, acceptance_checks_json,
                    budget_input_tokens, budget_output_tokens, budget_cost_usd, max_retries, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?)
                """,
                (
                    tid,
                    plan_id,
                    revision_id,
                    project_id,
                    worktree_id,
                    t.task_key,
                    t.title,
                    t.objective,
                    t.features.task_type,
                    t.features.complexity,
                    t.assigned_agent,
                    t.assigned_provider,
                    t.assigned_model_id,
                    t.assigned_effort,
                    t.auth_mode,
                    json.dumps(t.owned_paths, ensure_ascii=False),
                    json.dumps(t.acceptance_checks, ensure_ascii=False),
                    t.budget_input_tokens,
                    t.budget_output_tokens,
                    t.budget_cost_usd,
                    t.max_retries,
                    now,
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO v2_task_features (
                    id, session_id, task_id, phase, rubric_version, task_type, complexity,
                    language, file_count, total_file_bytes, expected_edits, expected_tests,
                    dependency_count, context_pack_tokens, context_pack_is_approx, uncertainty_level, created_at
                ) VALUES (?, NULL, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    tid,
                    t.features.rubric_version,
                    t.features.task_type,
                    t.features.complexity,
                    t.features.language,
                    t.features.file_count,
                    t.features.total_file_bytes,
                    t.features.expected_edits,
                    t.features.expected_tests,
                    t.features.dependency_count,
                    t.features.context_pack_tokens,
                    1 if t.features.context_pack_is_approx else 0,
                    t.features.uncertainty_level,
                    now,
                ),
            )
        for t in tasks:
            tid = key_to_task_id[t.task_key]
            for dep in t.dependencies:
                conn.execute(
                    "INSERT INTO v2_task_dependencies (task_id, depends_on_task_id) VALUES (?, ?)",
                    (tid, key_to_task_id[dep]),
                )

    def create_plan(self, req: PlanCreateRequest, created_by: str = "planner") -> dict[str, Any]:
        pid = str(req.project_id)
        wid = str(req.worktree_id)
        trace_started = utc_now_iso()
        rev_hash = self._validate_dag_and_hash(req.objective, req.tasks)
        plan_id = str(uuid.uuid4())
        rev_id = str(uuid.uuid4())
        now = utc_now_iso()

        with self.db.write_tx() as conn:
            self._ensure_worktree(conn, pid, wid)
            conn.execute(
                """
                INSERT INTO v2_plans (
                    id, project_id, worktree_id, objective, status, current_revision_id,
                    approved_revision_id, catalog_snapshot_id, created_by, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'REVIEW_REQUIRED', ?, NULL, ?, ?, ?, ?)
                """,
                (plan_id, pid, wid, req.objective, rev_id, req.catalog_snapshot_id, created_by, now, now),
            )
            conn.execute(
                """
                INSERT INTO v2_plan_revisions (
                    id, plan_id, revision_number, revision_hash, plan_json, forecast_id, planner_session_id, created_by, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rev_id,
                    plan_id,
                    rev_hash,
                    json.dumps(req.model_dump(mode="json"), ensure_ascii=False),
                    req.forecast_id,
                    req.planner_session_id,
                    created_by,
                    now,
                ),
            )
            self._insert_revision_tasks(
                conn, plan_id=plan_id, revision_id=rev_id, project_id=pid, worktree_id=wid, tasks=req.tasks, now=now
            )
            self._audit(
                conn,
                project_id=pid,
                actor=created_by,
                action="plan_created",
                target_type="plan",
                target_id=plan_id,
                details={"revision_id": rev_id, "revision_hash": rev_hash, "task_count": len(req.tasks)},
            )
            plan_created(conn, plan_id=plan_id, project_id=pid, worktree_id=wid,
                         actor=created_by, started_at=trace_started, ended_at=utc_now_iso(), task_count=len(req.tasks))
        return self.get_plan(plan_id, pid)

    def _reconcile_expired_leases(self) -> None:
        with self.db.write_tx() as conn:
            reconcile_expired_job_leases(conn, utc_now_iso())

    def list_plans(self, project_id: str) -> dict[str, Any]:
        self._reconcile_expired_leases()
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            rows = conn.execute(
                "SELECT * FROM v2_plans WHERE project_id = ? ORDER BY created_at DESC LIMIT 50",
                (project_id,),
            ).fetchall()
            return {"items": [dict(r) for r in rows]}

    def get_plan(self, plan_id: str, project_id: str) -> dict[str, Any]:
        self._reconcile_expired_leases()
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            plan = conn.execute(
                "SELECT * FROM v2_plans WHERE id = ? AND project_id = ?",
                (plan_id, project_id),
            ).fetchone()
            if not plan:
                raise BridgeError(404, "plan_not_found", "Plan not found for project")
            revs = conn.execute(
                "SELECT id, revision_number, revision_hash, forecast_id, planner_session_id, created_by, created_at FROM v2_plan_revisions WHERE plan_id = ? ORDER BY revision_number DESC",
                (plan_id,),
            ).fetchall()
            tasks = conn.execute(
                "SELECT * FROM v2_tasks WHERE plan_id = ? AND revision_id = ? ORDER BY created_at ASC",
                (plan_id, plan["current_revision_id"]),
            ).fetchall()
            approvals = conn.execute(
                "SELECT * FROM v2_plan_approvals WHERE plan_id = ? ORDER BY created_at DESC",
                (plan_id,),
            ).fetchall()
            return {
                **dict(plan),
                "revisions": [dict(r) for r in revs],
                "editable_tasks": json.loads(conn.execute("SELECT plan_json FROM v2_plan_revisions WHERE id=? AND plan_id=?", (plan["current_revision_id"], plan_id)).fetchone()["plan_json"]).get("tasks", []),
                "tasks": [dict(t) for t in tasks],
                "approvals": [dict(a) for a in approvals],
                "dependencies": [dict(x) for x in conn.execute("SELECT d.task_id, d.depends_on_task_id FROM v2_task_dependencies d JOIN v2_tasks t ON t.id=d.task_id WHERE t.plan_id=? AND t.revision_id=?", (plan_id, plan["current_revision_id"]))],
                "forecast": json.loads(forecast["forecast_json"]) if (forecast := conn.execute(
                    "SELECT f.forecast_json FROM v2_forecast_snapshots f JOIN v2_plan_revisions r "
                    "ON r.forecast_id=f.id WHERE r.id=? AND f.project_id=?",
                    (plan["current_revision_id"], project_id),
                ).fetchone()) else None,
                "attempts": [redact_payload(dict(row)) for row in conn.execute(
                    """SELECT a.id, a.task_id, a.plan_revision_id, a.runner_id, a.status,
                              a.started_at, a.ended_at, a.error_code, a.error_summary,
                              l.heartbeat_at, l.expires_at, s.id AS session_id, o.handoff_summary,
                              o.tests_passed, o.duration_ms, o.actual_changed_files
                       FROM v2_tasks t JOIN v2_task_attempts a ON a.task_id=t.id
                       LEFT JOIN v2_job_leases l ON l.attempt_id=a.id
                       LEFT JOIN v2_sessions s ON s.attempt_id=a.id
                       LEFT JOIN v2_session_outcomes o ON o.session_id=s.id
                       WHERE t.plan_id=? AND t.project_id=?
                       ORDER BY a.started_at DESC LIMIT 100""", (plan_id, project_id)
                )],
            }

    def update_plan_revision(self, plan_id: str, req: PlanRevisionUpdateRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            plan = conn.execute(
                "SELECT * FROM v2_plans WHERE id = ? AND project_id = ?",
                (plan_id, pid),
            ).fetchone()
            if not plan:
                raise BridgeError(404, "plan_not_found", "Plan not found")
            if conn.execute(
                "SELECT 1 FROM v2_job_leases l JOIN v2_tasks t ON t.id=l.task_id "
                "WHERE t.plan_id=? AND l.released_at IS NULL LIMIT 1", (plan_id,),
            ).fetchone() or plan["status"] in ("RUNNING", "COMPLETED", "CANCELLED"):
                raise BridgeError(409, "plan_state_conflict", f"Cannot revise plan in state {plan['status']}")

            unresolved = conn.execute(
                "SELECT 1 FROM v2_tasks WHERE plan_id = ? AND state IN ('leased','running','blocked') LIMIT 1",
                (plan_id,),
            ).fetchone()
            if unresolved:
                raise BridgeError(409, "unresolved_attempt", "Review active or unknown task outcomes before revising")
            if req.expected_revision_id and str(req.expected_revision_id) != plan["current_revision_id"]:
                raise BridgeError(409, "stale_revision", "Reload the current revision before editing")
            rev_hash = self._validate_dag_and_hash(plan["objective"], req.tasks)
            max_rev = conn.execute(
                "SELECT COALESCE(MAX(revision_number), 0) AS n FROM v2_plan_revisions WHERE plan_id = ?",
                (plan_id,),
            ).fetchone()
            new_rev_num = int(max_rev["n"]) + 1
            new_rev_id = str(uuid.uuid4())

            conn.execute(
                """
                INSERT INTO v2_plan_revisions (
                    id, plan_id, revision_number, revision_hash, plan_json, forecast_id, planner_session_id, created_by, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_rev_id,
                    plan_id,
                    new_rev_num,
                    rev_hash,
                    json.dumps(req.model_dump(mode="json"), ensure_ascii=False),
                    req.forecast_id,
                    req.planner_session_id,
                    req.created_by,
                    now,
                ),
            )
            self._insert_revision_tasks(
                conn,
                plan_id=plan_id,
                revision_id=new_rev_id,
                project_id=pid,
                worktree_id=plan["worktree_id"],
                tasks=req.tasks,
                now=now,
            )
            # Material change invalidates any prior approval
            conn.execute(
                """
                UPDATE v2_plans
                SET status = 'REVIEW_REQUIRED', current_revision_id = ?, approved_revision_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (new_rev_id, now, plan_id),
            )
            self._audit(
                conn,
                project_id=pid,
                actor=req.created_by,
                action="plan_revised",
                target_type="plan",
                target_id=plan_id,
                details={"revision_number": new_rev_num, "revision_id": new_rev_id, "revision_hash": rev_hash},
            )
        return self.get_plan(plan_id, pid)

    def approve_plan(self, plan_id: str, project_id: str, req: PlanApproveRequest) -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            self._ensure_project(conn, project_id)
            policy = self._get_policy_row(conn, project_id)
            if policy["kill_switch"]:
                raise BridgeError(403, "kill_switch_active", "Project kill switch is active")

            plan = conn.execute(
                "SELECT * FROM v2_plans WHERE id = ? AND project_id = ?",
                (plan_id, project_id),
            ).fetchone()
            if not plan:
                raise BridgeError(404, "plan_not_found", "Plan not found")
            if plan["current_revision_id"] != str(req.revision_id):
                raise BridgeError(409, "stale_revision", "Requested revision_id is not the current revision")

            rev = conn.execute(
                "SELECT * FROM v2_plan_revisions WHERE id = ? AND plan_id = ?",
                (str(req.revision_id), plan_id),
            ).fetchone()
            if not rev or rev["revision_hash"] != req.revision_hash:
                raise BridgeError(409, "revision_hash_mismatch", "Revision hash mismatch; approval rejected")

            if plan["status"] not in ("DRAFT", "REVIEW_REQUIRED"):
                raise BridgeError(409, "plan_state_conflict", f"Cannot approve plan in state {plan['status']}")

            conn.execute(
                """
                INSERT INTO v2_plan_approvals (
                    id, plan_id, revision_id, revision_hash, decision, actor, mode, reason, expires_at, created_at
                ) VALUES (?, ?, ?, ?, 'approved', ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    plan_id,
                    str(req.revision_id),
                    req.revision_hash,
                    req.actor,
                    req.mode,
                    req.reason,
                    req.auto_expires_at,
                    now,
                ),
            )
            conn.execute(
                "UPDATE v2_plans SET status = 'APPROVED', approved_revision_id = ?, updated_at = ? WHERE id = ?",
                (str(req.revision_id), now, plan_id),
            )
            # Atomic budget reservations per task
            tasks = conn.execute(
                "SELECT id, assigned_provider, auth_mode, budget_cost_usd, budget_input_tokens, budget_output_tokens FROM v2_tasks WHERE revision_id = ?",
                (str(req.revision_id),),
            ).fetchall()
            for t in tasks:
                tok_budget = None
                if t["budget_input_tokens"] is not None or t["budget_output_tokens"] is not None:
                    tok_budget = (t["budget_input_tokens"] or 0) + (t["budget_output_tokens"] or 0)
                conn.execute(
                    """
                    INSERT INTO v2_budget_reservations (
                        id, project_id, plan_id, task_id, provider, auth_mode, reserved_cost_usd, reserved_tokens, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'reserved', ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        project_id,
                        plan_id,
                        t["id"],
                        t["assigned_provider"],
                        t["auth_mode"],
                        t["budget_cost_usd"],
                        tok_budget,
                        now,
                    ),
                )
            self._audit(
                conn,
                project_id=project_id,
                actor=req.actor,
                action="plan_approved",
                target_type="plan",
                target_id=plan_id,
                details={"revision_id": str(req.revision_id), "mode": req.mode},
            )
        return self.get_plan(plan_id, project_id)

    def transition_plan(self, plan_id: str, action: str, req: PlanLifecycleRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        target_status = {
            "reject": "REJECTED",
            "pause": "PAUSED",
            "resume": "APPROVED",
            "cancel": "CANCELLED",
        }[action]
        with self.db.write_tx() as conn:
            plan = conn.execute(
                "SELECT * FROM v2_plans WHERE id = ? AND project_id = ?",
                (plan_id, pid),
            ).fetchone()
            if not plan:
                raise BridgeError(404, "plan_not_found", "Plan not found")
            if action == "resume":
                if plan["status"] != "PAUSED" or not plan["approved_revision_id"]:
                    raise BridgeError(409, "no_approved_revision", "Only paused approved plans can resume")
                blocked = conn.execute(
                    "SELECT 1 FROM v2_tasks WHERE revision_id = ? AND state = 'blocked' LIMIT 1",
                    (plan["approved_revision_id"],),
                ).fetchone()
                if blocked:
                    raise BridgeError(409, "unknown_outcome", "Review blocked task outcomes before creating a new approved plan")
            conn.execute(
                "UPDATE v2_plans SET status = ?, updated_at = ? WHERE id = ?",
                (target_status, now, plan_id),
            )
            if action in ("reject", "cancel"):
                conn.execute(
                    "UPDATE v2_budget_reservations SET status = 'released', settled_at = ? WHERE plan_id = ? AND status = 'reserved'",
                    (now, plan_id),
                )
                if action == "cancel":
                    conn.execute(
                        "UPDATE v2_tasks SET state = 'cancelled', updated_at = ? WHERE plan_id = ? AND state IN ('queued', 'leased', 'running')",
                        (now, plan_id),
                    )
            if action == "reject" and plan["current_revision_id"]:
                rev = conn.execute("SELECT revision_hash FROM v2_plan_revisions WHERE id = ?", (plan["current_revision_id"],)).fetchone()
                conn.execute(
                    """
                    INSERT INTO v2_plan_approvals (id, plan_id, revision_id, revision_hash, decision, actor, mode, reason, created_at)
                    VALUES (?, ?, ?, ?, 'rejected', ?, 'manual', ?, ?)
                    """,
                    (str(uuid.uuid4()), plan_id, plan["current_revision_id"], rev["revision_hash"], req.actor, req.reason, now),
                )
            self._audit(
                conn,
                project_id=pid,
                actor=req.actor,
                action=f"plan_{action}",
                target_type="plan",
                target_id=plan_id,
                details={"reason": req.reason},
            )
        return self.get_plan(plan_id, pid)

    # --- 4. Narrow Authenticated Host Runner Jobs ---
    def claim_job(self, req: JobClaimRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        wid = str(req.worktree_id)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=req.lease_seconds)).isoformat()

        with self.db.write_tx() as conn:
            worktree = self._ensure_worktree(conn, pid, wid)
            policy = self._get_policy_row(conn, pid)
            if policy["kill_switch"]:
                raise BridgeError(403, "kill_switch_active", "Project kill switch is active")

            reconcile_expired_job_leases(conn, now)

            # Enforce concurrency limit
            active_leases = conn.execute(
                """
                SELECT COUNT(*) AS cnt FROM v2_job_leases l
                JOIN v2_tasks t ON t.id = l.task_id
                WHERE t.project_id = ? AND l.released_at IS NULL AND l.expires_at > ?
                """,
                (pid, now),
            ).fetchone()
            if int(active_leases["cnt"]) >= int(policy["max_concurrency"]):
                return {"claimed": False, "reason": "max_concurrency_reached"}

            # Find queued task belonging to an APPROVED/RUNNING plan matching approved_revision_id with all dependencies succeeded
            candidates = conn.execute(
                """
                SELECT t.*, p.approved_revision_id, r.revision_hash
                FROM v2_tasks t
                JOIN v2_plans p ON p.id = t.plan_id
                JOIN v2_plan_revisions r ON r.id = t.revision_id
                WHERE t.project_id = ? AND t.worktree_id = ? AND t.state = 'queued'
                  AND p.status IN ('APPROVED', 'RUNNING')
                  AND p.approved_revision_id = t.revision_id AND p.current_revision_id = t.revision_id
                  AND (? IS NULL OR p.id = ?)
                ORDER BY t.created_at ASC
                """,
                (pid, wid, str(req.plan_id) if req.plan_id else None, str(req.plan_id) if req.plan_id else None),
            ).fetchall()

            chosen: dict[str, Any] | None = None
            for cand in candidates:
                unmet = conn.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM v2_task_dependencies d
                    JOIN v2_tasks dep ON dep.id = d.depends_on_task_id
                    WHERE d.task_id = ? AND dep.state != 'succeeded'
                    """,
                    (cand["id"],),
                ).fetchone()
                if int(unmet["cnt"]) == 0:
                    chosen = dict(cand)
                    break

            if not chosen:
                return {"claimed": False, "reason": "no_ready_tasks"}

            att_row = conn.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) AS n FROM v2_task_attempts WHERE task_id = ?",
                (chosen["id"],),
            ).fetchone()
            attempt_number = int(att_row["n"]) + 1
            attempt_id = str(uuid.uuid4())
            lease_id = str(uuid.uuid4())
            session_id = str(uuid.uuid4())

            conn.execute(
                """
                INSERT INTO v2_task_attempts (
                    id, task_id, attempt_number, plan_revision_id, plan_revision_hash, runner_id, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'claimed', ?)
                """,
                (attempt_id, chosen["id"], attempt_number, chosen["revision_id"], chosen["revision_hash"], req.runner_id, now),
            )
            conn.execute(
                """
                INSERT INTO v2_job_leases (lease_id, attempt_id, task_id, runner_id, claimed_at, heartbeat_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (lease_id, attempt_id, chosen["id"], req.runner_id, now, now, expires_at),
            )
            conn.execute(
                """
                INSERT INTO v2_sessions (
                    id, project_id, worktree_id, plan_id, plan_revision_id, task_id, attempt_id,
                    role, provider, model_id, reasoning_effort, auth_mode, git_head, dirty_fingerprint, status, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'executor', ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    session_id,
                    pid,
                    wid,
                    chosen["plan_id"],
                    chosen["revision_id"],
                    chosen["id"],
                    attempt_id,
                    chosen["assigned_provider"],
                    chosen["assigned_model_id"],
                    chosen["assigned_effort"],
                    chosen["auth_mode"],
                    worktree["head_commit"],
                    worktree["dirty_fingerprint"],
                    now,
                ),
            )
            conn.execute("UPDATE v2_tasks SET state = 'leased', updated_at = ? WHERE id = ?", (now, chosen["id"]))
            conn.execute("UPDATE v2_plans SET status = 'RUNNING', updated_at = ? WHERE id = ? AND status = 'APPROVED'", (now, chosen["plan_id"]))

            agent_claimed(conn, task=chosen, attempt_id=attempt_id, session_id=session_id,
                          runner_id=req.runner_id, started_at=now, attempt_number=attempt_number)
            return {
                "claimed": True,
                "root_trace_id": conn.execute("SELECT root_trace_id FROM v22_execution_traces WHERE trace_id=?", (trace_key("attempt",attempt_id),)).fetchone()[0],
                "job_id": chosen["id"],
                "task_id": chosen["id"],
                "task_key": chosen["task_key"],
                "attempt_id": attempt_id,
                "attempt_number": attempt_number,
                "lease_id": lease_id,
                "lease_expires_at": expires_at,
                "session_id": session_id,
                "plan_id": chosen["plan_id"],
                "plan_revision_id": chosen["revision_id"],
                "revision_hash": chosen["revision_hash"],
                "project_id": pid,
                "worktree_id": wid,
                "worktree_path": worktree["canonical_root"],
                "assigned_agent": chosen["assigned_agent"],
                "provider": chosen["assigned_provider"],
                "model_id": chosen["assigned_model_id"],
                "effort": chosen["assigned_effort"],
                "auth_mode": chosen["auth_mode"],
                "owned_paths": json.loads(chosen["owned_paths_json"]),
                "acceptance_checks": json.loads(chosen["acceptance_checks_json"]),
                "budgets": {
                    "input_tokens": chosen["budget_input_tokens"],
                    "output_tokens": chosen["budget_output_tokens"],
                    "cost_usd": chosen["budget_cost_usd"],
                },
                "context_pack": {
                    "objective": chosen["objective"],
                    "git_head": worktree["head_commit"],
                    "dirty_fingerprint": worktree["dirty_fingerprint"],
                },
            }

    def _verify_active_lease(self, conn: Any, job_id: str, project_id: str, lease_id: str) -> dict[str, Any]:
        row = conn.execute(
            """
            SELECT l.*, t.project_id, t.worktree_id, t.plan_id, t.revision_id, s.id AS session_id
            FROM v2_job_leases l
            JOIN v2_tasks t ON t.id = l.task_id
            JOIN v2_plans p ON p.id = t.plan_id
            LEFT JOIN v2_sessions s ON s.attempt_id = l.attempt_id
            WHERE l.task_id = ? AND t.project_id = ? AND l.lease_id = ? AND l.released_at IS NULL
              AND julianday(l.expires_at) > julianday(?)
              AND t.state IN ('leased', 'running')
              AND p.approved_revision_id = t.revision_id
              AND p.status IN ('APPROVED', 'RUNNING', 'PAUSED')
            """,
            (job_id, project_id, lease_id, utc_now_iso()),
        ).fetchone()
        if not row:
            raise BridgeError(409, "lease_invalid", "Active lease not found for job")
        return dict(row)

    def heartbeat_job(self, job_id: str, req: JobHeartbeatRequest) -> dict[str, Any]:
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(seconds=req.lease_seconds)).isoformat()
        with self.db.write_tx() as conn:
            lease = self._verify_active_lease(conn, job_id, str(req.project_id), str(req.lease_id))
            if lease["runner_id"] != req.runner_id:
                raise BridgeError(409, "runner_mismatch", "Lease belongs to a different runner")
            conn.execute(
                "UPDATE v2_job_leases SET heartbeat_at = ?, expires_at = ? WHERE lease_id = ?",
                (now, expires_at, str(req.lease_id)),
            )
            conn.execute(
                "UPDATE v2_task_attempts SET status = 'running' WHERE id = ? AND status = 'claimed'",
                (lease["attempt_id"],),
            )
            conn.execute(
                "UPDATE v2_tasks SET state = 'running', updated_at = ? WHERE id = ? AND state = 'leased'",
                (now, job_id),
            )
            agent_heartbeat(conn, lease["attempt_id"], now)
        return {"job_id": job_id, "lease_id": str(req.lease_id), "lease_expires_at": expires_at}

    def record_job_event(self, job_id: str, req: JobEventRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        with self.db.write_tx() as conn:
            lease = self._verify_active_lease(conn, job_id, pid, str(req.lease_id))
            existing = conn.execute(
                "SELECT id FROM events WHERE project_id = ? AND worktree_id = ? AND idempotency_key = ?",
                (pid, lease["worktree_id"], req.idempotency_key),
            ).fetchone()
            if existing:
                return {"event_id": existing["id"], "deduplicated": True}
            ev_id = str(uuid.uuid4())
            redacted = json.dumps(redact_payload(req.payload), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO events (
                    id, idempotency_key, payload_hash, project_id, worktree_id, task_id,
                    source_agent, kind, payload_json, approved, redacted_payload_json, processed_by_worker, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'runner', ?, ?, 0, ?, 0, ?)
                """,
                (ev_id, req.idempotency_key, hashlib.sha256(redacted.encode()).hexdigest(), pid, lease["worktree_id"], job_id, req.kind, redacted, redacted, utc_now_iso()),
            )
            return {"event_id": ev_id, "deduplicated": False}

    def checkpoint_job(self, job_id: str, req: JobCheckpointRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            lease = self._verify_active_lease(conn, job_id, pid, str(req.lease_id))
            parent_sid = lease["session_id"]
            if parent_sid:
                conn.execute(
                    "UPDATE v2_sessions SET status = 'checkpointed', ended_at = ? WHERE id = ?",
                    (now, parent_sid),
                )
                conn.execute(
                    """
                    INSERT INTO v2_session_outcomes (session_id, accepted, peak_context_tokens, handoff_summary, recorded_at)
                    VALUES (?, NULL, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        peak_context_tokens = excluded.peak_context_tokens,
                        handoff_summary = excluded.handoff_summary,
                        recorded_at = excluded.recorded_at
                    """,
                    (parent_sid, req.peak_context_tokens, redact_payload(req.handoff_summary), now),
                )
            conn.execute("UPDATE v2_tasks SET state = 'checkpointed', updated_at = ? WHERE id = ?", (now, job_id))
            conn.execute("UPDATE v2_task_attempts SET status = 'checkpointed' WHERE id = ?", (lease["attempt_id"],))
            conn.execute("UPDATE v2_job_leases SET released_at = ? WHERE lease_id = ?", (now, str(req.lease_id)))
            agent_finished(conn, lease["attempt_id"], "unknown", now, "checkpointed", "Checkpoint saved; final outcome not yet known")
            return {"job_id": job_id, "checkpointed": True, "parent_session_id": parent_sid}

    def complete_job(self, job_id: str, req: JobCompleteRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            lease = self._verify_active_lease(conn, job_id, pid, str(req.lease_id))
            conn.execute("UPDATE v2_job_leases SET released_at = ? WHERE lease_id = ?", (now, str(req.lease_id)))
            conn.execute(
                """
                UPDATE v2_task_attempts
                SET status = ?, ended_at = ?, error_code = ?, error_summary = ?
                WHERE id = ?
                """,
                (req.outcome, now, req.error_code, redact_payload(req.error_summary), lease["attempt_id"]),
            )
            task_state = req.outcome if req.outcome in ("succeeded", "failed", "cancelled") else "failed"
            conn.execute("UPDATE v2_tasks SET state = ?, updated_at = ? WHERE id = ?", (task_state, now, job_id))

            sid = lease["session_id"]
            if sid:
                sess_status = "completed" if req.outcome == "succeeded" else ("cancelled" if req.outcome == "cancelled" else "failed")
                conn.execute("UPDATE v2_sessions SET status = ?, ended_at = ? WHERE id = ?", (sess_status, now, sid))
                conn.execute(
                    """
                    INSERT INTO v2_session_outcomes (
                        session_id, accepted, tests_passed, duration_ms, actual_changed_files, peak_context_tokens, handoff_summary, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        accepted = excluded.accepted,
                        tests_passed = excluded.tests_passed,
                        duration_ms = excluded.duration_ms,
                        actual_changed_files = excluded.actual_changed_files,
                        peak_context_tokens = excluded.peak_context_tokens,
                        handoff_summary = excluded.handoff_summary,
                        recorded_at = excluded.recorded_at
                    """,
                    (
                        sid,
                        (1 if req.accepted else 0) if req.accepted is not None else None,
                        (1 if req.tests_passed else 0) if req.tests_passed is not None else None,
                        req.duration_ms,
                        req.actual_changed_files,
                        req.peak_context_tokens,
                        redact_payload(req.handoff_summary),
                        now,
                    ),
                )

            # Settle budget reservation
            conn.execute(
                "UPDATE v2_budget_reservations SET status = 'settled', settled_at = ? WHERE task_id = ? AND status = 'reserved'",
                (now, job_id),
            )
            # Check if all tasks in revision are finished
            rem = conn.execute(
                "SELECT COUNT(*) AS cnt FROM v2_tasks WHERE revision_id = ? AND state NOT IN ('succeeded', 'failed', 'cancelled')",
                (lease["revision_id"],),
            ).fetchone()
            if int(rem["cnt"]) == 0:
                failed = conn.execute(
                    "SELECT COUNT(*) AS cnt FROM v2_tasks WHERE revision_id = ? AND state IN ('failed', 'cancelled')",
                    (lease["revision_id"],),
                ).fetchone()
                plan_status = "FAILED" if int(failed["cnt"]) > 0 else "COMPLETED"
                conn.execute("UPDATE v2_plans SET status = ?, updated_at = ? WHERE id = ?", (plan_status, now, lease["plan_id"]))
                self._reconcile_plan_forecast_actuals(conn, lease["plan_id"], lease["revision_id"], now)

            trace_status = req.outcome if req.outcome in ("succeeded", "failed", "cancelled") else "blocked"
            agent_finished(conn, lease["attempt_id"], trace_status, now, req.error_code, req.error_summary)
        return {"job_id": job_id, "outcome": req.outcome, "completed_at": now}

    def cancel_job(self, job_id: str, project_id: str, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            task = conn.execute("SELECT * FROM v2_tasks WHERE id = ? AND project_id = ?", (job_id, project_id)).fetchone()
            if not task:
                raise BridgeError(404, "job_not_found", "Job not found")
            conn.execute("UPDATE v2_tasks SET state = 'cancelled', updated_at = ? WHERE id = ?", (now, job_id))
            conn.execute(
                "UPDATE v2_job_leases SET released_at = ? WHERE task_id = ? AND released_at IS NULL",
                (now, job_id),
            )
            conn.execute(
                "UPDATE v2_task_attempts SET status = 'cancelled', ended_at = ? WHERE task_id = ? AND status IN ('claimed', 'running')",
                (now, job_id),
            )
            conn.execute(
                "UPDATE v2_budget_reservations SET status = 'released', settled_at = ? WHERE task_id = ? AND status = 'reserved'",
                (now, job_id),
            )
            for attempt in conn.execute("SELECT id FROM v2_task_attempts WHERE task_id=? AND status='cancelled' AND ended_at=?", (job_id,now)).fetchall():
                agent_finished(conn, attempt["id"], "cancelled", now)
            self._audit(conn, project_id=project_id, actor=actor, action="job_cancelled", target_type="task", target_id=job_id)
        return {"job_id": job_id, "state": "cancelled"}

    # --- 5. Sessions, Usage Deduplication, Quotas & Analytics ---
    def upsert_session(self, req: SessionUpsertRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        wid = str(req.worktree_id)
        sid = str(req.session_id or uuid.uuid4())
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            self._ensure_worktree(conn, pid, wid)
            existing = conn.execute("SELECT id, project_id, worktree_id, role, status, attempt_id FROM v2_sessions WHERE id = ?", (sid,)).fetchone()
            if existing:
                if existing["project_id"] != pid or existing["worktree_id"] != wid or existing["role"] != req.role:
                    raise BridgeError(404, "session_not_found", "Session not found for project")
                # A late hook must not reopen a completed session; managed lifecycle is lease-owned.
                if existing["attempt_id"] is None and existing["status"] not in ("completed", "failed", "cancelled"):
                    conn.execute(
                        "UPDATE v2_sessions SET status = ?, ended_at = CASE WHEN ? != 'running' THEN ? ELSE NULL END, model_id = CASE WHEN model_id = 'unknown' THEN ? ELSE model_id END WHERE id = ?",
                        (req.status, req.status, now, req.model_id, sid),
                    )
            else:
                conn.execute(
                    """
                    INSERT INTO v2_sessions (
                        id, project_id, worktree_id, plan_id, plan_revision_id, task_id, attempt_id, parent_session_id,
                        role, provider, model_id, cli_version, reasoning_effort, auth_mode, account_bucket,
                        upstream_session_id, git_head, dirty_fingerprint, status, unlinked, incomplete_coverage, started_at, ended_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sid,
                        pid,
                        wid,
                        str(req.plan_id) if req.plan_id else None,
                        str(req.plan_revision_id) if req.plan_revision_id else None,
                        str(req.task_id) if req.task_id else None,
                        str(req.attempt_id) if req.attempt_id else None,
                        str(req.parent_session_id) if req.parent_session_id else None,
                        req.role,
                        req.provider,
                        req.model_id,
                        req.cli_version,
                        req.reasoning_effort,
                        req.auth_mode,
                        req.account_bucket,
                        req.upstream_session_id,
                        req.git_head,
                        req.dirty_fingerprint,
                        req.status,
                        1 if req.unlinked else 0,
                        1 if req.incomplete_coverage else 0,
                        now,
                        now if req.status != "running" else None,
                    ),
                )
                if req.features:
                    conn.execute(
                        """
                        INSERT INTO v2_task_features (
                            id, session_id, task_id, phase, rubric_version, task_type, complexity,
                            language, file_count, total_file_bytes, expected_edits, expected_tests,
                            dependency_count, context_pack_tokens, context_pack_is_approx, uncertainty_level, created_at
                        ) VALUES (?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            str(uuid.uuid4()),
                            sid,
                            str(req.task_id) if req.task_id else None,
                            req.features.rubric_version,
                            req.features.task_type,
                            req.features.complexity,
                            req.features.language,
                            req.features.file_count,
                            req.features.total_file_bytes,
                            req.features.expected_edits,
                            req.features.expected_tests,
                            req.features.dependency_count,
                            req.features.context_pack_tokens,
                            1 if req.features.context_pack_is_approx else 0,
                            req.features.uncertainty_level,
                            now,
                        ),
                    )
        return self.get_session(sid, pid)

    def _aggregate_session_counters(self, conn: Any, session_id: str) -> dict[str, Any]:
        rows = conn.execute(
            "SELECT * FROM v2_usage_events WHERE session_id = ? ORDER BY sequence_no ASC, observed_at ASC",
            (session_id,),
        ).fetchall()
        if not rows:
            return {
                "input_tokens": None,
                "output_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "cost_usd": None,
                "measurement_kind": "unavailable",
            }

        fields = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "cost_usd")
        totals: dict[str, float | int] = {}
        kind = "unavailable"
        for r in rows:
            if r["measurement_kind"] == "measured":
                kind = "measured"
            elif r["measurement_kind"] == "estimated" and kind == "unavailable":
                kind = "estimated"
            for f in fields:
                if r[f] is not None:
                    # A cumulative event covers previous increments; later deltas extend it.
                    totals[f] = r[f] if r["counter_mode"] == "cumulative" else totals.get(f, 0) + r[f]

        out: dict[str, Any] = {"measurement_kind": kind, "sources": sorted({r["source"] for r in rows}), "observed_at": max(r["observed_at"] for r in rows)}
        for f in fields:
            if f in totals:
                val = totals[f]
                out[f] = round(float(val), 6) if f == "cost_usd" else int(val)
            else:
                out[f] = None
        return out

    def list_sessions(
        self,
        project_id: str,
        *,
        worktree_id: str | None = None,
        task_type: str | None = None,
        complexity: str | None = None,
        model_id: str | None = None,
        effort: str | None = None,
        outcome: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        self._reconcile_expired_leases()
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            clauses = ["s.project_id = ?"]
            params: list[Any] = [project_id]
            if worktree_id:
                clauses.append("s.worktree_id = ?")
                params.append(worktree_id)
            if model_id:
                clauses.append("s.model_id = ?")
                params.append(model_id)
            if effort:
                clauses.append("s.reasoning_effort = ?")
                params.append(effort)
            if outcome:
                clauses.append("s.status = ?")
                params.append(outcome)
            if task_type:
                clauses.append("COALESCE(tf.task_type, t.task_type) = ?")
                params.append(task_type)
            if complexity:
                clauses.append("COALESCE(tf.complexity, t.complexity) = ?")
                params.append(complexity)
            if from_date:
                clauses.append("s.started_at >= ?")
                params.append(from_date)
            if to_date:
                clauses.append("s.started_at <= ?")
                params.append(to_date)

            rows = conn.execute(
                f"""
                SELECT s.*, COALESCE(tf.task_type, t.task_type) AS task_type, COALESCE(tf.complexity, t.complexity) AS complexity, so.accepted, so.tests_passed, so.duration_ms,
                       so.retries, so.peak_context_tokens, a.runner_id, a.status AS attempt_state,
                       a.error_code, l.heartbeat_at, l.expires_at, w.branch, t.title AS task_title
                FROM v2_sessions s
                LEFT JOIN v2_task_features tf ON tf.session_id = s.id AND tf.phase = 'planned'
                LEFT JOIN v2_session_outcomes so ON so.session_id = s.id
                LEFT JOIN v2_task_attempts a ON a.id=s.attempt_id
                LEFT JOIN v2_job_leases l ON l.attempt_id=a.id
                JOIN worktrees w ON w.id=s.worktree_id
                LEFT JOIN v2_tasks t ON t.id=s.task_id
                WHERE {' AND '.join(clauses)}
                ORDER BY s.started_at DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["usage"] = self._aggregate_session_counters(conn, r["id"])
                items.append(d)
            return {"items": items}

    def get_session(self, session_id: str, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            row = conn.execute(
                "SELECT * FROM v2_sessions WHERE id = ? AND project_id = ?",
                (session_id, project_id),
            ).fetchone()
            if not row:
                raise BridgeError(404, "session_not_found", "Session not found for project")
            features = conn.execute(
                "SELECT * FROM v2_task_features WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            ).fetchall()
            outcome = conn.execute(
                "SELECT * FROM v2_session_outcomes WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            return {
                **dict(row),
                "features": [dict(f) for f in features],
                "outcome": dict(outcome) if outcome else None,
                "usage": self._aggregate_session_counters(conn, session_id),
            }

    def delete_session(self, session_id: str, project_id: str, actor: str = "admin") -> dict[str, Any]:
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT id FROM v2_sessions WHERE id = ? AND project_id = ?",
                (session_id, project_id),
            ).fetchone()
            if not row:
                raise BridgeError(404, "session_not_found", "Session not found for project")
            conn.execute("DELETE FROM v22_execution_traces WHERE session_id=? AND step_type='session_usage'", (session_id,))
            conn.execute("DELETE FROM v2_sessions WHERE id = ?", (session_id,))
            self._audit(
                conn,
                project_id=project_id,
                actor=actor,
                action="session_deleted_retention",
                target_type="session",
                target_id=session_id,
            )
        return {"deleted": True, "session_id": session_id}

    def record_usage(self, req: UsageEventCreateRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        wid = str(req.worktree_id)
        sid = str(req.session_id)
        now = req.usage.observed_at or utc_now_iso()
        with self.db.write_tx() as conn:
            self._ensure_worktree(conn, pid, wid)
            sess = conn.execute(
                "SELECT id, project_id FROM v2_sessions WHERE id = ? AND project_id = ? AND worktree_id = ?",
                (sid, pid, wid),
            ).fetchone()
            if not sess:
                raise BridgeError(404, "session_not_found", "Session not found for project")
            existing = conn.execute(
                "SELECT id FROM v2_usage_events WHERE session_id = ? AND source_event_id = ?",
                (sid, req.source_event_id),
            ).fetchone()
            if existing:
                return {"usage_event_id": existing["id"], "deduplicated": True}

            uid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO v2_usage_events (
                    id, source_event_id, session_id, project_id, worktree_id, sequence_no,
                    counter_mode, measurement_kind, input_tokens, output_tokens, cache_read_tokens,
                    cache_write_tokens, reasoning_tokens, cost_usd, pricing_version, unit, source, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'tokens', ?, ?)
                """,
                (
                    uid,
                    req.source_event_id,
                    sid,
                    pid,
                    wid,
                    req.sequence_no,
                    req.usage.counter_mode,
                    req.usage.measurement_kind,
                    req.usage.input_tokens,
                    req.usage.output_tokens,
                    req.usage.cache_read_tokens,
                    req.usage.cache_write_tokens,
                    req.usage.reasoning_tokens,
                    req.usage.cost_usd,
                    req.pricing_version,
                    req.usage.source,
                    now,
                ),
            )
            sync_usage_trace(conn, sid)
            return {"usage_event_id": uid, "deduplicated": False}

    def get_usage_summary(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            sessions = conn.execute(
                "SELECT id, role FROM v2_sessions WHERE project_id = ?",
                (project_id,),
            ).fetchall()

            def sum_role(target_roles: set[str]) -> dict[str, Any]:
                totals: dict[str, float | int | None] = {
                    "input_tokens": None,
                    "output_tokens": None,
                    "cache_read_tokens": None,
                    "cache_write_tokens": None,
                    "reasoning_tokens": None,
                    "cost_usd": None,
                }
                kind = "unavailable"
                coverage = {"measured": 0, "estimated": 0, "unavailable": 0}
                for s in sessions:
                    if s["role"] not in target_roles:
                        continue
                    c = self._aggregate_session_counters(conn, s["id"])
                    coverage[c["measurement_kind"]] += 1
                    if c["measurement_kind"] == "measured":
                        kind = "measured"
                    elif c["measurement_kind"] == "estimated" and kind == "unavailable":
                        kind = "estimated"
                    for f in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens", "cost_usd"):
                        if c[f] is not None:
                            totals[f] = (totals[f] or 0) + c[f]
                if totals["cost_usd"] is not None:
                    totals["cost_usd"] = round(float(totals["cost_usd"]), 6)
                totals["measurement_kind"] = kind
                totals["coverage"] = coverage
                return totals

            quotas = self._latest_quotas(conn)
            return {
                "available": True,
                "project_id": project_id,
                "planner": sum_role({"planner"}),
                "executor": sum_role({"executor", "reviewer", "manual_cli"}),
                "quotas": quotas,
            }

    def record_quota(self, req: QuotaSnapshotCreateRequest) -> dict[str, Any]:
        qid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO v2_quota_snapshots (
                    id, provider, auth_mode, account_bucket, quota_kind, unit,
                    numerator, denominator, window_label, reset_at, source, confounded, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qid,
                    req.provider,
                    req.auth_mode,
                    req.account_bucket,
                    req.quota_kind,
                    req.unit,
                    req.numerator,
                    req.denominator,
                    req.window_label,
                    req.reset_at,
                    req.source,
                    1 if req.confounded else 0,
                    req.observed_at,
                ),
            )
        return {"quota_id": qid}

    def _latest_quotas(self, conn: Any) -> list[dict[str, Any]]:
        rows = conn.execute(
            "SELECT * FROM v2_quota_snapshots ORDER BY observed_at DESC LIMIT 50"
        ).fetchall()
        seen: set[tuple[str, str, str, str | None]] = set()
        items = []
        for r in rows:
            key = (r["provider"], r["auth_mode"], r["quota_kind"], r["account_bucket"])
            if key in seen:
                continue
            seen.add(key)
            pct = None
            if r["numerator"] is not None and r["denominator"] is not None and r["denominator"] > 0:
                pct = round((float(r["numerator"]) / float(r["denominator"])) * 100.0, 2)
            items.append(
                {
                    "id": r["id"],
                    "provider": r["provider"],
                    "auth_mode": r["auth_mode"],
                    "account_bucket": r["account_bucket"],
                    "quota_kind": r["quota_kind"],
                    "unit": r["unit"],
                    "numerator": r["numerator"],
                    "denominator": r["denominator"],
                    "percentage": pct,
                    "display_value": f"{pct}%" if pct is not None else "Không có dữ liệu",
                    "window_label": r["window_label"],
                    "reset_at": r["reset_at"],
                    "source": r["source"],
                    "confounded": bool(r["confounded"]),
                    "observed_at": r["observed_at"],
                }
            )
        return items

    def get_quotas(self) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            return {"items": self._latest_quotas(conn)}

    # --- 6. Cohorts, Deterministic Forecasts & Calibration ---
    def get_cohorts(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            rows = conn.execute(
                """
                SELECT s.id, s.provider, s.model_id, s.auth_mode, s.reasoning_effort,
                       tf.task_type, tf.complexity, so.accepted, so.retries
                FROM v2_sessions s
                LEFT JOIN v2_task_features tf ON tf.session_id = s.id AND tf.phase = 'planned'
                LEFT JOIN v2_session_outcomes so ON so.session_id = s.id
                WHERE s.project_id = ?
                """,
                (project_id,),
            ).fetchall()

            groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
            for r in rows:
                key = (
                    r["provider"],
                    r["model_id"],
                    r["auth_mode"],
                    r["task_type"] or "unknown",
                    r["complexity"] or "M",
                )
                c = self._aggregate_session_counters(conn, r["id"])
                total_tok = None
                if c["input_tokens"] is not None or c["output_tokens"] is not None:
                    total_tok = (c["input_tokens"] or 0) + (c["output_tokens"] or 0)
                groups.setdefault(key, []).append(
                    {
                        "total_tokens": total_tok,
                        "cost_usd": c["cost_usd"],
                        "accepted": r["accepted"],
                        "retries": r["retries"] or 0,
                    }
                )

            cohorts = []
            for (prov, mod, auth, ttype, comp), samples in groups.items():
                toks = sorted(float(s["total_tokens"]) for s in samples if s["total_tokens"] is not None)
                costs = sorted(float(s["cost_usd"]) for s in samples if s["cost_usd"] is not None)
                accepted_cnt = sum(1 for s in samples if s["accepted"] == 1)
                retry_cnt = sum(1 for s in samples if s["retries"] > 0)
                cohorts.append(
                    {
                        "provider": prov,
                        "model_id": mod,
                        "auth_mode": auth,
                        "task_type": ttype,
                        "complexity": comp,
                        "sample_count": len(samples),
                        "p50_tokens": int(_percentile(toks, 0.5)) if toks else None,
                        "p90_tokens": int(_percentile(toks, 0.9)) if toks else None,
                        "cost_per_accepted_task": (
                            round(sum(costs) / accepted_cnt, 6) if costs and accepted_cnt > 0 else None
                        ),
                        "retry_rate": round(retry_cnt / len(samples), 3) if samples else 0.0,
                    }
                )
            return {"items": cohorts}

    def compute_forecast(self, req: ForecastComputeRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        with self.db.read_conn() as conn:
            self._ensure_worktree(conn, pid, str(req.worktree_id))
            cohorts = self.get_cohorts(pid)["items"]
            cohort_map = {
                (c["provider"], c["model_id"], c["auth_mode"], c["task_type"], c["complexity"]): c
                for c in cohorts
            }

        task_forecasts: list[TaskForecast] = []
        min_samples = None
        for t in req.tasks:
            c = cohort_map.get((t.assigned_provider, t.assigned_model_id, t.auth_mode, t.features.task_type, t.features.complexity))
            n = c["sample_count"] if c else 0
            min_samples = n if min_samples is None else min(min_samples, n)
            if n >= 20:
                conf = "medium"
            elif n >= 5:
                conf = "low"
            else:
                conf = "cold_start"
            p50 = c["p50_tokens"] if c else None
            p90 = c["p90_tokens"] if c else None
            task_forecasts.append(
                TaskForecast(
                    task_key=t.task_key,
                    provider=t.assigned_provider,
                    model_id=t.assigned_model_id,
                    effort=t.assigned_effort,
                    auth_mode=t.auth_mode,
                    complexity=t.features.complexity,
                    confidence=conf,
                    sample_count=n,
                    input_tokens_p50=int(p50 * 0.7) if p50 is not None else None,
                    input_tokens_p90=int(p90 * 0.7) if p90 is not None else None,
                    output_tokens_p50=int(p50 * 0.3) if p50 is not None else None,
                    output_tokens_p90=int(p90 * 0.3) if p90 is not None else None,
                    cost_usd_p50=c["cost_per_accepted_task"] if c else None,
                    cost_usd_p90=c["cost_per_accepted_task"] if c else None,
                    quota_impact_pct=None,
                    quota_unit=None,
                    assumptions=[
                        "Chỉ dùng thống kê lịch sử nội bộ (0 LLM)",
                        "Quota gói thuê bao chưa có denominator nên để null",
                    ],
                )
            )

        overall_conf = "cold_start" if (min_samples or 0) < 5 else ("low" if (min_samples or 0) < 20 else "medium")
        forecast_id = str(uuid.uuid4())
        payload = {
            "forecast_id": forecast_id,
            "project_id": pid,
            "forecast_version": "v2.0-empirical",
            "confidence": overall_conf,
            "sample_count": min_samples or 0,
            "tasks": [tf.model_dump(mode="json") for tf in task_forecasts],
        }
        if req.persist:
            with self.db.write_tx() as conn:
                conn.execute(
                    """
                    INSERT INTO v2_forecast_snapshots (
                        id, project_id, plan_id, plan_revision_id, forecast_version, confidence,
                        sample_count, cohort_criteria_json, forecast_json, assumptions_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        forecast_id,
                        pid,
                        str(req.plan_id) if req.plan_id else None,
                        str(req.plan_revision_id) if req.plan_revision_id else None,
                        "v2.0-empirical",
                        overall_conf,
                        min_samples or 0,
                        json.dumps({"project_id": pid}),
                        json.dumps(payload, ensure_ascii=False),
                        json.dumps(["Deterministic local cohort quantile"], ensure_ascii=False),
                        utc_now_iso(),
                    ),
                )
        return payload

    def _reconcile_plan_forecast_actuals(self, conn: Any, plan_id: str, revision_id: str, now: str) -> None:
        rev = conn.execute("SELECT forecast_id FROM v2_plan_revisions WHERE id = ?", (revision_id,)).fetchone()
        if not rev or not rev["forecast_id"]:
            return
        fid = rev["forecast_id"]
        snap = conn.execute("SELECT forecast_json FROM v2_forecast_snapshots WHERE id = ?", (fid,)).fetchone()
        if not snap:
            return
        sessions = conn.execute("SELECT id FROM v2_sessions WHERE plan_revision_id = ?", (revision_id,)).fetchall()
        in_tok = out_tok = cr_tok = cw_tok = 0
        has_tok = False
        cost_sum = 0.0
        has_cost = False
        for s in sessions:
            c = self._aggregate_session_counters(conn, s["id"])
            if c["input_tokens"] is not None:
                in_tok += c["input_tokens"]
                has_tok = True
            if c["output_tokens"] is not None:
                out_tok += c["output_tokens"]
                has_tok = True
            if c["cache_read_tokens"] is not None:
                cr_tok += c["cache_read_tokens"]
            if c["cache_write_tokens"] is not None:
                cw_tok += c["cache_write_tokens"]
            if c["cost_usd"] is not None:
                cost_sum += c["cost_usd"]
                has_cost = True
        total_tok = (in_tok + out_tok) if has_tok else None

        f_data = json.loads(snap["forecast_json"])
        p90_sum = sum((t.get("input_tokens_p90") or 0) + (t.get("output_tokens_p90") or 0) for t in f_data.get("tasks", []))
        p50_sum = sum((t.get("input_tokens_p50") or 0) + (t.get("output_tokens_p50") or 0) for t in f_data.get("tasks", []))
        within_p90 = (1 if total_tok <= p90_sum else 0) if (total_tok is not None and p90_sum > 0) else None
        abs_err = abs(total_tok - p50_sum) if (total_tok is not None and p50_sum > 0) else None

        conn.execute(
            """
            INSERT INTO v2_forecast_actuals (
                forecast_id, actual_input_tokens, actual_output_tokens, actual_cache_read_tokens,
                actual_cache_write_tokens, actual_total_tokens, actual_cost_usd, within_p90, abs_token_error, reconciled_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(forecast_id) DO UPDATE SET
                actual_input_tokens = excluded.actual_input_tokens,
                actual_output_tokens = excluded.actual_output_tokens,
                actual_total_tokens = excluded.actual_total_tokens,
                actual_cost_usd = excluded.actual_cost_usd,
                within_p90 = excluded.within_p90,
                abs_token_error = excluded.abs_token_error,
                reconciled_at = excluded.reconciled_at
            """,
            (
                fid,
                in_tok if has_tok else None,
                out_tok if has_tok else None,
                cr_tok if has_tok else None,
                cw_tok if has_tok else None,
                total_tok,
                round(cost_sum, 6) if has_cost else None,
                within_p90,
                abs_err,
                now,
            ),
        )

    def get_forecast_actual(self, forecast_id: str, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            snap = conn.execute(
                "SELECT * FROM v2_forecast_snapshots WHERE id = ? AND project_id = ?",
                (forecast_id, project_id),
            ).fetchone()
            if not snap:
                raise BridgeError(404, "forecast_not_found", "Forecast snapshot not found")
            actual = conn.execute(
                "SELECT * FROM v2_forecast_actuals WHERE forecast_id = ?",
                (forecast_id,),
            ).fetchone()
            return {
                "forecast": json.loads(snap["forecast_json"]),
                "actual": dict(actual) if actual else None,
            }

    def get_calibration(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            self._ensure_project(conn, project_id)
            rows = conn.execute(
                """
                SELECT fa.*
                FROM v2_forecast_actuals fa
                JOIN v2_forecast_snapshots fs ON fs.id = fa.forecast_id
                WHERE fs.project_id = ?
                ORDER BY fa.reconciled_at DESC
                LIMIT 100
                """,
                (project_id,),
            ).fetchall()
            if not rows:
                return {
                    "project_id": project_id,
                    "sample_count": 0,
                    "mae_tokens": None,
                    "wape": None,
                    "p90_coverage": None,
                    "drift_detected": False,
                    "status": "uncalibrated",
                }
            errs = [r["abs_token_error"] for r in rows if r["abs_token_error"] is not None]
            tots = [r["actual_total_tokens"] for r in rows if r["actual_total_tokens"] is not None and r["actual_total_tokens"] > 0]
            covs = [r["within_p90"] for r in rows if r["within_p90"] is not None]
            mae = round(sum(errs) / len(errs), 2) if errs else None
            wape = round(sum(errs) / sum(tots), 4) if (errs and tots and sum(tots) > 0) else None
            p90_cov = round(sum(covs) / len(covs), 3) if covs else None
            drift = bool(p90_cov is not None and p90_cov < 0.7)
            return {
                "project_id": project_id,
                "sample_count": len(rows),
                "mae_tokens": mae,
                "wape": wape,
                "p90_coverage": p90_cov,
                "drift_detected": drift,
                "status": "drifted" if drift else ("calibrated" if len(rows) >= 20 else "low_sample"),
            }
