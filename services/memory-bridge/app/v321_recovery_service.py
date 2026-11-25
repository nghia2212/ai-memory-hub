"""V3.2.1 P16B — Persistent Task Checkpoint Ledger, Startup Reconciliation & Safe Recovery Engine.

Reuses existing V2 Plan/Task/Attempt/Lease/Session, MCP321 Agent Runs/Provider Requests/Chat,
Subscription Controls/Quota, Audit Log & Execution Map — ZERO competing orchestrator!
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .quota_guard import quota_status
from .security import compute_payload_hash, redact_payload
from .v2_service import V2BridgeService
from .v321_provider_service import V321ProviderService
from .v321_recovery_schema import (
    RecoveryActionRequest,
    RecoveryChangeAgentRequest,
    RecoveryPolicyUpdateRequest,
    RecoveryResumeRequest,
    TaskCheckpointCreateRequest,
)
from .v321_subscription_service import (
    CLI_TO_SOURCE,
    SUBSCRIPTION_SOURCE_KEYS,
    V321SubscriptionService,
)

TERMINAL_TASK_STATES = frozenset({"completed", "cancelled", "rejected"})
TERMINAL_RUN_STATES = frozenset({"cancelled", "stopped_budget", "blocked"})
TERMINAL_RECOVERY_STATES = frozenset({"canceled", "resolved"})


def probe_host_runner_liveness(
    *,
    runner_id: str | None = None,
    runner_pid: int | None = None,
    timeout: float = 2.0,
) -> dict[str, Any]:
    """Read-only check whether an old runner process is still alive via Host Brain Agent.

    Never kills unrelated processes; returns 'unknown' when Host Runner is unreachable.
    """
    if not runner_pid:
        return {"status": "unknown", "reason": "Không có PID của runner cũ để xác minh."}
    url = (os.environ.get("HOST_BRAIN_URL") or "").strip().rstrip("/")
    token = (os.environ.get("BRIDGE_RUNNER_TOKEN") or "").strip()
    if not url or not token:
        return {"status": "unreachable", "reason": "Host Runner chưa cấu hình hoặc đang ngoại tuyến."}
    body = json.dumps({"runner_id": runner_id, "pid": runner_pid}).encode("utf-8")
    req = urllib.request.Request(
        f"{url}/host/runner-status",
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            data = json.loads(raw) if raw else {}
            st = str(data.get("status") or "unknown")
            if st not in ("alive", "stopped", "unknown", "unreachable"):
                st = "unknown"
            return {"status": st, "reason": str(data.get("reason") or ""), "matched_process": data.get("matched_process")}
    except Exception as exc:
        return {"status": "unreachable", "reason": f"Không liên hệ được Host Runner: {exc}"}


class V321RecoveryService:
    """P16B Durable Checkpoints, Startup Reconciliation, Lease Fencing & Recovery Queue."""

    def __init__(
        self,
        db: DatabaseManager,
        v2_service: V2BridgeService,
        provider_service: V321ProviderService,
        sub_service: V321SubscriptionService,
    ) -> None:
        self.db = db
        self.v2_service = v2_service
        self.provider_service = provider_service
        self.sub_service = sub_service

    def _audit(
        self,
        conn: Any,
        *,
        actor: str,
        action: str,
        target_type: str,
        target_id: str,
        details: dict[str, Any],
        project_id: str | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO v2_audit_log (id, project_id, actor, action, target_type, target_id, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), project_id, actor, action, target_type, target_id, json.dumps(details, ensure_ascii=False), utc_now_iso()),
        )

    def _emit_recovery_trace(
        self,
        conn: Any,
        *,
        project_id: str,
        worktree_id: str | None,
        plan_id: str | None,
        task_id: str | None,
        session_id: str | None,
        step_name: str,
        status: str,
        output_summary: str,
        metadata: dict[str, Any],
    ) -> str | None:
        if not worktree_id:
            return None
        tid = str(uuid.uuid4())
        now = utc_now_iso()
        phash = compute_payload_hash({"tid": tid, "step": step_name, "status": status, "meta": metadata})
        conn.execute(
            """
            INSERT INTO v22_execution_traces (
                trace_id, root_trace_id, parent_trace_id, project_id, worktree_id,
                session_id, plan_id, task_id, step_type, step_name, actor_type, actor_id,
                status, started_at, ended_at, duration_ms, used_mcp, used_memory, used_llm,
                output_summary, metadata_json, payload_hash
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'local_rule', ?, 'system', 'p16b-recovery', ?, ?, ?, 0, 0, 0, 0, ?, ?, ?)
            """,
            (
                tid, tid, project_id, worktree_id, session_id, plan_id, task_id,
                step_name[:200], status, now, now, output_summary[:600],
                json.dumps(redact_payload(metadata), ensure_ascii=False), phash,
            ),
        )
        return tid

    # --- Recovery Policy (Manual / Automatic / Hybrid default) ---
    def get_recovery_policy(self, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            row = conn.execute("SELECT * FROM mcp321_recovery_policies WHERE project_id = ?", (project_id,)).fetchone()
            if not row:
                return {
                    "project_id": project_id,
                    "recovery_mode": "hybrid",
                    "auto_resume_readonly": True,
                    "auto_reconnect_alive_runner": True,
                    "require_review_on_dirty_git": True,
                    "require_review_on_uncertain_api": True,
                    "updated_by": "default",
                    "updated_at": None,
                }
            return {
                "project_id": row["project_id"],
                "recovery_mode": row["recovery_mode"],
                "auto_resume_readonly": bool(row["auto_resume_readonly"]),
                "auto_reconnect_alive_runner": bool(row["auto_reconnect_alive_runner"]),
                "require_review_on_dirty_git": bool(row["require_review_on_dirty_git"]),
                "require_review_on_uncertain_api": bool(row["require_review_on_uncertain_api"]),
                "updated_by": row["updated_by"],
                "updated_at": row["updated_at"],
            }

    def update_recovery_policy(
        self, project_id: str, req: RecoveryPolicyUpdateRequest, *, actor: str = "admin"
    ) -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            conn.execute(
                """
                INSERT INTO mcp321_recovery_policies (
                    project_id, recovery_mode, auto_resume_readonly, auto_reconnect_alive_runner,
                    require_review_on_dirty_git, require_review_on_uncertain_api, updated_by, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id) DO UPDATE SET
                    recovery_mode = excluded.recovery_mode,
                    auto_resume_readonly = excluded.auto_resume_readonly,
                    auto_reconnect_alive_runner = excluded.auto_reconnect_alive_runner,
                    require_review_on_dirty_git = excluded.require_review_on_dirty_git,
                    require_review_on_uncertain_api = excluded.require_review_on_uncertain_api,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (
                    project_id,
                    req.recovery_mode,
                    1 if req.auto_resume_readonly else 0,
                    1 if req.auto_reconnect_alive_runner else 0,
                    1 if req.require_review_on_dirty_git else 0,
                    1 if req.require_review_on_uncertain_api else 0,
                    actor,
                    now,
                ),
            )
            self._audit(
                conn,
                actor=actor,
                action="recovery_policy_updated",
                target_type="project",
                target_id=project_id,
                details=req.model_dump(),
                project_id=project_id,
            )
        return self.get_recovery_policy(project_id)

    # --- Immutable Versioned Checkpoints with Fencing ---
    def record_checkpoint(self, req: TaskCheckpointCreateRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            if req.idempotency_key:
                prev = conn.execute(
                    "SELECT * FROM mcp321_task_checkpoints WHERE entity_kind = ? AND entity_id = ? AND idempotency_key = ?",
                    (req.entity_kind, req.entity_id, req.idempotency_key),
                ).fetchone()
                if prev:
                    return self._format_checkpoint(prev, deduplicated=True)

            latest = conn.execute(
                "SELECT checkpoint_version, fencing_token FROM mcp321_task_checkpoints WHERE entity_kind = ? AND entity_id = ? ORDER BY checkpoint_version DESC LIMIT 1",
                (req.entity_kind, req.entity_id),
            ).fetchone()
            # Fencing check: reject stale runner trying to write an older fencing token!
            cur_fence = int(latest["fencing_token"]) if latest else 1
            if req.fencing_token is not None and req.fencing_token < cur_fence:
                raise BridgeError(
                    409,
                    "stale_fencing_token",
                    f"Checkpoint bị từ chối vì fencing_token cũ ({req.fencing_token} < {cur_fence}). Chặn ghi đè trùng lặp.",
                )
            next_ver = (int(latest["checkpoint_version"]) + 1) if latest else 1
            eff_fence = max(cur_fence, req.fencing_token or cur_fence)
            cid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO mcp321_task_checkpoints (
                    id, entity_kind, entity_id, project_id, worktree_id, plan_id, revision_id,
                    task_id, attempt_id, session_id, runner_id, runner_pid, checkpoint_version,
                    idempotency_key, execution_state, delivery_state, recovery_state,
                    completed_steps_json, current_step_json, pending_steps_json,
                    git_state_json, test_results_json, context_refs_json, budget_json,
                    interruption_reason, fencing_token, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    cid, req.entity_kind, req.entity_id, pid, wid, req.plan_id, req.revision_id,
                    req.task_id, req.attempt_id, req.session_id, req.runner_id, req.runner_pid, next_ver,
                    req.idempotency_key, req.execution_state, req.delivery_state, req.recovery_state,
                    json.dumps(redact_payload(req.completed_steps), ensure_ascii=False),
                    json.dumps(redact_payload(req.current_step), ensure_ascii=False),
                    json.dumps(redact_payload(req.pending_steps), ensure_ascii=False),
                    json.dumps(redact_payload(req.git_state), ensure_ascii=False),
                    json.dumps(redact_payload(req.test_results), ensure_ascii=False),
                    json.dumps(redact_payload(req.context_refs), ensure_ascii=False),
                    json.dumps(redact_payload(req.budget), ensure_ascii=False),
                    req.interruption_reason, eff_fence, now,
                ),
            )
            row = conn.execute("SELECT * FROM mcp321_task_checkpoints WHERE id = ?", (cid,)).fetchone()
        return self._format_checkpoint(row, deduplicated=False)

    def _format_checkpoint(self, row: Any, *, deduplicated: bool = False) -> dict[str, Any]:
        d = dict(row)
        for col, key in (
            ("completed_steps_json", "completed_steps"),
            ("current_step_json", "current_step"),
            ("pending_steps_json", "pending_steps"),
            ("git_state_json", "git_state"),
            ("test_results_json", "test_results"),
            ("context_refs_json", "context_refs"),
            ("budget_json", "budget"),
        ):
            d[key] = json.loads(d.pop(col) or ("[]" if key.endswith("steps") else "{}"))
        d["deduplicated"] = deduplicated
        return d

    def list_checkpoints(self, entity_kind: str, entity_id: str, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp321_task_checkpoints WHERE entity_kind = ? AND entity_id = ? AND project_id = ? ORDER BY checkpoint_version DESC LIMIT 50",
                (entity_kind, entity_id, project_id),
            ).fetchall()
            return {"items": [self._format_checkpoint(r) for r in rows], "total": len(rows)}

    # --- Startup Reconciliation (Idempotent, Safe Under Repeated Restarts) ---
    def reconcile_startup(
        self,
        *,
        project_id: str | None = None,
        check_host_runner: bool = True,
        apply_auto_policy: bool = True,
    ) -> dict[str, Any]:
        """Scan unfinished tasks, interrupted runs & undelivered results without duplicating any Plan/Task/Attempt."""
        now = utc_now_iso()
        scanned = 0
        upserted = 0
        auto_resumed = 0
        reconnected = 0
        pending_review = 0

        with self.db.write_tx() as conn:
            quotas = self.v2_service._latest_quotas(conn)
            proj_filter = " AND t.plan_id IN (SELECT id FROM v2_plans WHERE project_id = ?)" if project_id else ""
            proj_args = (project_id,) if project_id else ()

            # 1. Scan unfinished V2 Plan Tasks (never touch terminal completed/cancelled/rejected tasks!)
            v2_tasks = conn.execute(
                f"""
                SELECT t.*, p.project_id, p.worktree_id, p.objective AS plan_objective, p.status AS plan_status
                FROM v2_tasks t
                JOIN v2_plans p ON p.id = t.plan_id
                WHERE t.state IN ('leased', 'running', 'blocked')
                  AND p.status NOT IN ('COMPLETED', 'CANCELLED', 'REJECTED')
                  {proj_filter}
                ORDER BY t.updated_at DESC LIMIT 100
                """,
                proj_args,
            ).fetchall()

            for t in v2_tasks:
                scanned += 1
                tid = t["id"]
                pid = t["project_id"]
                wid = t["worktree_id"]
                existing = conn.execute(
                    "SELECT * FROM mcp321_recovery_queue WHERE entity_kind = 'v2_task' AND entity_id = ?",
                    (tid,),
                ).fetchone()
                if existing and existing["recovery_status"] in TERMINAL_RECOVERY_STATES:
                    continue

                att = conn.execute(
                    "SELECT * FROM v2_task_attempts WHERE task_id = ? ORDER BY attempt_number DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                lease = conn.execute(
                    "SELECT * FROM v2_job_leases WHERE task_id = ? ORDER BY claimed_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                sess = conn.execute(
                    "SELECT * FROM v2_sessions WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
                    (tid,),
                ).fetchone()
                ckpt = conn.execute(
                    "SELECT * FROM mcp321_task_checkpoints WHERE entity_kind = 'v2_task' AND entity_id = ? ORDER BY checkpoint_version DESC LIMIT 1",
                    (tid,),
                ).fetchone()

                runner_id = (lease["runner_id"] if lease else None) or (ckpt["runner_id"] if ckpt else None)
                runner_pid = ckpt["runner_pid"] if ckpt else None
                lease_still_valid = bool(
                    lease
                    and lease["released_at"] is None
                    and conn.execute("SELECT julianday(?) > julianday(?)", (lease["expires_at"], now)).fetchone()[0]
                )

                # Probe old runner liveness safely
                if runner_pid and check_host_runner:
                    liveness = probe_host_runner_liveness(runner_id=runner_id, runner_pid=runner_pid)
                    old_status = liveness["status"]
                elif lease_still_valid:
                    old_status = "alive"
                elif att and att["status"] in ("orphaned", "failed"):
                    old_status = "stopped"
                else:
                    old_status = "unknown"

                git_state = json.loads(ckpt["git_state_json"]) if ckpt else {}
                if not git_state and wid:
                    wt = conn.execute("SELECT branch, head_commit, dirty_fingerprint FROM worktrees WHERE id = ?", (wid,)).fetchone()
                    if wt:
                        git_state = {"branch": wt["branch"], "head_commit": wt["head_commit"], "dirty": bool(wt["dirty_fingerprint"])}
                completed_steps = json.loads(ckpt["completed_steps_json"]) if ckpt else []
                pending_steps = json.loads(ckpt["pending_steps_json"]) if ckpt else [{"step": t["title"], "task_key": t["task_key"]}]
                budget = json.loads(ckpt["budget_json"]) if ckpt else {}

                prov = str(t["assigned_provider"] or "agy")
                mid = str(t["assigned_model_id"] or "")
                skey = CLI_TO_SOURCE.get(prov, prov)
                q = quota_status(quotas, prov, mid, str(t["auth_mode"] or "subscription_cli"))

                err_code = str((att["error_code"] if att else "") or (ckpt["interruption_reason"] if ckpt else "") or "")
                if q["state"] == "exhausted" or "quota" in err_code.lower():
                    safety = "quota_exhausted"
                    reason = "Agent/Model hết quota giữa nhiệm vụ (không tự động chuyển sang API trả phí)."
                    rec_action = "change_agent"
                elif git_state.get("dirty") and (ckpt and ckpt["execution_state"] in ("running", "interrupted")):
                    safety = "dirty_worktree_edit"
                    reason = "Gián đoạn khi worktree có thay đổi mã nguồn chưa commit (được bảo toàn, cần đối soát trước khi ghi tiếp)."
                    rec_action = "review"
                elif "network" in err_code.lower() or "timeout" in err_code.lower():
                    safety = "network_interrupted"
                    reason = f"Gián đoạn kết nối mạng ({err_code})."
                    rec_action = "review"
                elif old_status == "alive":
                    safety = "safe_readonly"
                    reason = "Agent cũ vẫn đang chạy khi Hub khởi động lại; cần kết nối lại thay vì tạo tác vụ trùng."
                    rec_action = "reconnect"
                elif old_status == "stopped" and not git_state.get("dirty"):
                    safety = "safe_readonly"
                    reason = f"Agent cũ đã dừng ({err_code or 'hub_restart'}); có thể phục hồi an toàn từ checkpoint."
                    rec_action = "resume"
                else:
                    safety = "needs_reconciliation"
                    reason = f"Trạng thái lease/agent cũ chưa xác minh ({err_code or 'stale_lease'}); giữ ở hàng đợi đối soát."
                    rec_action = "review"

                pol = self._get_policy_in_tx(conn, pid)
                rec_status = self._decide_initial_status(pol, old_status, safety, apply_auto_policy)
                if rec_status == "auto_resumable":
                    auto_resumed += 1
                elif rec_status == "reconnected":
                    reconnected += 1
                else:
                    pending_review += 1

                self._upsert_recovery_item(
                    conn,
                    entity_kind="v2_task",
                    entity_id=tid,
                    project_id=pid,
                    worktree_id=wid,
                    plan_id=t["plan_id"],
                    task_id=tid,
                    attempt_id=att["id"] if att else None,
                    session_id=sess["id"] if sess else None,
                    runner_id=runner_id,
                    runner_pid=runner_pid,
                    agent_source=skey,
                    model_id=mid,
                    recovery_status=rec_status,
                    old_agent_status=old_status,
                    interruption_reason=reason,
                    safety_classification=safety,
                    latest_checkpoint_id=ckpt["id"] if ckpt else None,
                    completed_steps=completed_steps,
                    pending_steps=pending_steps,
                    git_state=git_state,
                    budget=budget,
                    delivery_state=ckpt["delivery_state"] if ckpt else "pending",
                    fencing_token=int(ckpt["fencing_token"]) if ckpt else 1,
                    recommended_action=rec_action,
                    now=now,
                )
                upserted += 1

            # 2. Scan interrupted MCP321 API Agent Runs & undelivered completed results
            run_filter = " AND project_id = ?" if project_id else ""
            runs = conn.execute(
                f"""
                SELECT * FROM mcp321_agent_runs
                WHERE status IN ('running', 'completed') {run_filter}
                ORDER BY updated_at DESC LIMIT 100
                """,
                proj_args,
            ).fetchall()

            for r in runs:
                rid = r["id"]
                pid = r["project_id"]
                wid = r["worktree_id"]
                existing = conn.execute(
                    "SELECT * FROM mcp321_recovery_queue WHERE entity_kind = 'agent_run' AND entity_id = ?",
                    (rid,),
                ).fetchone()
                if existing and existing["recovery_status"] in TERMINAL_RECOVERY_STATES:
                    continue

                # Check if completed run already has a delivered chat message (if yes -> truly done, skip!)
                if r["status"] == "completed":
                    msg_row = conn.execute(
                        "SELECT delivery_status FROM mcp321_chat_messages WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
                        (rid,),
                    ).fetchone()
                    if not msg_row or msg_row["delivery_status"] == "delivered":
                        continue
                    # Completed AI answer exists in DB, only result delivery was interrupted!
                    scanned += 1
                    pol = self._get_policy_in_tx(conn, pid)
                    rec_status = "auto_resumable" if (apply_auto_policy and pol["recovery_mode"] in ("hybrid", "automatic")) else "pending_review"
                    if rec_status == "auto_resumable":
                        auto_resumed += 1
                    else:
                        pending_review += 1
                    self._upsert_recovery_item(
                        conn,
                        entity_kind="agent_run",
                        entity_id=rid,
                        project_id=pid,
                        worktree_id=wid,
                        plan_id=r["plan_id"],
                        task_id=r["task_id"],
                        attempt_id=r["attempt_id"],
                        session_id=r["session_id"],
                        runner_id="hub-api-agent",
                        runner_pid=None,
                        agent_source=r["provider_profile_id"] or "api_agent",
                        model_id=r["requested_model"] or "",
                        recovery_status=rec_status,
                        old_agent_status="stopped",
                        interruption_reason="AI đã hoàn thành và lưu kết quả nhưng chưa hiển thị/giao về Chat Workspace. Chỉ cần giao lại kết quả (0 LLM, 0 phí).",
                        safety_classification="result_undelivered",
                        latest_checkpoint_id=None,
                        completed_steps=[{"step": "AI inference completed", "verified": True}],
                        pending_steps=[{"step": "Deliver cached result to Chat Workspace"}],
                        git_state={},
                        budget={
                            "max_budget_usd": r["max_budget_usd"],
                            "spent_usd": r["spent_budget_usd"],
                            "reserved_usd": r["reserved_budget_usd"],
                            "uncertain_usd": r["uncertain_budget_usd"],
                            "remaining_usd": max(0.0, round(float(r["max_budget_usd"] - r["spent_budget_usd"] - r["reserved_budget_usd"] - r["uncertain_budget_usd"]), 6)),
                        },
                        delivery_state="pending",
                        fencing_token=1,
                        recommended_action="deliver_result",
                        now=now,
                    )
                    upserted += 1
                    continue

                # Running API Agent interrupted by Hub restart!
                scanned += 1
                uncertain_reqs = conn.execute(
                    "SELECT id, provider_key, requested_model, status, reserved_cost_usd FROM mcp321_provider_requests WHERE run_id = ? AND status IN ('reserved', 'uncertain')",
                    (rid,),
                ).fetchall()
                ckpt = conn.execute(
                    "SELECT * FROM mcp321_task_checkpoints WHERE entity_kind = 'agent_run' AND entity_id = ? ORDER BY checkpoint_version DESC LIMIT 1",
                    (rid,),
                ).fetchone()
                git_state = json.loads(ckpt["git_state_json"]) if ckpt else {}
                completed_steps = json.loads(ckpt["completed_steps_json"]) if ckpt else [{"step": f"Đã hoàn thành {r['rounds_used']} vòng"}]
                pending_steps = json.loads(ckpt["pending_steps_json"]) if ckpt else [{"step": f"Tiếp tục vòng {r['rounds_used'] + 1}/{r['max_rounds']}"}]

                has_uncertain_api = bool(uncertain_reqs) or float(r["uncertain_budget_usd"] or 0.0) > 0.0
                if has_uncertain_api:
                    safety = "uncertain_api_inflight"
                    reason = f"Hub dừng trong lúc đang gọi API ({len(uncertain_reqs)} request chưa rõ kết quả). Giữ ngân sách dự phòng, KHÔNG tự động gọi lại API."
                    rec_action = "review"
                elif r["write_mode"] == "worktree_patch" or git_state.get("dirty"):
                    safety = "dirty_worktree_edit"
                    reason = "Gián đoạn trong lúc sửa mã nguồn; cần đối soát diff/worktree trước khi tiếp tục."
                    rec_action = "review"
                else:
                    safety = "safe_readonly"
                    reason = "API Agent chỉ đọc bị gián đoạn khi Hub khởi động lại; có thể phục hồi từ checkpoint."
                    rec_action = "resume"

                pol = self._get_policy_in_tx(conn, pid)
                rec_status = self._decide_initial_status(pol, "stopped", safety, apply_auto_policy)
                if rec_status == "auto_resumable":
                    auto_resumed += 1
                else:
                    pending_review += 1

                self._upsert_recovery_item(
                    conn,
                    entity_kind="agent_run",
                    entity_id=rid,
                    project_id=pid,
                    worktree_id=wid,
                    plan_id=r["plan_id"],
                    task_id=r["task_id"],
                    attempt_id=r["attempt_id"],
                    session_id=r["session_id"],
                    runner_id="hub-api-agent",
                    runner_pid=None,
                    agent_source=r["provider_profile_id"] or "api_agent",
                    model_id=r["requested_model"] or "",
                    recovery_status=rec_status,
                    old_agent_status="stopped",
                    interruption_reason=reason,
                    safety_classification=safety,
                    latest_checkpoint_id=ckpt["id"] if ckpt else None,
                    completed_steps=completed_steps,
                    pending_steps=pending_steps,
                    git_state=git_state,
                    budget={
                        "max_budget_usd": r["max_budget_usd"],
                        "spent_usd": r["spent_budget_usd"],
                        "reserved_usd": r["reserved_budget_usd"],
                        "uncertain_usd": r["uncertain_budget_usd"],
                        "remaining_usd": max(0.0, round(float(r["max_budget_usd"] - r["spent_budget_usd"] - r["reserved_budget_usd"] - r["uncertain_budget_usd"]), 6)),
                    },
                    delivery_state="pending",
                    fencing_token=int(ckpt["fencing_token"]) if ckpt else 1,
                    recommended_action=rec_action,
                    now=now,
                )
                upserted += 1

        return {
            "scanned": scanned,
            "upserted": upserted,
            "auto_resumable": auto_resumed,
            "reconnected": reconnected,
            "pending_review": pending_review,
            "reconciled_at": now,
        }

    def _get_policy_in_tx(self, conn: Any, project_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM mcp321_recovery_policies WHERE project_id = ?", (project_id,)).fetchone()
        if not row:
            return {
                "recovery_mode": "hybrid",
                "auto_resume_readonly": True,
                "auto_reconnect_alive_runner": True,
                "require_review_on_dirty_git": True,
                "require_review_on_uncertain_api": True,
            }
        return {
            "recovery_mode": row["recovery_mode"],
            "auto_resume_readonly": bool(row["auto_resume_readonly"]),
            "auto_reconnect_alive_runner": bool(row["auto_reconnect_alive_runner"]),
            "require_review_on_dirty_git": bool(row["require_review_on_dirty_git"]),
            "require_review_on_uncertain_api": bool(row["require_review_on_uncertain_api"]),
        }

    def _decide_initial_status(self, pol: dict[str, Any], old_status: str, safety: str, apply_auto: bool) -> str:
        if not apply_auto or pol["recovery_mode"] == "manual":
            return "pending_review"
        # NEVER auto-resume uncertain API calls, dirty edits, or quota exhausted!
        if safety in ("uncertain_api_inflight", "dirty_worktree_edit", "quota_exhausted", "needs_reconciliation", "network_interrupted"):
            return "pending_review"
        if old_status == "alive" and pol["auto_reconnect_alive_runner"]:
            return "reconnected"
        if safety in ("safe_readonly", "result_undelivered") and old_status == "stopped" and pol["auto_resume_readonly"]:
            return "auto_resumable"
        return "pending_review"

    def _upsert_recovery_item(
        self,
        conn: Any,
        *,
        entity_kind: str,
        entity_id: str,
        project_id: str,
        worktree_id: str | None,
        plan_id: str | None,
        task_id: str | None,
        attempt_id: str | None,
        session_id: str | None,
        runner_id: str | None,
        runner_pid: int | None,
        agent_source: str,
        model_id: str,
        recovery_status: str,
        old_agent_status: str,
        interruption_reason: str,
        safety_classification: str,
        latest_checkpoint_id: str | None,
        completed_steps: list[dict[str, Any]],
        pending_steps: list[dict[str, Any]],
        git_state: dict[str, Any],
        budget: dict[str, Any],
        delivery_state: str,
        fencing_token: int,
        recommended_action: str,
        now: str,
    ) -> None:
        rid = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO mcp321_recovery_queue (
                id, entity_kind, entity_id, project_id, worktree_id, plan_id, task_id, attempt_id,
                session_id, runner_id, runner_pid, agent_source, model_id, recovery_status,
                old_agent_status, interruption_reason, safety_classification, latest_checkpoint_id,
                completed_steps_json, pending_steps_json, git_state_json, budget_json,
                delivery_state, fencing_token, recommended_action, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_kind, entity_id) DO UPDATE SET
                attempt_id = excluded.attempt_id,
                session_id = excluded.session_id,
                runner_id = excluded.runner_id,
                runner_pid = excluded.runner_pid,
                agent_source = excluded.agent_source,
                model_id = excluded.model_id,
                recovery_status = CASE
                    WHEN mcp321_recovery_queue.recovery_status IN ('paused', 'canceled', 'resolved')
                    THEN mcp321_recovery_queue.recovery_status
                    ELSE excluded.recovery_status
                END,
                old_agent_status = excluded.old_agent_status,
                interruption_reason = excluded.interruption_reason,
                safety_classification = excluded.safety_classification,
                latest_checkpoint_id = COALESCE(excluded.latest_checkpoint_id, mcp321_recovery_queue.latest_checkpoint_id),
                completed_steps_json = excluded.completed_steps_json,
                pending_steps_json = excluded.pending_steps_json,
                git_state_json = excluded.git_state_json,
                budget_json = excluded.budget_json,
                delivery_state = excluded.delivery_state,
                fencing_token = MAX(mcp321_recovery_queue.fencing_token, excluded.fencing_token),
                recommended_action = excluded.recommended_action,
                updated_at = excluded.updated_at
            """,
            (
                rid, entity_kind, entity_id, project_id, worktree_id, plan_id, task_id, attempt_id,
                session_id, runner_id, runner_pid, agent_source, model_id, recovery_status,
                old_agent_status, interruption_reason, safety_classification, latest_checkpoint_id,
                json.dumps(completed_steps, ensure_ascii=False),
                json.dumps(pending_steps, ensure_ascii=False),
                json.dumps(git_state, ensure_ascii=False),
                json.dumps(budget, ensure_ascii=False),
                delivery_state, fencing_token, recommended_action, now, now,
            ),
        )

    def _format_queue_row(self, row: Any, proj_name: str = "") -> dict[str, Any]:
        d = dict(row)
        d["project_name"] = proj_name
        d["completed_steps"] = json.loads(d.pop("completed_steps_json") or "[]")
        d["pending_steps"] = json.loads(d.pop("pending_steps_json") or "[]")
        d["git_state"] = json.loads(d.pop("git_state_json") or "{}")
        d["budget"] = json.loads(d.pop("budget_json") or "{}")
        # Compute truthful action capabilities for UI
        st = d["recovery_status"]
        old = d["old_agent_status"]
        safety = d["safety_classification"]
        is_term = st in ("canceled", "resolved")
        d["actions_allowed"] = {
            "resume": (not is_term) and (old != "alive") and (safety != "quota_exhausted"),
            "reconnect": (not is_term) and (old == "alive"),
            "pause": (not is_term) and (st != "paused"),
            "cancel": not is_term,
            "change_agent": not is_term,
            "review": True,
        }
        return d

    def list_recovery_queue(self, project_id: str | None = None, include_resolved: bool = False) -> dict[str, Any]:
        clauses: list[str] = []
        args: list[Any] = []
        if project_id:
            clauses.append("q.project_id = ?")
            args.append(project_id)
        if not include_resolved:
            clauses.append("q.recovery_status NOT IN ('canceled', 'resolved')")
        where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT q.*, p.name AS proj_name
                FROM mcp321_recovery_queue q
                LEFT JOIN projects p ON p.id = q.project_id
                {where_sql}
                ORDER BY q.updated_at DESC LIMIT 100
                """,
                args,
            ).fetchall()
            items = [self._format_queue_row(r, proj_name=r["proj_name"] or "") for r in rows]
        policy = self.get_recovery_policy(project_id) if project_id else None
        return {"items": items, "total": len(items), "policy": policy}

    def _get_item_or_404(self, conn: Any, recovery_id: str, project_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM mcp321_recovery_queue WHERE id = ? AND project_id = ?",
            (recovery_id, project_id),
        ).fetchone()
        if not row:
            raise BridgeError(404, "recovery_item_not_found", "Không tìm thấy mục phục hồi trong phạm vi dự án.")
        return row

    # --- Action 1: RESUME (New Attempt on SAME Task, Fencing Token++, Skip Verified Completed Steps) ---
    def resume_item(self, recovery_id: str, req: RecoveryResumeRequest, *, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.read_conn() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            if row["recovery_status"] in TERMINAL_RECOVERY_STATES:
                raise BridgeError(409, "terminal_recovery_state", f"Nhiệm vụ đã ở trạng thái kết thúc ({row['recovery_status']}), không thể Resume.")
            if row["old_agent_status"] == "alive":
                raise BridgeError(
                    409,
                    "old_agent_still_alive",
                    "Agent cũ vẫn đang hoạt động. Hãy dùng 'Reconnect Agent' thay vì tạo Attempt mới để tránh chạy trùng.",
                )
            if row["safety_classification"] == "uncertain_api_inflight" and not req.confirm_uncertain_retry:
                raise BridgeError(
                    409,
                    "uncertain_api_outcome",
                    "Request API trước đó chưa rõ kết quả. Cần xác nhận đối soát (confirm_uncertain_retry=true) trước khi thử lại để tránh tính phí trùng.",
                )
            if row["safety_classification"] == "dirty_worktree_edit" and not req.confirm_dirty_worktree:
                raise BridgeError(
                    409,
                    "dirty_worktree_review_required",
                    "Worktree có mã nguồn chưa commit. Cần xác nhận đã kiểm tra diff (confirm_dirty_worktree=true) trước khi tiếp tục.",
                )
            agent_source = row["agent_source"]
            model_id = row["model_id"]
            safety = row["safety_classification"]

        # Special 0-LLM path: AI already completed, only deliver cached result!
        if safety == "result_undelivered":
            return self.review_item(recovery_id, RecoveryActionRequest(project_id=req.project_id, note=req.note), deliver_cached_result=True, actor=actor)

        # Revalidate Agent/Model/Quota eligibility BEFORE resuming (no silent fallback to paid API!)
        if agent_source and agent_source != "api_agent":
            self.sub_service.check_dispatch_eligibility(
                source_key=agent_source,
                model_id=model_id or None,
                project_id=pid,
                mode="task",
                require_direct_dispatch=False,
            )

        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            new_fence = int(row["fencing_token"]) + 1
            new_attempt_id = None

            if row["entity_kind"] == "v2_task":
                # Quarantine any stale leases on this task atomically (fencing!)
                conn.execute(
                    "UPDATE v2_job_leases SET released_at = ? WHERE task_id = ? AND released_at IS NULL",
                    (now, row["entity_id"]),
                )
                # Transition task back to 'queued' on SAME task & plan (never duplicate plan/task!)
                conn.execute(
                    "UPDATE v2_tasks SET state = 'queued', updated_at = ? WHERE id = ? AND state NOT IN ('succeeded', 'cancelled')",
                    (now, row["entity_id"]),
                )
                if row["plan_id"]:
                    conn.execute(
                        "UPDATE v2_plans SET status = 'APPROVED', updated_at = ? WHERE id = ? AND status IN ('PAUSED', 'FAILED')",
                        (now, row["plan_id"]),
                    )
                # Record immutable resume checkpoint with bumped fencing token
                max_ver = conn.execute(
                    "SELECT COALESCE(MAX(checkpoint_version), 0) FROM mcp321_task_checkpoints WHERE entity_kind = 'v2_task' AND entity_id = ?",
                    (row["entity_id"],),
                ).fetchone()[0]
                ckpt_id = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO mcp321_task_checkpoints (
                        id, entity_kind, entity_id, project_id, worktree_id, plan_id, task_id,
                        checkpoint_version, execution_state, delivery_state, recovery_state,
                        completed_steps_json, pending_steps_json, git_state_json, budget_json,
                        interruption_reason, fencing_token, created_at
                    ) VALUES (?, 'v2_task', ?, ?, ?, ?, ?, ?, 'queued', 'pending', 'resumed', ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ckpt_id, row["entity_id"], pid, row["worktree_id"], row["plan_id"], row["task_id"],
                        int(max_ver) + 1, row["completed_steps_json"], row["pending_steps_json"],
                        row["git_state_json"], row["budget_json"], f"Resumed by {actor}: {req.note}".strip(), new_fence, now,
                    ),
                )
            elif row["entity_kind"] == "agent_run":
                # Move interrupted run back to pending_approval/ready with preserved budget & completed steps
                conn.execute(
                    "UPDATE mcp321_agent_runs SET status = 'pending_approval', stop_reason = ?, updated_at = ? WHERE id = ? AND status = 'running'",
                    (f"recovered_from_checkpoint:fence={new_fence}", now, row["entity_id"]),
                )

            conn.execute(
                """
                UPDATE mcp321_recovery_queue
                SET recovery_status = 'resumed', fencing_token = ?, resolution_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (new_fence, f"Đã phục hồi an toàn từ checkpoint (fencing_token={new_fence}, bỏ qua bước đã hoàn thành). {req.note}".strip(), now, recovery_id),
            )
            self._audit(
                conn,
                actor=actor,
                action="recovery_resumed",
                target_type=row["entity_kind"],
                target_id=row["entity_id"],
                details={"recovery_id": recovery_id, "fencing_token": new_fence, "note": req.note},
                project_id=pid,
            )
            trace_id = self._emit_recovery_trace(
                conn,
                project_id=pid,
                worktree_id=row["worktree_id"],
                plan_id=row["plan_id"],
                task_id=row["task_id"],
                session_id=row["session_id"],
                step_name=f"[P16B Recovery] Resume từ checkpoint (fence={new_fence})",
                status="succeeded",
                output_summary=f"Đã phục hồi cùng Task {row['entity_id']} với fencing_token={new_fence}; giữ nguyên bước đã hoàn thành.",
                metadata={"recovery_id": recovery_id, "fencing_token": new_fence, "action": "resume"},
            )
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {"ok": True, "item": self._format_queue_row(updated), "fencing_token": new_fence, "trace_id": trace_id}

    # --- Action 2: RECONNECT AGENT (When Old Runner Still Alive) ---
    def reconnect_item(self, recovery_id: str, req: RecoveryActionRequest, *, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            if row["old_agent_status"] != "alive":
                raise BridgeError(
                    409,
                    "runner_not_alive",
                    f"Agent cũ không ở trạng thái đang chạy ({row['old_agent_status']}). Hãy dùng 'Resume' để tạo Attempt mới từ checkpoint.",
                )
            conn.execute(
                "UPDATE mcp321_recovery_queue SET recovery_status = 'reconnected', resolution_note = ?, updated_at = ? WHERE id = ?",
                (f"Đã kết nối lại và tiếp tục giám sát runner {row['runner_id'] or 'external'} (không tạo trùng Attempt). {req.note}".strip(), now, recovery_id),
            )
            self._audit(
                conn,
                actor=actor,
                action="recovery_reconnected",
                target_type=row["entity_kind"],
                target_id=row["entity_id"],
                details={"recovery_id": recovery_id, "runner_id": row["runner_id"], "runner_pid": row["runner_pid"]},
                project_id=pid,
            )
            trace_id = self._emit_recovery_trace(
                conn,
                project_id=pid,
                worktree_id=row["worktree_id"],
                plan_id=row["plan_id"],
                task_id=row["task_id"],
                session_id=row["session_id"],
                step_name="[P16B Recovery] Reconnect Agent cũ đang chạy",
                status="succeeded",
                output_summary=f"Đã kết nối lại runner {row['runner_id'] or 'external'} mà không tạo tác vụ trùng.",
                metadata={"recovery_id": recovery_id, "action": "reconnect"},
            )
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {"ok": True, "item": self._format_queue_row(updated), "trace_id": trace_id}

    # --- Action 3: PAUSE ---
    def pause_item(self, recovery_id: str, req: RecoveryActionRequest, *, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            if row["entity_kind"] == "v2_task" and row["plan_id"]:
                conn.execute("UPDATE v2_plans SET status = 'PAUSED', updated_at = ? WHERE id = ? AND status IN ('APPROVED', 'RUNNING')", (now, row["plan_id"]))
            conn.execute(
                "UPDATE mcp321_recovery_queue SET recovery_status = 'paused', resolution_note = ?, updated_at = ? WHERE id = ?",
                (f"Đã tạm dừng an toàn (bảo toàn checkpoint & mã nguồn). {req.note}".strip(), now, recovery_id),
            )
            self._audit(conn, actor=actor, action="recovery_paused", target_type=row["entity_kind"], target_id=row["entity_id"], details={"recovery_id": recovery_id}, project_id=pid)
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {"ok": True, "item": self._format_queue_row(updated)}

    # --- Action 4: CANCEL (Terminal — Never Auto-Resumed) ---
    def cancel_item(self, recovery_id: str, req: RecoveryActionRequest, *, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            if row["entity_kind"] == "v2_task":
                conn.execute("UPDATE v2_job_leases SET released_at = ? WHERE task_id = ? AND released_at IS NULL", (now, row["entity_id"]))
                conn.execute("UPDATE v2_tasks SET state = 'cancelled', updated_at = ? WHERE id = ? AND state != 'succeeded'", (now, row["entity_id"]))
            elif row["entity_kind"] == "agent_run":
                conn.execute("UPDATE mcp321_agent_runs SET status = 'cancelled', stop_reason = 'cancelled_in_recovery', ended_at = ?, updated_at = ? WHERE id = ?", (now, now, row["entity_id"]))
            conn.execute(
                "UPDATE mcp321_recovery_queue SET recovery_status = 'canceled', resolution_note = ?, updated_at = ? WHERE id = ?",
                (f"Đã hủy vĩnh viễn theo yêu cầu quản trị (không tự phục hồi lại). {req.note}".strip(), now, recovery_id),
            )
            self._audit(conn, actor=actor, action="recovery_canceled", target_type=row["entity_kind"], target_id=row["entity_id"], details={"recovery_id": recovery_id}, project_id=pid)
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {"ok": True, "item": self._format_queue_row(updated)}

    # --- Action 5: CHANGE AGENT (Explicit Policy/Quota/Egress Validation — No Silent Paid Fallback) ---
    def change_agent(self, recovery_id: str, req: RecoveryChangeAgentRequest, *, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        skey = req.execution_source.strip().lower()
        elig = self.sub_service.check_dispatch_eligibility(
            source_key=skey,
            model_id=req.model_id,
            project_id=pid,
            mode="task",
            require_direct_dispatch=False,
        )
        # Never allow switching to paid API without project egress policy + explicit consent!
        if elig["group"] == "metered_api":
            eg = self.provider_service.get_project_egress_policy(pid)
            if not eg["allow_external_paid_api"] or elig["provider"] not in eg["allowed_provider_keys"]:
                raise BridgeError(403, "project_egress_denied", "Chính sách Egress dự án chặn chuyển sang API trả phí.")
            if not req.allow_paid_api_consent:
                raise BridgeError(403, "egress_consent_required", "Cần xác nhận đồng ý chi phí API trả phí (allow_paid_api_consent=true).")

        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            if row["entity_kind"] == "v2_task":
                new_auth = "subscription_cli" if skey in SUBSCRIPTION_SOURCE_KEYS else ("api_key" if elig["group"] == "metered_api" else "unknown")
                conn.execute(
                    "UPDATE v2_tasks SET assigned_provider = ?, assigned_model_id = ?, auth_mode = ?, updated_at = ? WHERE id = ?",
                    (elig["provider"], elig["model_id"], new_auth, now, row["entity_id"]),
                )
            elif row["entity_kind"] == "agent_run" and elig.get("provider_profile"):
                conn.execute(
                    "UPDATE mcp321_agent_runs SET provider_profile_id = ?, requested_model = ?, updated_at = ? WHERE id = ?",
                    (elig["provider_profile"]["id"], elig["model_id"], now, row["entity_id"]),
                )
            new_safety = "safe_readonly" if row["safety_classification"] == "quota_exhausted" else row["safety_classification"]
            new_action = "resume" if new_safety == "safe_readonly" else row["recommended_action"]
            conn.execute(
                """
                UPDATE mcp321_recovery_queue
                SET agent_source = ?, model_id = ?, safety_classification = ?, recommended_action = ?,
                    resolution_note = ?, updated_at = ?
                WHERE id = ?
                """,
                (skey, elig["model_id"], new_safety, new_action, f"Đã đổi sang {skey} / {elig['model_id']}. {req.note}".strip(), now, recovery_id),
            )
            self._audit(
                conn,
                actor=actor,
                action="recovery_agent_changed",
                target_type=row["entity_kind"],
                target_id=row["entity_id"],
                details={"recovery_id": recovery_id, "new_source": skey, "new_model": elig["model_id"]},
                project_id=pid,
            )
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {"ok": True, "item": self._format_queue_row(updated)}

    # --- Action 6: REVIEW RECOVERY (Full Lineage + Optional Zero-LLM Result Redelivery) ---
    def review_item(
        self,
        recovery_id: str,
        req: RecoveryActionRequest,
        *,
        deliver_cached_result: bool = False,
        actor: str = "admin",
    ) -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        delivered_now = False
        with self.db.write_tx() as conn:
            row = self._get_item_or_404(conn, recovery_id, pid)
            ckpts = [
                self._format_checkpoint(c)
                for c in conn.execute(
                    "SELECT * FROM mcp321_task_checkpoints WHERE entity_kind = ? AND entity_id = ? ORDER BY checkpoint_version DESC LIMIT 20",
                    (row["entity_kind"], row["entity_id"]),
                ).fetchall()
            ]
            attempts = []
            if row["entity_kind"] == "v2_task":
                attempts = [
                    dict(a)
                    for a in conn.execute(
                        "SELECT id, attempt_number, status, error_code, started_at, ended_at FROM v2_task_attempts WHERE task_id = ? ORDER BY attempt_number ASC",
                        (row["entity_id"],),
                    ).fetchall()
                ]
            uncertain_reqs = []
            if row["entity_kind"] == "agent_run":
                uncertain_reqs = [
                    dict(u)
                    for u in conn.execute(
                        "SELECT id, provider_key, requested_model, status, reserved_cost_usd, estimated_cost_usd, created_at FROM mcp321_provider_requests WHERE run_id = ? ORDER BY created_at ASC",
                        (row["entity_id"],),
                    ).fetchall()
                ]
                if deliver_cached_result and row["safety_classification"] == "result_undelivered":
                    run_row = conn.execute("SELECT result_summary FROM mcp321_agent_runs WHERE id = ?", (row["entity_id"],)).fetchone()
                    if run_row and (run_row["result_summary"] or "").strip():
                        conn.execute(
                            "UPDATE mcp321_chat_messages SET content = ?, delivery_status = 'delivered' WHERE run_id = ? AND delivery_status != 'delivered'",
                            (run_row["result_summary"], row["entity_id"]),
                        )
                        conn.execute(
                            "UPDATE mcp321_recovery_queue SET recovery_status = 'resolved', delivery_state = 'delivered', resolution_note = ?, updated_at = ? WHERE id = ?",
                            ("Đã giao lại kết quả AI đã lưu vào Chat Workspace (0 LLM, 0 chi phí).", now, recovery_id),
                        )
                        delivered_now = True
                        self._audit(conn, actor=actor, action="recovery_result_redelivered", target_type="agent_run", target_id=row["entity_id"], details={"recovery_id": recovery_id}, project_id=pid)
            updated = conn.execute("SELECT * FROM mcp321_recovery_queue WHERE id = ?", (recovery_id,)).fetchone()
        return {
            "ok": True,
            "delivered_cached_result": delivered_now,
            "item": self._format_queue_row(updated),
            "checkpoints": ckpts,
            "attempts": attempts,
            "provider_requests": uncertain_reqs,
        }
