"""V3.2.1 P19A — AI Chat Workspace Service (Chat / Task / Plan & DAG modes).

Supports both:
- Subscription IDE/CLI agents (`antigravity`, `claude_code`, `codex_cli`, `gemini_cli`) via verified Host Runner
  direct dispatch OR explicit Manual IDE Handoff (`awaiting_external` + real result import, never simulated).
- Hub-managed API / Local / Mock providers via existing `V321AgentExecutionEngine` & `AIProviderGateway`.
- Unified Memory / MCP / Skill resolution (0 LLM), Quota & Agent/Model Control enforcement, and Execution Map traces.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .schemas import HandoffCreateRequest
from .security import redact_payload
from .v2_schemas import PlanCreateRequest, PlanTaskSpec, PreExecutionFeatures, QuotaSnapshotCreateRequest
from .v2_service import V2BridgeService
from .v321_agent_engine import V321AgentExecutionEngine
from .v321_api_agent_schema import (
    AgentRunCreateRequest,
    AgentRunPreviewRequest,
    ChatConversationCreateRequest,
    ChatHandoffImportRequest,
    ChatMessageSendRequest,
)
from .v321_provider_gateway import AIProviderGateway, ProviderRequest
from .v321_provider_service import V321ProviderService, compute_usage_cost_usd
from .v321_subscription_service import (
    SUBSCRIPTION_SOURCE_KEYS,
    V321SubscriptionService,
    invoke_host_subscription_chat,
)

MAX_HISTORY_MESSAGES = 12
MAX_HISTORY_CHARS = 12000


class V321ChatService:
    """Durable, project-scoped AI Chat Workspace supporting Chat, Task, and Plan & DAG modes."""

    def __init__(
        self,
        db: DatabaseManager,
        v2_service: V2BridgeService,
        provider_service: V321ProviderService,
        provider_gateway: AIProviderGateway,
        agent_engine: V321AgentExecutionEngine,
        *,
        sub_service: V321SubscriptionService | None = None,
    ) -> None:
        self.db = db
        self.v2_service = v2_service
        self.provider_service = provider_service
        self.provider_gateway = provider_gateway
        self.agent_engine = agent_engine
        self.sub_service = sub_service or V321SubscriptionService(db, v2_service, provider_service)

    def list_conversations(self, project_id: str, search: str = "", include_archived: bool = False) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        if not include_archived:
            clauses.append("archived = 0")
        if search.strip():
            clauses.append("LOWER(title) LIKE ?")
            args.append(f"%{search.strip().lower()}%")
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM mcp321_chat_conversations WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 100",
                args,
            ).fetchall()
            items = []
            for r in rows:
                mcnt = conn.execute("SELECT COUNT(*) FROM mcp321_chat_messages WHERE conversation_id = ?", (r["id"],)).fetchone()[0]
                d = dict(r)
                d["archived"] = bool(d["archived"])
                d["message_count"] = mcnt
                items.append(d)
            return {"items": items, "total": len(items)}

    def create_conversation(self, req: ChatConversationCreateRequest) -> dict[str, Any]:
        self.provider_service.ensure_seeded()
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        now = utc_now_iso()
        cid = str(uuid.uuid4())
        eff_source = (req.execution_source or "").strip().lower()
        eff_prov = req.provider_profile_id
        if not eff_source:
            eff_source = eff_prov or "prov-mock-deterministic"
        if eff_source in SUBSCRIPTION_SOURCE_KEYS:
            eff_prov = None
        elif not eff_prov:
            eff_prov = eff_source

        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            if wid and not conn.execute("SELECT 1 FROM worktrees WHERE id = ? AND project_id = ?", (wid, pid)).fetchone():
                raise BridgeError(403, "worktree_scope_denied", "Worktree does not belong to project.")
            if not wid:
                wt = conn.execute("SELECT id FROM worktrees WHERE project_id = ? ORDER BY created_at DESC LIMIT 1", (pid,)).fetchone()
                wid = wt["id"] if wt else None
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (req.business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            conn.execute(
                """
                INSERT INTO mcp321_chat_conversations (
                    id, project_id, worktree_id, business_id, title, mode, provider_profile_id,
                    execution_source, model_id, external_session_id, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?, ?)
                """,
                (cid, pid, wid, req.business_id, req.title.strip(), req.mode, eff_prov, eff_source, req.model_id or "", now, now),
            )
        return self.get_conversation(cid, pid)

    def get_conversation(self, conversation_id: str, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            conv = conn.execute(
                "SELECT * FROM mcp321_chat_conversations WHERE id = ? AND project_id = ?",
                (conversation_id, project_id),
            ).fetchone()
            if not conv:
                raise BridgeError(404, "conversation_not_found", "Conversation not found in project scope.")
            msgs = conn.execute(
                "SELECT * FROM mcp321_chat_messages WHERE conversation_id = ? ORDER BY created_at ASC, rowid ASC",
                (conversation_id,),
            ).fetchall()
            out_msgs = []
            for m in msgs:
                d = dict(m)
                d["usage"] = json.loads(d.pop("usage_json") or "{}")
                d["sources"] = json.loads(d.pop("sources_json") or "{}")
                d["truncated"] = bool(d["truncated"])
                out_msgs.append(d)
            handoffs = conn.execute(
                "SELECT * FROM mcp321_chat_handoffs WHERE conversation_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 20",
                (conversation_id, project_id),
            ).fetchall()
            out_handoffs = []
            for h in handoffs:
                hd = dict(h)
                hd["evidence"] = json.loads(hd.pop("evidence_json") or "{}")
                out_handoffs.append(hd)
            res = dict(conv)
            res["archived"] = bool(res["archived"])
            res["messages"] = out_msgs
            res["handoffs"] = out_handoffs
            return res

    def archive_conversation(self, conversation_id: str, project_id: str) -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            cur = conn.execute(
                "UPDATE mcp321_chat_conversations SET archived = 1, updated_at = ? WHERE id = ? AND project_id = ?",
                (now, conversation_id, project_id),
            )
            if cur.rowcount == 0:
                raise BridgeError(404, "conversation_not_found", "Conversation not found in project scope.")
        return {"id": conversation_id, "archived": True}

    def export_conversation(self, conversation_id: str, project_id: str) -> dict[str, Any]:
        conv = self.get_conversation(conversation_id, project_id)
        return {"schema": "ai-memory-hub-v321-chat-export", "exported_at": utc_now_iso(), "conversation": redact_payload(conv)}

    def list_handoffs(self, conversation_id: str, project_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM mcp321_chat_handoffs WHERE conversation_id = ? AND project_id = ? ORDER BY created_at DESC LIMIT 30",
                (conversation_id, project_id),
            ).fetchall()
            items = []
            for r in rows:
                d = dict(r)
                d["evidence"] = json.loads(d.pop("evidence_json") or "{}")
                items.append(d)
            return {"items": items, "total": len(items)}

    def import_handoff_result(self, handoff_id: str, req: ChatHandoffImportRequest, *, actor: str = "admin") -> dict[str, Any]:
        """Import real external IDE execution result with explicit evidence & IDs (never simulated)."""
        pid = str(req.project_id)
        now = utc_now_iso()
        clean_res = str(redact_payload({"c": req.result_content})["c"]).strip()
        if not clean_res:
            raise BridgeError(422, "empty_result", "Result content must not be empty.")
        with self.db.write_tx() as conn:
            h = conn.execute(
                "SELECT * FROM mcp321_chat_handoffs WHERE id = ? AND project_id = ?",
                (handoff_id, pid),
            ).fetchone()
            if not h:
                raise BridgeError(404, "handoff_not_found", "Chat handoff package not found in project scope.")
            ev = dict(req.evidence or {})
            ev["imported_by"] = actor
            ev["imported_at"] = now
            conn.execute(
                """
                UPDATE mcp321_chat_handoffs
                SET status = 'imported', external_plan_id = ?, external_session_id = ?,
                    provider_reported_model = ?, result_content = ?, evidence_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (req.external_plan_id, req.external_session_id, req.provider_reported_model, clean_res, json.dumps(ev, ensure_ascii=False), now, handoff_id),
            )
            conv_id = h["conversation_id"]
            wid = h["worktree_id"]
            source_key = h["source_key"]
            model_id = req.provider_reported_model or h["model_id"]

        trace_id = str(uuid.uuid4())
        if wid:
            self.agent_engine._emit_trace(
                trace_id=trace_id,
                root_trace_id=trace_id,
                parent_trace_id=None,
                project_id=pid,
                worktree_id=wid,
                session_id=req.external_session_id,
                plan_id=req.external_plan_id,
                step_type="handoff",
                step_name=f"[IDE Handoff · {source_key}] Kết quả nhập thực tế",
                actor_type="agent",
                actor_id=f"manual-ide-handoff:{source_key}",
                status="succeeded",
                started_at=now,
                ended_at=now,
                provider=source_key,
                model_id=model_id,
                used_memory=True,
                used_mcp=False,
                used_llm=True,
                input_summary=h["prompt_package"][:300],
                output_summary=clean_res[:600],
                metadata={
                    "source": "manually_reported_ide_handoff",
                    "handoff_id": handoff_id,
                    "requested_model": h["model_id"],
                    "reported_model": req.provider_reported_model,
                    "external_plan_id": req.external_plan_id,
                    "external_session_id": req.external_session_id,
                    "measurement_kind": "unavailable",
                },
            )

        return self._save_assistant_message(
            asst_msg_id=str(uuid.uuid4()),
            conversation_id=conv_id,
            project_id=pid,
            content=clean_res,
            content_type="text/markdown",
            delivery_status="delivered",
            mode=h["mode"],
            provider_key=source_key,
            model_id=model_id,
            trace_id=trace_id if wid else None,
            usage={"measurement_quality": "unavailable", "estimated_cost_usd": None},
            sources={
                "delivery_channel": "manual_ide_handoff_import",
                "handoff_id": handoff_id,
                "external_plan_id": req.external_plan_id,
                "external_session_id": req.external_session_id,
                "requested_model": h["model_id"],
                "reported_model": req.provider_reported_model,
            },
        )

    def _build_bounded_history(self, conn: Any, conversation_id: str) -> list[dict[str, str]]:
        rows = conn.execute(
            "SELECT role, content FROM mcp321_chat_messages WHERE conversation_id = ? AND delivery_status = 'delivered' ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (conversation_id, MAX_HISTORY_MESSAGES),
        ).fetchall()
        msgs: list[dict[str, str]] = []
        total_chars = 0
        for r in reversed(rows):
            c = str(r["content"] or "")
            if total_chars + len(c) > MAX_HISTORY_CHARS:
                c = c[: max(200, MAX_HISTORY_CHARS - total_chars)]
            msgs.append({"role": r["role"], "content": c})
            total_chars += len(c)
            if total_chars >= MAX_HISTORY_CHARS:
                break
        return msgs

    def _create_manual_handoff(
        self,
        *,
        conversation_id: str,
        project_id: str,
        worktree_id: str | None,
        source_key: str,
        model_id: str,
        mode: str,
        objective: str,
        bundle: dict[str, Any],
        reason: str,
        asst_msg_id: str,
        asst_idem: str | None,
    ) -> dict[str, Any]:
        hid = str(uuid.uuid4())
        now = utc_now_iso()
        skills_list = [s.get("slug") for s in (bundle.get("skills") or [])]
        tools_list = [t.get("tool_name") for t in (bundle.get("selected_tools") or [])]
        mems_list = [m.get("title") for m in (bundle.get("memories") or [])]
        prompt_pkg = (
            f"# AI Memory Hub — Bàn giao sang IDE ({source_key})\n"
            f"- Handoff ID: {hid}\n"
            f"- Mode: {mode} (read-only mặc định)\n"
            f"- Model đề xuất: {model_id or '(theo IDE)'}\n"
            f"- Memories liên quan: {json.dumps(mems_list, ensure_ascii=False)}\n"
            f"- Skills liên quan: {json.dumps(skills_list, ensure_ascii=False)}\n"
            f"- MCP Tools đã duyệt: {json.dumps(tools_list, ensure_ascii=False)}\n\n"
            f"## Yêu cầu\n{objective}\n"
        )
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO mcp321_chat_handoffs (
                    id, conversation_id, project_id, worktree_id, source_key, model_id, mode,
                    prompt_package, status, evidence_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'awaiting_external', ?, ?, ?)
                """,
                (hid, conversation_id, project_id, worktree_id, source_key, model_id, mode, prompt_pkg, json.dumps({"reason": reason}, ensure_ascii=False), now, now),
            )
        msg = (
            f"**Chế độ Bàn giao sang IDE (`awaiting_external` — KHÔNG giả lập thực thi)**\n"
            f"- **Lý do**: {reason}\n"
            f"- **Handoff ID**: `{hid}`\n"
            f"- **Nguồn / Model**: `{source_key}` / `{model_id or 'auto'}`\n"
            f"- Sao chép gói prompt bên dưới sang IDE, chạy thực tế, sau đó dán kết quả vào mục **Nhập kết quả bàn giao IDE** để đồng bộ vào lịch sử & Execution Map.\n\n"
            f"```markdown\n{prompt_pkg}\n```"
        )
        return self._save_assistant_message(
            asst_msg_id=asst_msg_id,
            conversation_id=conversation_id,
            project_id=project_id,
            content=msg,
            content_type="text/markdown",
            delivery_status="pending",
            mode=mode,
            provider_key=source_key,
            model_id=model_id,
            idempotency_key=asst_idem,
            usage={"measurement_quality": "unavailable", "estimated_cost_usd": None},
            sources={
                "delivery_channel": "manual_ide_handoff",
                "handoff_id": hid,
                "status": "awaiting_external",
                "reason": reason,
                "memories": len(mems_list),
                "skills": skills_list,
                "tools": tools_list,
            },
        )

    def send_message(self, conversation_id: str, req: ChatMessageSendRequest, actor: str = "admin") -> dict[str, Any]:
        self.provider_service.ensure_seeded()
        pid = str(req.project_id)
        now = utc_now_iso()
        redacted_user_content = str(redact_payload({"c": req.content})["c"]).strip()
        if not redacted_user_content:
            raise BridgeError(422, "empty_message", "Message content must not be empty.")

        with self.db.read_conn() as conn:
            conv = conn.execute(
                "SELECT * FROM mcp321_chat_conversations WHERE id = ? AND project_id = ? AND archived = 0",
                (conversation_id, pid),
            ).fetchone()
            if not conv:
                raise BridgeError(404, "conversation_not_found", "Conversation not found in project scope.")
            if req.idempotency_key:
                prev_asst = conn.execute(
                    "SELECT * FROM mcp321_chat_messages WHERE conversation_id = ? AND idempotency_key = ?",
                    (conversation_id, f"asst:{req.idempotency_key}"),
                ).fetchone()
                if prev_asst:
                    d = dict(prev_asst)
                    d["usage"] = json.loads(d.pop("usage_json") or "{}")
                    d["sources"] = json.loads(d.pop("sources_json") or "{}")
                    d["truncated"] = bool(d["truncated"])
                    return {"idempotent_replay": True, "assistant_message": d, "conversation_id": conversation_id}
            history = self._build_bounded_history(conn, conversation_id)
            wt_row = conn.execute("SELECT canonical_root FROM worktrees WHERE id = ?", (conv["worktree_id"],)).fetchone() if conv["worktree_id"] else None
            worktree_path = wt_row["canonical_root"] if wt_row else None

        eff_mode = req.mode or conv["mode"] or "chat"
        eff_source = (
            (req.execution_source or "").strip().lower()
            or (req.provider_profile_id or "").strip()
            or (conv["execution_source"] or "").strip().lower()
            or (conv["provider_profile_id"] or "").strip()
            or "prov-mock-deterministic"
        )
        eff_model = (req.model_id or "").strip() or (conv["model_id"] or "").strip() or None
        wid = conv["worktree_id"]
        bid = conv["business_id"]
        ext_session_id = (conv["external_session_id"] or "").strip() or None

        # Backend eligibility check BEFORE persisting or dispatching (rejects disabled agent/model or exhausted quota!)
        is_subscription = eff_source in SUBSCRIPTION_SOURCE_KEYS
        elig = self.sub_service.check_dispatch_eligibility(
            source_key=eff_source,
            model_id=eff_model,
            project_id=pid,
            mode=eff_mode,
            require_direct_dispatch=(is_subscription and eff_mode != "plan_dag" and not req.manual_handoff),
        )
        resolved_model = elig["model_id"]

        # Persist immutable user message first
        user_msg_id = str(uuid.uuid4())
        asst_msg_id = str(uuid.uuid4())
        user_idem = f"user:{req.idempotency_key}" if req.idempotency_key else None
        asst_idem = f"asst:{req.idempotency_key}" if req.idempotency_key else None

        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO mcp321_chat_messages (
                    id, conversation_id, project_id, role, content, content_type, delivery_status,
                    mode, idempotency_key, created_at
                ) VALUES (?, ?, ?, 'user', ?, 'text/plain', 'delivered', ?, ?, ?)
                """,
                (user_msg_id, conversation_id, pid, redacted_user_content, eff_mode, user_idem, now),
            )
            if conv["title"] == "Cuộc trò chuyện mới":
                short_t = redacted_user_content.splitlines()[0][:80]
                conn.execute("UPDATE mcp321_chat_conversations SET title = ?, updated_at = ? WHERE id = ?", (short_t, now, conversation_id))

        # --- MODE 3: PLAN & DAG (Honors selected Execution Source & Model, never hard-codes!) ---
        if eff_mode == "plan_dag":
            if not wid:
                raise BridgeError(422, "worktree_required", "Plan & DAG mode requires a registered worktree in the project.")
            short_title = redacted_user_content.splitlines()[0][:120]
            t2_provider = elig["provider"] if is_subscription else elig["provider_profile"]["provider_key"]
            t2_auth = "subscription_cli" if is_subscription else ("api_key" if elig["group"] == "metered_api" else "unknown")
            plan_res = self.v2_service.create_plan(
                PlanCreateRequest(
                    project_id=uuid.UUID(pid),
                    worktree_id=uuid.UUID(wid),
                    objective=redacted_user_content,
                    tasks=[
                        PlanTaskSpec(
                            task_key="T1",
                            title=f"Phân tích & thiết kế: {short_title[:80]}",
                            objective=f"Phân tích yêu cầu (chỉ đọc) và lập danh sách thay đổi cho: {redacted_user_content[:1000]}",
                            dependencies=[],
                            features=PreExecutionFeatures(task_type="architecture", complexity="S"),
                            assigned_agent="planner",
                            assigned_provider="local",
                            assigned_model_id="deterministic-rules",
                            auth_mode="unknown",
                        ),
                        PlanTaskSpec(
                            task_key="T2",
                            title=f"Triển khai & kiểm thử: {short_title[:80]}",
                            objective=redacted_user_content[:2000],
                            dependencies=["T1"],
                            features=PreExecutionFeatures(task_type="implementation", complexity="M"),
                            assigned_agent="executor",
                            assigned_provider=t2_provider,
                            assigned_model_id=resolved_model,
                            auth_mode=t2_auth,
                        ),
                    ],
                )
            )
            plan_id = str(plan_res.get("plan_id") or plan_res.get("id"))
            ans = (
                f"Đã tạo kế hoạch **Plan & DAG** ở trạng thái `REVIEW_REQUIRED` (chờ bạn phê duyệt trên tab **Giao công việc**):\n"
                f"- **Plan ID**: `{plan_id}`\n"
                f"- **Nguồn thực thi / Model**: `{eff_source}` / `{resolved_model}` (`{t2_auth}`)\n"
                f"- **Số tác vụ dự kiến**: `2` (`T1` Phân tích cục bộ → `T2` Thực thi)\n"
                f"Bạn có thể mở tab **Giao công việc** để xem DAG, chỉnh sửa hoặc bấm Phê duyệt."
            )
            return self._save_assistant_message(
                asst_msg_id=asst_msg_id, conversation_id=conversation_id, project_id=pid,
                content=ans, content_type="text/markdown", delivery_status="delivered", mode="plan_dag",
                provider_key=eff_source, model_id=resolved_model,
                plan_id=plan_id, idempotency_key=asst_idem,
                sources={"mode": "plan_dag", "plan_id": plan_id, "execution_source": eff_source, "requested_model": resolved_model},
            )

        # --- SUBSCRIPTION CLI / IDE EXECUTION PATH (Antigravity, Claude Code, Codex CLI, Gemini CLI) ---
        if is_subscription:
            # Resolve Memory, Skill & Minimal MCP bundle (0 LLM) using existing engine
            dummy_prof = {
                "id": eff_source,
                "provider_key": elig["provider"],
                "selected_model": resolved_model,
                "billing_mode": "subscription_ide",
                "pricing": {},
                "max_output_tokens": 4096,
            }
            bundle = self.agent_engine._resolve_context_bundle(
                AgentRunPreviewRequest(
                    project_id=uuid.UUID(pid),
                    worktree_id=uuid.UUID(wid) if wid else None,
                    business_id=bid,
                    execution_mode="external",
                    provider_profile_id=None,
                    requested_model=resolved_model,
                    thinking_level=req.thinking_level,
                    objective=redacted_user_content,
                    max_budget_usd=0.0,
                ),
                dummy_prof,
            )
            if req.manual_handoff:
                return self._create_manual_handoff(
                    conversation_id=conversation_id, project_id=pid, worktree_id=wid,
                    source_key=eff_source, model_id=resolved_model, mode=eff_mode,
                    objective=redacted_user_content, bundle=bundle,
                    reason="Người dùng chọn Bàn giao thủ công sang IDE.",
                    asst_msg_id=asst_msg_id, asst_idem=asst_idem,
                )

            # Build scoped prompt with Memory + Skill + bounded history (never leak secrets)
            skills_list = [s.get("slug") for s in (bundle.get("skills") or [])]
            tools_list = [t.get("tool_name") for t in (bundle.get("selected_tools") or [])] if (eff_mode == "task" or req.allow_tools) else []
            mem_titles = [m.get("title") for m in (bundle.get("memories") or [])]

            hist_txt = ""
            if history and not ext_session_id:
                recent = "\n".join(f"- {m['role']}: {m['content'][:500]}" for m in history[-6:])
                hist_txt = f"\nNgữ cảnh hội thoại gần đây:\n{recent}\n"

            prompt = (
                f"[AI Memory Hub · {eff_mode.upper()} · Read-only]\n"
                f"Dự án: {pid} | Nghiệp vụ: {bid}\n"
                f"Memories liên quan: {json.dumps(mem_titles, ensure_ascii=False)}\n"
                f"Skills liên quan: {json.dumps(skills_list, ensure_ascii=False)}\n"
                f"MCP Tools khả dụng: {json.dumps(tools_list, ensure_ascii=False)}\n"
                f"{hist_txt}\n"
                f"Yêu cầu của người dùng (CHỈ ĐỌC, không sửa tệp, trả lời rõ ràng bằng tiếng Việt):\n{redacted_user_content}"
            )

            call_started = utc_now_iso()
            trace_id = str(uuid.uuid4())
            code, host_res = invoke_host_subscription_chat(
                {
                    "cli_family": elig["cli_name"],
                    "model_id": resolved_model,
                    "prompt": prompt,
                    "mode": eff_mode,
                    "worktree_path": worktree_path,
                    "external_session_id": ext_session_id,
                    "thinking_level": req.thinking_level,
                    "timeout_sec": 110,
                },
                timeout=125.0,
            )

            # If Host Runner is offline/unavailable -> honest manual handoff fallback (NEVER simulate success!)
            if code == 503 and host_res.get("error_code") in ("host_runner_offline", "host_runner_not_configured"):
                return self._create_manual_handoff(
                    conversation_id=conversation_id, project_id=pid, worktree_id=wid,
                    source_key=eff_source, model_id=resolved_model, mode=eff_mode,
                    objective=redacted_user_content, bundle=bundle,
                    reason=f"Host Runner không kết nối được ({host_res.get('message')}). Đã tạo gói bàn giao IDE thực tế.",
                    asst_msg_id=asst_msg_id, asst_idem=asst_idem,
                )

            # Auto-record quota exhaustion if provider reported quota error
            if host_res.get("error_code") == "quota_exceeded":
                try:
                    self.v2_service.record_quota(
                        QuotaSnapshotCreateRequest(
                            provider=elig["provider"],
                            auth_mode="subscription_cli",
                            account_bucket=f"model:{resolved_model}",
                            quota_kind="subscription_quota",
                            unit="availability_flag",
                            numerator=1,
                            denominator=1,
                            reset_at=host_res.get("reset_at"),
                            source="provider_error_observation",
                            confounded=False,
                            observed_at=utc_now_iso(),
                        )
                    )
                except Exception:
                    pass

            ans = str(host_res.get("final_answer") or "").strip()
            ok = bool(host_res.get("ok")) and bool(ans)
            deliv = "delivered" if ok else ("missing" if host_res.get("exit_code") == 0 else "failed")
            reported_model = host_res.get("reported_model")
            new_ext_sid = host_res.get("external_session_id") or ext_session_id
            if new_ext_sid and new_ext_sid != ext_session_id:
                with self.db.write_tx() as conn:
                    conn.execute(
                        "UPDATE mcp321_chat_conversations SET external_session_id = ?, execution_source = ?, model_id = ?, updated_at = ? WHERE id = ?",
                        (new_ext_sid, eff_source, resolved_model, utc_now_iso(), conversation_id),
                    )

            usage_raw = host_res.get("usage") or {}
            usage_out = {
                "input_tokens": usage_raw.get("input_tokens"),
                "cached_input_tokens": usage_raw.get("cache_read_tokens"),
                "output_tokens": usage_raw.get("output_tokens"),
                "reasoning_tokens": usage_raw.get("reasoning_tokens"),
                "estimated_cost_usd": None,  # Subscription quota — never billed as metered API!
                "measurement_quality": usage_raw.get("measurement_kind") or "unavailable",
                "billing_mode": "subscription_cli",
            }

            # Persist real handoff in Task mode
            handoff_id = None
            if eff_mode == "task" and ok and wid:
                try:
                    _, h_res = self.agent_engine.memory_service.save_handoff(
                        HandoffCreateRequest(
                            project_id=uuid.UUID(pid),
                            worktree_id=uuid.UUID(wid),
                            task_id=f"chat-sub-{conversation_id[:8]}-{asst_msg_id[:8]}",
                            summary=ans[:4000],
                            evidence={
                                "execution_source": eff_source,
                                "cli_family": elig["cli_name"],
                                "requested_model": resolved_model,
                                "reported_model": reported_model,
                                "external_session_id": new_ext_sid,
                                "memories": len(mem_titles),
                                "skills": skills_list,
                                "tools": tools_list,
                            },
                            source_agent=f"subscription-cli:{eff_source}",
                            idempotency_key=f"chat-sub-handoff-{asst_msg_id}",
                        )
                    )
                    handoff_id = h_res.get("handoff_id") or h_res.get("id")
                except Exception:
                    handoff_id = None

            # Emit trace to unified Execution Map
            if wid:
                self.agent_engine._emit_trace(
                    trace_id=trace_id,
                    root_trace_id=trace_id,
                    parent_trace_id=None,
                    project_id=pid,
                    worktree_id=wid,
                    session_id=None,
                    plan_id=None,
                    step_type="llm_call",
                    step_name=f"[{eff_mode.title()} · {eff_source}] {redacted_user_content[:60]}",
                    actor_type="model",
                    actor_id=f"subscription-cli:{eff_source}",
                    status="succeeded" if ok else "failed",
                    started_at=call_started,
                    ended_at=utc_now_iso(),
                    provider=elig["provider"],
                    model_id=resolved_model,
                    used_memory=bool(mem_titles),
                    used_mcp=bool(tools_list),
                    used_llm=True,
                    input_tokens=usage_raw.get("input_tokens"),
                    output_tokens=usage_raw.get("output_tokens"),
                    cache_read_tokens=usage_raw.get("cache_read_tokens"),
                    cache_write_tokens=usage_raw.get("cache_write_tokens"),
                    reasoning_tokens=usage_raw.get("reasoning_tokens"),
                    cost_usd=None,
                    input_summary=redacted_user_content[:400],
                    output_summary=(ans or host_res.get("message") or "")[:600],
                    error_code=None if ok else (host_res.get("error_code") or "subscription_cli_failed"),
                    metadata={
                        "source": f"host_runner.{elig['cli_name']}",
                        "measurement_kind": usage_out["measurement_quality"],
                        "requested_model": resolved_model,
                        "reported_model": reported_model,
                        "external_session_id": new_ext_sid,
                        "conversation_id": conversation_id,
                        "mode": eff_mode,
                        "execution_source": eff_source,
                        "billing_mode": "subscription_cli",
                        "auto_selection_reason": elig.get("auto_selection_reason"),
                    },
                )

            return self._save_assistant_message(
                asst_msg_id=asst_msg_id,
                conversation_id=conversation_id,
                project_id=pid,
                content=ans or host_res.get("message") or f"CLI `{elig['cli_name']}` không trả về kết quả hợp lệ.",
                content_type="text/markdown" if any(m in ans for m in ("```", "\n- ", "\n# ", "**")) else "text/plain",
                delivery_status=deliv,
                mode=eff_mode,
                provider_key=eff_source,
                model_id=resolved_model,
                thinking_level=req.thinking_level,
                trace_id=trace_id if wid else None,
                idempotency_key=asst_idem,
                usage=usage_out,
                sources={
                    "delivery_channel": "subscription_cli_direct",
                    "execution_source": eff_source,
                    "cli_family": elig["cli_name"],
                    "requested_model": resolved_model,
                    "reported_model": reported_model,
                    "external_session_id": new_ext_sid,
                    "auto_selection_reason": elig.get("auto_selection_reason"),
                    "memories": len(mem_titles),
                    "skills": skills_list,
                    "tools": tools_list,
                    "handoff_id": handoff_id,
                    "quota_state": elig["quota"]["state"],
                },
            )

        # --- API / LOCAL / MOCK PATHS ---
        prof = elig["provider_profile"]
        is_mock = prof["billing_mode"] == "mock"

        # --- MODE 2: TASK (Scoped single Hub-managed API Agent job) ---
        if eff_mode == "task":
            run = self.agent_engine.create_and_run(
                AgentRunCreateRequest(
                    project_id=uuid.UUID(pid),
                    worktree_id=uuid.UUID(wid) if wid else None,
                    business_id=bid,
                    execution_mode="api_agent",
                    provider_profile_id=prof["id"],
                    requested_model=resolved_model,
                    thinking_level=req.thinking_level,
                    write_mode="read_only",
                    objective=redacted_user_content,
                    max_budget_usd=req.max_budget_usd,
                    egress_consent_confirmed=req.egress_consent_confirmed,
                    execute_now=True,
                    mock_script=req.mock_script,
                ),
                actor=actor,
            )
            ans = (run.get("result_summary") or "").strip()
            deliv = "delivered" if (run["status"] == "completed" and ans) else ("missing" if run["status"] == "completed" else "failed")
            if not ans:
                ans = run.get("stop_reason") or "Nhiệm vụ không trả về nội dung câu trả lời hợp lệ."
            reqs = run.get("provider_requests") or []
            u_sum = {
                "input_tokens": sum(r["input_tokens"] for r in reqs if r.get("input_tokens") is not None) if any(r.get("input_tokens") is not None for r in reqs) else None,
                "output_tokens": sum(r["output_tokens"] for r in reqs if r.get("output_tokens") is not None) if any(r.get("output_tokens") is not None for r in reqs) else None,
                "reasoning_tokens": sum(r["reasoning_tokens"] for r in reqs if r.get("reasoning_tokens") is not None) if any(r.get("reasoning_tokens") is not None for r in reqs) else None,
                "estimated_cost_usd": 0.0 if is_mock else run.get("spent_budget_usd"),
                "is_simulation": is_mock,
            }
            ctx_prev = run.get("context_preview") or {}
            return self._save_assistant_message(
                asst_msg_id=asst_msg_id, conversation_id=conversation_id, project_id=pid,
                content=ans, content_type="text/markdown", delivery_status=deliv, mode="task",
                provider_key=ctx_prev.get("provider_key"), model_id=run.get("requested_model"),
                thinking_level=req.thinking_level, run_id=run["id"], trace_id=run.get("root_trace_id"),
                idempotency_key=asst_idem, usage=u_sum,
                sources={
                    "memories": len(ctx_prev.get("memories") or []),
                    "skills": [s.get("slug") for s in (ctx_prev.get("skills") or [])],
                    "tools": [t.get("tool_name") for t in (ctx_prev.get("selected_tools") or [])],
                    "handoff_id": run.get("handoff_id"),
                    "is_simulation": is_mock,
                },
            )

        # --- MODE 1: CHAT (Direct conversational mode — NO mandatory Plan/DAG!) ---
        egress = self.provider_service.get_project_egress_policy(pid)
        if prof["billing_mode"] == "metered_api":
            if not egress["allow_external_paid_api"] or prof["provider_key"] not in egress["allowed_provider_keys"]:
                raise BridgeError(403, "project_egress_denied", "Project egress policy denies external paid AI API calls.")
            if not req.egress_consent_confirmed:
                raise BridgeError(403, "egress_consent_required", "Explicit consent required for external paid AI API.")

        bundle = self.agent_engine._resolve_context_bundle(
            AgentRunPreviewRequest(
                project_id=uuid.UUID(pid),
                worktree_id=uuid.UUID(wid) if wid else None,
                business_id=bid,
                execution_mode="api_agent",
                provider_profile_id=prof["id"],
                requested_model=resolved_model,
                thinking_level=req.thinking_level,
                objective=redacted_user_content,
                max_budget_usd=req.max_budget_usd,
            ),
            prof,
        )
        tools_to_pass = bundle["selected_tools"] if req.allow_tools else []
        est_cost_req = self.provider_service.estimate_conservative_request_cost(prof, len(redacted_user_content) + sum(len(m["content"]) for m in history))
        if prof["billing_mode"] == "metered_api" and (est_cost_req is None or req.max_budget_usd <= 0 or est_cost_req > req.max_budget_usd):
            raise BridgeError(403, "budget_exceeded", "Chat request blocked by budget controller (unknown price or exceeds max_budget_usd).")
        if is_mock and req.max_budget_usd > 0 and est_cost_req is not None and est_cost_req > req.max_budget_usd:
            raise BridgeError(403, "budget_exceeded", f"Chat request blocked by budget controller ({est_cost_req:.6f} > {req.max_budget_usd:.6f} USD).")

        messages = [
            {
                "role": "system",
                "content": (
                    "Bạn là trợ lý AI của AI Memory Hub (chế độ Chat chỉ đọc). "
                    f"Memory liên quan: {json.dumps(bundle['memories'], ensure_ascii=False)[:1200]}. "
                    f"Skills: {[s.get('slug') for s in bundle['skills']]}."
                ),
            },
            *history,
            {"role": "user", "content": redacted_user_content},
        ]

        call_started = utc_now_iso()
        trace_id = str(uuid.uuid4())
        resp = self.provider_gateway.invoke(
            ProviderRequest(
                provider_key=prof["provider_key"],
                model=resolved_model,
                endpoint_url=prof["endpoint_url"],
                endpoint_allowlist=prof["endpoint_allowlist"],
                credential_env_ref=prof["credential_env_ref"],
                messages=messages,
                tools=tools_to_pass,
                thinking_level=req.thinking_level,
                max_output_tokens=prof["max_output_tokens"],
                mock_script=req.mock_script,
            )
        )
        est_cost = None if is_mock else compute_usage_cost_usd(resp.usage, prof.get("pricing") or {}, prof["billing_mode"])
        ans = (resp.content or "").strip()
        deliv = "delivered" if ans else "missing"
        ct = "text/markdown" if any(m in ans for m in ("```", "\n- ", "\n# ", "**")) else "text/plain"

        if wid:
            self.agent_engine._emit_trace(
                trace_id=trace_id,
                root_trace_id=trace_id,
                parent_trace_id=None,
                project_id=pid,
                worktree_id=wid,
                session_id=None,
                plan_id=None,
                step_type="llm_call",
                step_name=f"[Chat · {prof['provider_key']}] {redacted_user_content[:60]}",
                actor_type="model",
                actor_id=f"hub-chat:{prof['provider_key']}",
                status="succeeded" if deliv == "delivered" else "failed",
                started_at=call_started,
                ended_at=utc_now_iso(),
                provider=prof["provider_key"],
                model_id=resolved_model,
                used_memory=bool(bundle["memories"]),
                used_mcp=bool(tools_to_pass),
                used_llm=True,
                input_tokens=resp.usage.input_tokens,
                output_tokens=resp.usage.output_tokens,
                cache_read_tokens=resp.usage.cached_input_tokens,
                cache_write_tokens=resp.usage.cache_write_tokens,
                reasoning_tokens=resp.usage.reasoning_tokens,
                cost_usd=est_cost,
                input_summary=redacted_user_content[:400],
                output_summary=ans[:600],
                error_code=None if deliv == "delivered" else "missing_final_answer",
                metadata={
                    "measurement_kind": resp.usage.measurement_quality,
                    "requested_model": resolved_model,
                    "reported_model": resp.provider_reported_model,
                    "conversation_id": conversation_id,
                    "mode": "chat",
                    "agent_source": "api_agent",
                    "billing_mode": prof["billing_mode"],
                    "simulation": is_mock,
                },
            )

        return self._save_assistant_message(
            asst_msg_id=asst_msg_id,
            conversation_id=conversation_id,
            project_id=pid,
            content=ans or "Provider không trả về nội dung câu trả lời hợp lệ.",
            content_type=ct,
            delivery_status=deliv,
            mode="chat",
            provider_key=prof["provider_key"],
            model_id=resolved_model,
            thinking_level=req.thinking_level,
            trace_id=trace_id if wid else None,
            idempotency_key=asst_idem,
            usage={
                "input_tokens": resp.usage.input_tokens,
                "cached_input_tokens": resp.usage.cached_input_tokens,
                "output_tokens": resp.usage.output_tokens,
                "reasoning_tokens": resp.usage.reasoning_tokens,
                "total_tokens": resp.usage.total_tokens,
                "estimated_cost_usd": est_cost,
                "measurement_quality": "simulation" if is_mock else resp.usage.measurement_quality,
                "is_simulation": is_mock,
            },
            sources={
                "memories": len(bundle["memories"]),
                "skills": [s.get("slug") for s in bundle["skills"]],
                "tools": [t.get("tool_name") for t in tools_to_pass],
                "history_turns": len(history),
                "is_simulation": is_mock,
            },
        )

    def _save_assistant_message(
        self,
        *,
        asst_msg_id: str,
        conversation_id: str,
        project_id: str,
        content: str,
        content_type: str,
        delivery_status: str,
        mode: str,
        provider_key: str | None = None,
        model_id: str | None = None,
        thinking_level: str | None = None,
        run_id: str | None = None,
        plan_id: str | None = None,
        trace_id: str | None = None,
        idempotency_key: str | None = None,
        usage: dict[str, Any] | None = None,
        sources: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now = utc_now_iso()
        redacted = str(redact_payload({"c": content})["c"])
        truncated = len(redacted) > 16000
        bounded = redacted[:16000]
        with self.db.write_tx() as conn:
            conn.execute(
                """
                INSERT INTO mcp321_chat_messages (
                    id, conversation_id, project_id, role, content, content_type, delivery_status,
                    mode, provider_key, model_id, thinking_level, run_id, plan_id, trace_id,
                    idempotency_key, usage_json, sources_json, truncated, created_at
                ) VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    asst_msg_id, conversation_id, project_id, bounded, content_type, delivery_status,
                    mode, provider_key, model_id, thinking_level, run_id, plan_id, trace_id,
                    idempotency_key, json.dumps(usage or {}, ensure_ascii=False),
                    json.dumps(sources or {}, ensure_ascii=False), 1 if truncated else 0, now,
                ),
            )
            conn.execute("UPDATE mcp321_chat_conversations SET updated_at = ? WHERE id = ?", (now, conversation_id))
            row = conn.execute("SELECT * FROM mcp321_chat_messages WHERE id = ?", (asst_msg_id,)).fetchone()
        d = dict(row)
        d["usage"] = json.loads(d.pop("usage_json") or "{}")
        d["sources"] = json.loads(d.pop("sources_json") or "{}")
        d["truncated"] = bool(d["truncated"])
        return {"idempotent_replay": False, "conversation_id": conversation_id, "assistant_message": d}
