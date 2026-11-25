"""V3.2.1 P14-P19 FastAPI router for AI Provider Gateway, Project Egress Policy, API Agent Runs & Hybrid Stages."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v321_agent_engine import V321AgentExecutionEngine
from .v321_api_agent_schema import (
    AgentControlUpdateRequest,
    AgentRunCreateRequest,
    AgentRunPreviewRequest,
    ChatConversationCreateRequest,
    ChatHandoffImportRequest,
    ChatMessageSendRequest,
    HybridPlanCreateRequest,
    HybridStageUpdateRequest,
    ModelControlUpdateRequest,
    ProjectChatDefaultsRequest,
    ProjectEgressPolicyRequest,
    ProviderConnectionTestRequest,
    ProviderProfilePatchRequest,
    ProviderProfileUpsertRequest,
)
from .v321_chat_service import V321ChatService
from .v321_provider_service import V321ProviderService
from .v321_subscription_service import V321SubscriptionService


def create_v321_api_agent_router(
    prov_svc: V321ProviderService,
    engine: V321AgentExecutionEngine,
    cfg: Settings,
    *,
    chat_svc: V321ChatService | None = None,
    sub_svc: V321SubscriptionService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v321", tags=["v3.2.1 p14-p19 ai providers & api agent"])

    def principal(request: Request) -> Principal:
        return authenticate_token(extract_bearer_token(request.headers.get("Authorization")), cfg)

    def reader(p: Principal = Depends(principal)) -> Principal:
        require_scope(p, "read")
        return p

    def writer(p: Principal = Depends(principal)) -> Principal:
        require_any_scope(p, "write", "planner", "runner", "admin")
        return p

    def admin(p: Principal = Depends(principal)) -> Principal:
        require_scope(p, "admin")
        return p

    # --- P14 & P15: AI Providers ---
    @router.get("/providers")
    def list_providers(p: Principal = Depends(reader)):
        return prov_svc.list_providers()

    @router.post("/providers", status_code=201)
    def create_provider(req: ProviderProfileUpsertRequest, p: Principal = Depends(admin)):
        return prov_svc.create_provider(req)

    @router.put("/providers/{profile_id}")
    def update_provider(profile_id: str, req: ProviderProfilePatchRequest, p: Principal = Depends(admin)):
        return prov_svc.update_provider(profile_id, req)

    @router.post("/providers/{profile_id}/test")
    def test_provider(profile_id: str, req: ProviderConnectionTestRequest, p: Principal = Depends(admin)):
        return prov_svc.test_provider_connection(profile_id, req)

    # --- P15 & P18: Project Egress & Budget Policy ---
    @router.get("/projects/{project_id}/egress-policy")
    def get_egress_policy(project_id: UUID, p: Principal = Depends(reader)):
        return prov_svc.get_project_egress_policy(str(project_id))

    @router.put("/projects/{project_id}/egress-policy")
    def set_egress_policy(project_id: UUID, req: ProjectEgressPolicyRequest, p: Principal = Depends(admin)):
        return prov_svc.set_project_egress_policy(str(project_id), req)

    # --- P16, P17, P18: Hub-managed API Agent Runs ---
    @router.post("/agent-runs/preview")
    def preview_agent_run(req: AgentRunPreviewRequest, p: Principal = Depends(writer)):
        return engine.preview_run(req)

    @router.post("/agent-runs", status_code=201)
    def create_agent_run(req: AgentRunCreateRequest, p: Principal = Depends(admin)):
        return engine.create_and_run(req, actor=p.role)

    @router.get("/agent-runs")
    def list_agent_runs(project_id: UUID | None = None, limit: int = Query(30, ge=1, le=100), p: Principal = Depends(reader)):
        return engine.list_runs(str(project_id) if project_id else None, limit=limit)

    @router.get("/agent-runs/{run_id}")
    def get_agent_run(run_id: str, p: Principal = Depends(reader)):
        return engine.get_run(run_id)

    @router.post("/agent-runs/{run_id}/cancel")
    def cancel_agent_run(run_id: str, p: Principal = Depends(admin)):
        return engine.cancel_run(run_id)

    # --- P19: Hybrid Mode Stages ---
    @router.post("/hybrid-plans", status_code=201)
    def create_hybrid_plan(req: HybridPlanCreateRequest, p: Principal = Depends(admin)):
        return engine.create_hybrid_plan(req)

    @router.get("/hybrid-plans/{plan_id}")
    def list_hybrid_stages(plan_id: str, project_id: UUID, p: Principal = Depends(reader)):
        return engine.list_hybrid_stages(plan_id, str(project_id))

    @router.patch("/hybrid-stages/{stage_id}")
    def update_hybrid_stage(stage_id: str, req: HybridStageUpdateRequest, p: Principal = Depends(admin)):
        return engine.update_hybrid_stage(stage_id, req)

    # --- P19A: AI Chat Workspace (Chat / Task / Plan & DAG) ---
    if chat_svc is not None:
        @router.get("/chat/conversations")
        def list_chat_conversations(
            project_id: UUID,
            search: str = Query("", max_length=120),
            include_archived: bool = Query(False),
            p: Principal = Depends(reader),
        ):
            return chat_svc.list_conversations(str(project_id), search=search, include_archived=include_archived)

        @router.post("/chat/conversations", status_code=201)
        def create_chat_conversation(req: ChatConversationCreateRequest, p: Principal = Depends(admin)):
            return chat_svc.create_conversation(req)

        @router.get("/chat/conversations/{conversation_id}")
        def get_chat_conversation(conversation_id: str, project_id: UUID, p: Principal = Depends(reader)):
            return chat_svc.get_conversation(conversation_id, str(project_id))

        @router.post("/chat/conversations/{conversation_id}/messages", status_code=201)
        def send_chat_message(conversation_id: str, req: ChatMessageSendRequest, p: Principal = Depends(admin)):
            return chat_svc.send_message(conversation_id, req, actor=p.role)

        @router.get("/chat/conversations/{conversation_id}/export")
        def export_chat_conversation(conversation_id: str, project_id: UUID, p: Principal = Depends(reader)):
            return chat_svc.export_conversation(conversation_id, str(project_id))

        @router.delete("/chat/conversations/{conversation_id}")
        def archive_chat_conversation(conversation_id: str, project_id: UUID, p: Principal = Depends(admin)):
            return chat_svc.archive_conversation(conversation_id, str(project_id))

        @router.get("/chat/conversations/{conversation_id}/handoffs")
        def list_chat_handoffs(conversation_id: str, project_id: UUID, p: Principal = Depends(reader)):
            return chat_svc.list_handoffs(conversation_id, str(project_id))

        @router.post("/chat/handoffs/{handoff_id}/import")
        def import_chat_handoff(handoff_id: str, req: ChatHandoffImportRequest, p: Principal = Depends(admin)):
            return chat_svc.import_handoff_result(handoff_id, req, actor=p.role)

    # --- P19A.1 / P19A.2: Subscription Execution Sources, Agent/Model Controls & Project Defaults ---
    if sub_svc is not None:
        @router.get("/execution-sources")
        def list_execution_sources(
            project_id: UUID | None = None,
            probe_host: bool = Query(True),
            p: Principal = Depends(reader),
        ):
            return sub_svc.list_execution_sources(
                project_id=str(project_id) if project_id else None,
                probe_host=probe_host,
            )

        @router.put("/agent-controls/{source_key}")
        def update_agent_control(source_key: str, req: AgentControlUpdateRequest, p: Principal = Depends(admin)):
            return sub_svc.set_agent_control(source_key, req, actor=p.role)

        @router.put("/model-controls")
        def update_model_control(req: ModelControlUpdateRequest, p: Principal = Depends(admin)):
            return sub_svc.set_model_control(req, actor=p.role)

        @router.get("/projects/{project_id}/chat-defaults")
        def get_project_chat_defaults(project_id: UUID, p: Principal = Depends(reader)):
            return sub_svc.get_project_chat_defaults(str(project_id))

        @router.put("/projects/{project_id}/chat-defaults")
        def set_project_chat_defaults(project_id: UUID, req: ProjectChatDefaultsRequest, p: Principal = Depends(admin)):
            return sub_svc.set_project_chat_defaults(str(project_id), req, actor=p.role)

    return router
