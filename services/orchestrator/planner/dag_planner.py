"""Bounded DAG planner, revision hashing, and immutable forecast integration."""
from __future__ import annotations

import hashlib
import json
from collections import deque
from typing import Any
from uuid import UUID

from services.orchestrator.contracts import (
    AuthMode,
    BrainAdapter,
    BrainPolicyConfig,
    BrainRequest,
    BrainResponse,
    BrainRole,
    ComplexityTier,
    PlanCreateRequest,
    PlanTaskSpec,
    PreExecutionFeatures,
    TaskType,
)

VALID_TASK_TYPES: set[str] = {
    "architecture",
    "complex_reasoning",
    "implementation",
    "ui",
    "debugging",
    "testing",
    "review",
    "documentation",
    "routine",
}
VALID_COMPLEXITY: set[str] = {"XS", "S", "M", "L", "XL"}


class InvalidDAGError(ValueError):
    """Raised when a proposed task DAG has missing keys, unknown deps, or cycles."""


def validate_dag(tasks: list[PlanTaskSpec]) -> list[str]:
    """Validate task keys and dependency acyclicity; return topological order."""
    if not tasks:
        raise InvalidDAGError("Plan must contain at least one task.")

    keys = [t.task_key for t in tasks]
    if len(set(keys)) != len(keys):
        raise InvalidDAGError("Duplicate task_key found in DAG.")

    key_set = set(keys)
    indegree: dict[str, int] = {k: 0 for k in keys}
    children: dict[str, list[str]] = {k: [] for k in keys}

    for task in tasks:
        for dep in task.dependencies:
            if dep not in key_set:
                raise InvalidDAGError(f"Task '{task.task_key}' depends on unknown task '{dep}'.")
            if dep == task.task_key:
                raise InvalidDAGError(f"Task '{task.task_key}' cannot depend on itself.")
            indegree[task.task_key] += 1
            children[dep].append(task.task_key)

    queue = deque([k for k, deg in indegree.items() if deg == 0])
    order: list[str] = []
    while queue:
        cur = queue.popleft()
        order.append(cur)
        for nxt in children[cur]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)

    if len(order) != len(keys):
        raise InvalidDAGError("Cycle detected in task DAG.")
    return order


def compute_revision_hash(plan_req: PlanCreateRequest) -> str:
    """Compute deterministic SHA-256 hash binding plan tasks, models, and budgets."""
    canonical = json.dumps(plan_req.model_dump(mode="json"), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_plan_from_brain(
    *,
    project_id: UUID,
    worktree_id: UUID,
    objective: str,
    policy: BrainPolicyConfig,
    adapter: BrainAdapter,
    role: BrainRole = "tier0_rules",
    assigned_agent: str = "codex",
    assigned_provider: str = "openai",
    assigned_model_id: str = "gpt-oss-120b-medium",
    assigned_auth_mode: AuthMode = "subscription_cli",
    catalog_snapshot_id: str | None = None,
    context_pack: dict[str, Any] | None = None,
    daily_planner_spent_usd: float = 0.0,
) -> tuple[PlanCreateRequest, str, BrainResponse]:
    """Generate a validated, REVIEW_REQUIRED PlanCreateRequest without self-approving."""
    if (
        policy.daily_planner_budget_usd is not None
        and daily_planner_spent_usd >= policy.daily_planner_budget_usd
        and role != "tier0_rules"
    ):
        raise PermissionError("Daily planner budget exhausted; only tier0_rules allowed.")

    req = BrainRequest(
        project_id=project_id,
        worktree_id=worktree_id,
        role=role,
        objective=objective,
        context_pack=context_pack or {},
    )
    resp = adapter.invoke(req)
    if resp.finish_status != "ok":
        raise RuntimeError(f"Planner brain failed: {resp.finish_status} ({resp.error_code})")

    raw_tasks = resp.structured_output.get("tasks")
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raise InvalidDAGError("Brain output missing non-empty 'tasks' list.")

    specs: list[PlanTaskSpec] = []
    for idx, item in enumerate(raw_tasks[:50], start=1):
        if not isinstance(item, dict):
            raise InvalidDAGError(f"Task entry {idx} must be an object.")
        t_type = str(item.get("task_type") or "implementation")
        task_type: TaskType = t_type if t_type in VALID_TASK_TYPES else "implementation"  # type: ignore[assignment]
        comp = str(item.get("complexity") or "S")
        complexity: ComplexityTier = comp if comp in VALID_COMPLEXITY else "S"  # type: ignore[assignment]

        deps = [str(d) for d in (item.get("dependencies") or [])]
        owned = [str(p) for p in (item.get("owned_paths") or [])]
        checks = [str(c) for c in (item.get("acceptance_checks") or [])]

        features = PreExecutionFeatures(
            task_type=task_type,
            complexity=complexity,
            file_count=len(owned),
            dependency_count=len(deps),
            context_pack_tokens=item.get("context_pack_tokens"),
            context_pack_is_approx=True,
        )
        specs.append(
            PlanTaskSpec(
                task_key=str(item.get("task_key") or f"task-{idx}"),
                title=str(item.get("title") or f"Task {idx}")[:200],
                objective=str(item.get("objective") or objective)[:4000],
                dependencies=deps,
                owned_paths=owned,
                acceptance_checks=checks,
                features=features,
                assigned_agent=str(item.get("assigned_agent") or assigned_agent),
                assigned_provider=str(item.get("assigned_provider") or assigned_provider),
                assigned_model_id=str(item.get("assigned_model_id") or assigned_model_id),
                assigned_effort=item.get("assigned_effort"),
                auth_mode=assigned_auth_mode,
                budget_input_tokens=item.get("budget_input_tokens"),
                budget_output_tokens=item.get("budget_output_tokens"),
                budget_cost_usd=item.get("budget_cost_usd"),
            )
        )

    validate_dag(specs)
    plan_req = PlanCreateRequest(
        project_id=project_id,
        worktree_id=worktree_id,
        objective=objective,
        catalog_snapshot_id=catalog_snapshot_id,
        tasks=specs,
        planner_session_id=resp.upstream_session_id,
    )
    rev_hash = compute_revision_hash(plan_req)
    return plan_req, rev_hash, resp
