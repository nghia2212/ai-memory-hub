"""Evaluation matrix separating observed local outcomes, synthetic benchmarks, and hypotheses."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from services.orchestrator.contracts import AuthMode, ComplexityTier, TaskType

EvidenceKind = Literal["observed", "synthetic_benchmark", "hypothesis"]


@dataclass(frozen=True)
class EvaluationEntry:
    model_id: str
    provider: str
    auth_mode: AuthMode
    task_type: TaskType
    complexity: ComplexityTier
    evidence_kind: EvidenceKind
    effort: str | None = None
    project_id: UUID | None = None
    correctness_score: float | None = None
    acceptance_rate: float | None = None
    avg_latency_ms: float | None = None
    avg_effective_cost_usd: float | None = None
    failure_rate: float | None = None
    sample_count: int = 0
    provenance: dict[str, Any] = field(default_factory=dict)


class EvaluationMatrix:
    """Stores and queries evaluation entries without mixing observed facts with hypotheses."""

    def __init__(self) -> None:
        self._entries: list[EvaluationEntry] = []

    def add(self, entry: EvaluationEntry) -> None:
        self._entries.append(entry)

    def query(
        self,
        *,
        task_type: TaskType | None = None,
        complexity: ComplexityTier | None = None,
        model_id: str | None = None,
        auth_mode: AuthMode | None = None,
        project_id: UUID | None = None,
    ) -> list[EvaluationEntry]:
        results: list[EvaluationEntry] = []
        for e in self._entries:
            if task_type and e.task_type != task_type:
                continue
            if complexity and e.complexity != complexity:
                continue
            if model_id and e.model_id != model_id:
                continue
            if auth_mode and e.auth_mode != auth_mode:
                continue
            if project_id is not None and e.project_id is not None and e.project_id != project_id:
                continue
            results.append(e)
        # Prefer project-specific observed > global observed > synthetic_benchmark > hypothesis
        rank_map = {"observed": 0, "synthetic_benchmark": 1, "hypothesis": 2}
        results.sort(
            key=lambda x: (
                0 if (project_id is not None and x.project_id == project_id) else 1,
                rank_map[x.evidence_kind],
                -x.sample_count,
            )
        )
        return results

    def best_for_model(
        self,
        *,
        provider: str,
        model_id: str,
        auth_mode: AuthMode,
        task_type: TaskType,
        complexity: ComplexityTier,
        project_id: UUID | None = None,
    ) -> EvaluationEntry | None:
        matches = self.query(
            task_type=task_type,
            complexity=complexity,
            model_id=model_id,
            auth_mode=auth_mode,
            project_id=project_id,
        )
        for m in matches:
            if m.provider == provider:
                return m
        return None
