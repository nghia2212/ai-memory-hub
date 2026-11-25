"""V3.2 (SK32) FastAPI router for Centralized Skill Registry, Scoped Bindings, Resolution, Runs & Candidates."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from .config import Settings
from .security import AuthPrincipal as Principal, authenticate_token, extract_bearer_token, require_any_scope, require_scope
from .v32_skill_schema import (
    SkillBindingUpsertRequest,
    SkillCandidateProposeRequest,
    SkillCandidateReviewRequest,
    SkillCreateRequest,
    SkillDraftUpdateRequest,
    SkillImportRequest,
    SkillPublishRequest,
    SkillResolveRequest,
    SkillRollbackRequest,
    SkillRunStatusUpdateRequest,
)
from .v32_skill_service import V32SkillService


def create_v32_router(skill_service: V32SkillService, cfg: Settings) -> APIRouter:
    router = APIRouter(prefix="/v32", tags=["v3.2 centralized skill management"])

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

    # --- P1 Skill Registry & Versions ---
    @router.get("/skills")
    def list_skills(
        namespace: str | None = None,
        lifecycle: str = Query("active", pattern=r"^(active|archived|all)$"),
        tag: str | None = None,
        search: str = Query("", max_length=120),
        include_starter: bool = Query(True),
        p: Principal = Depends(reader),
    ):
        return skill_service.list_skills(namespace=namespace, lifecycle=lifecycle, tag=tag, search=search, include_starter=include_starter)

    @router.post("/skills", status_code=201)
    def create_skill(req: SkillCreateRequest, p: Principal = Depends(admin)):
        return skill_service.create_skill(req, actor=p.role)

    @router.post("/skills/import")
    def import_skill(req: SkillImportRequest, p: Principal = Depends(admin)):
        return skill_service.import_skill(req, actor=p.role)

    @router.get("/skills/{skill_id}")
    def get_skill(skill_id: str, p: Principal = Depends(reader)):
        return skill_service.get_skill_detail(skill_id)

    @router.get("/skills/{skill_id}/version")
    def get_skill_version(skill_id: str, version_id: str | None = None, p: Principal = Depends(reader)):
        return skill_service.get_skill_version(skill_id, version_id=version_id)

    @router.post("/skills/{skill_id}/drafts")
    def save_draft(skill_id: str, req: SkillDraftUpdateRequest, p: Principal = Depends(admin)):
        return skill_service.create_or_update_draft(skill_id, req, actor=p.role)

    @router.post("/skills/{skill_id}/publish")
    def publish_skill(skill_id: str, req: SkillPublishRequest, p: Principal = Depends(admin)):
        return skill_service.publish_version(skill_id, req, actor=p.role)

    @router.post("/skills/{skill_id}/rollback")
    def rollback_skill(skill_id: str, req: SkillRollbackRequest, p: Principal = Depends(admin)):
        return skill_service.rollback_active_version(skill_id, req, actor=p.role)

    @router.delete("/skills/{skill_id}")
    def delete_or_archive_skill(skill_id: str, p: Principal = Depends(admin)):
        return skill_service.delete_or_archive_skill(skill_id)

    @router.get("/skills/{skill_id}/export")
    def export_skill(skill_id: str, p: Principal = Depends(reader)):
        return skill_service.export_skill(skill_id)

    # --- P2 Scoped Bindings & Deterministic Resolution ---
    @router.get("/bindings")
    def list_bindings(scope_type: str | None = None, scope_id: str | None = None, skill_id: str | None = None, p: Principal = Depends(reader)):
        return skill_service.list_bindings(scope_type=scope_type, scope_id=scope_id, skill_id=skill_id)

    @router.post("/bindings")
    def upsert_binding(req: SkillBindingUpsertRequest, p: Principal = Depends(admin)):
        return skill_service.upsert_binding(req)

    @router.delete("/bindings/{binding_id}")
    def delete_binding(binding_id: str, p: Principal = Depends(admin)):
        return skill_service.delete_binding(binding_id)

    @router.post("/resolve")
    def resolve_skills(req: SkillResolveRequest, p: Principal = Depends(writer)):
        return skill_service.resolve_skills(req)

    @router.get("/runs")
    def list_skill_runs(
        project_id: UUID,
        plan_id: UUID | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        session_id: UUID | None = None,
        limit: int = Query(50, ge=1, le=200),
        p: Principal = Depends(reader),
    ):
        return skill_service.list_skill_runs(
            project_id=str(project_id),
            plan_id=str(plan_id) if plan_id else None,
            task_id=task_id,
            attempt_id=attempt_id,
            session_id=str(session_id) if session_id else None,
            limit=limit,
        )

    @router.patch("/runs/{run_id}")
    def update_skill_run(run_id: str, req: SkillRunStatusUpdateRequest, p: Principal = Depends(writer)):
        return skill_service.update_skill_run_status(run_id, req)

    # --- P7 Experience -> Skill Candidates ---
    @router.get("/candidates")
    def list_candidates(project_id: UUID, status: str | None = None, p: Principal = Depends(reader)):
        return skill_service.list_candidates(str(project_id), status=status)

    @router.post("/candidates/propose", status_code=201)
    def propose_candidate(req: SkillCandidateProposeRequest, p: Principal = Depends(writer)):
        return skill_service.propose_candidate_from_memories(req, actor=p.role)

    @router.post("/candidates/{candidate_id}/review")
    def review_candidate(candidate_id: str, req: SkillCandidateReviewRequest, p: Principal = Depends(admin)):
        return skill_service.review_candidate(candidate_id, req, actor=p.role)

    return router
