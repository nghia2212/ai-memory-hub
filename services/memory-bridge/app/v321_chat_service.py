"""V3.2.1 P19A — AI Chat Workspace Service (Chat / Task / Plan & DAG modes).

Reuses existing:
- V321AgentExecutionEngine (Task mode + Memory/MCP/Skill context resolution)
- V321ProviderService & AIProviderGateway (Chat mode + Egress & Atomic Budget Controller)
- V2BridgeService (Plan & DAG mode -> creates real v2_plans in REVIEW_REQUIRED)
- v22_execution_traces (unified Execution Map; zero parallel orchestrator or telemetry stack)
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import compute_payload_hash, redact_payload
from .v2_schemas import PlanCreateRequest, PlanTaskSpec, PreExecutionFeatures
from .v2_service import V2BridgeService
from .v321_agent_engine import V321AgentExecutionEngine
from .v321_api_agent_schema import (
    AgentRunCreateRequest,
    AgentRunPreviewRequest,
    ChatConversationCreateRequest,
    ChatMessageSendRequest,
)
from .v321_provider_gateway import AIProviderGateway, ProviderRequest
from .v321_provider_service import V321ProviderService, compute_usage_cost_usd

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
    ) -> None:
        self.db = db
        self.v2_service = v2_service
        self.provider_service = provider_service
        self.provider_gateway = provider_gateway
        self.agent_engine = agent_engine

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
                    id, project_id, worktree_id, business_id, title, mode, provider_profile_id, model_id, archived, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (cid, pid, wid, req.business_id, req.title.strip(), req.mode, req.provider_profile_id or "prov-mock-deterministic", req.model_id or "", now, now),
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
            res = dict(conv)
            res["archived"] = bool(res["archived"])
            res["messages"] = out_msgs
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
            # Idempotency check (never duplicate messages or double-charge AI!)
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

        eff_mode = req.mode or conv["mode"] or "chat"
        eff_prov_id = req.provider_profile_id or conv["provider_profile_id"] or "prov-mock-deterministic"
        wid = conv["worktree_id"]
        bid = conv["business_id"]

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
            # Auto-title new conversation from first user message
            if conv["title"] == "Cuộc trò chuyện mới":
                short_t = redacted_user_content.splitlines()[0][:80]
                conn.execute("UPDATE mcp321_chat_conversations SET title = ?, updated_at = ? WHERE id = ?", (short_t, now, conversation_id))

        # --- MODE 3: PLAN & DAG ---
        if eff_mode == "plan_dag":
            if not wid:
                raise BridgeError(422, "worktree_required", "Plan & DAG mode requires a registered worktree in the project.")
            short_title = redacted_user_content.splitlines()[0][:120]
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
                            assigned_provider="agy",
                            assigned_model_id="gemini-4-neon-medium",
                            auth_mode="subscription_cli",
                        ),
                    ],
                )
            )
            plan_id = str(plan_res.get("plan_id") or plan_res.get("id"))
            ans = (
                f"Đã tạo kế hoạch **Plan & DAG** ở trạng thái `REVIEW_REQUIRED` (chờ bạn phê duyệt trên tab **Giao công việc**):\n"
                f"- **Plan ID**: `{plan_id}`\n"
                f"- **Số tác vụ dự kiến**: `2` (`T1` Phân tích → `T2` Triển khai & kiểm thử)\n"
                f"Bạn có thể mở tab **Giao công việc** để xem DAG, chỉnh sửa hoặc bấm Phê duyệt."
            )
            return self._save_assistant_message(
                asst_msg_id=asst_msg_id, conversation_id=conversation_id, project_id=pid,
                content=ans, content_type="text/markdown", delivery_status="delivered", mode="plan_dag",
                plan_id=plan_id, idempotency_key=asst_idem, sources={"mode": "plan_dag", "plan_id": plan_id},
            )

        # --- MODE 2: TASK (Scoped single Hub-managed API Agent job) ---
        if eff_mode == "task":
            run = self.agent_engine.create_and_run(
                AgentRunCreateRequest(
                    project_id=uuid.UUID(pid),
                    worktree_id=uuid.UUID(wid) if wid else None,
                    business_id=bid,
                    execution_mode="api_agent",
                    provider_profile_id=eff_prov_id,
                    requested_model=req.model_id,
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
                "estimated_cost_usd": run.get("spent_budget_usd"),
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
                },
            )

        # --- MODE 1: CHAT (Direct conversational mode — NO mandatory Plan/DAG!) ---
        prof = self.provider_service.get_provider(eff_prov_id)
        if not prof["enabled"]:
            raise BridgeError(403, "provider_disabled", f"Provider '{prof['display_name']}' is disabled.")
        egress = self.provider_service.get_project_egress_policy(pid)
        if prof["billing_mode"] == "metered_api":
            if not egress["allow_external_paid_api"] or prof["provider_key"] not in egress["allowed_provider_keys"]:
                raise BridgeError(403, "project_egress_denied", "Project egress policy denies external paid AI API calls.")
            if not req.egress_consent_confirmed:
                raise BridgeError(403, "egress_consent_required", "Explicit consent required for external paid AI API.")

        # Minimal deterministic context (0 LLM); tools disabled in Chat by default unless allow_tools=True
        bundle = self.agent_engine._resolve_context_bundle(
            AgentRunPreviewRequest(
                project_id=uuid.UUID(pid),
                worktree_id=uuid.UUID(wid) if wid else None,
                business_id=bid,
                execution_mode="api_agent",
                provider_profile_id=prof["id"],
                requested_model=req.model_id,
                thinking_level=req.thinking_level,
                objective=redacted_user_content,
                max_budget_usd=req.max_budget_usd,
            ),
            prof,
        )
        tools_to_pass = bundle["selected_tools"] if req.allow_tools else []
        req_model = req.model_id or prof["selected_model"]
        est_cost_req = self.provider_service.estimate_conservative_request_cost(prof, len(redacted_user_content) + sum(len(m["content"]) for m in history))
        if prof["billing_mode"] == "metered_api" and (est_cost_req is None or req.max_budget_usd <= 0 or est_cost_req > req.max_budget_usd):
            raise BridgeError(403, "budget_exceeded", "Chat request blocked by budget controller (unknown price or exceeds max_budget_usd).")
        if prof["billing_mode"] == "mock" and req.max_budget_usd > 0 and est_cost_req is not None and est_cost_req > req.max_budget_usd:
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
                model=req_model,
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
        est_cost = compute_usage_cost_usd(resp.usage, prof.get("pricing") or {}, prof["billing_mode"])
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
                model_id=req_model,
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
                    "requested_model": req_model,
                    "reported_model": resp.provider_reported_model,
                    "conversation_id": conversation_id,
                    "mode": "chat",
                    "agent_source": "api_agent",
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
            model_id=req_model,
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
                "measurement_quality": resp.usage.measurement_quality,
            },
            sources={
                "memories": len(bundle["memories"]),
                "skills": [s.get("slug") for s in bundle["skills"]],
                "tools": [t.get("tool_name") for t in tools_to_pass],
                "history_turns": len(history),
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
