"""Deterministic, explainable model router with hard filters and cost-aware ranking."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from services.orchestrator.catalog.registry import CatalogSnapshot, CatalogStaleError
from services.orchestrator.contracts import (
    AuthMode,
    ComplexityTier,
    ModelRecord,
    QuotaSnapshot,
    RoutingRecommendation,
    TaskType,
)

from .evaluator import EvaluationEntry, EvaluationMatrix


class NoEligibleModelError(RuntimeError):
    """Raised when no verified model satisfies hard routing filters."""


@dataclass(frozen=True)
class RoutingWeights:
    quality: float = 0.55
    cost: float = 0.25
    latency: float = 0.10
    failure_penalty: float = 0.10


@dataclass(frozen=True)
class RoutingConstraints:
    project_id: UUID | None = None
    allowed_auth_modes: tuple[AuthMode, ...] = ("subscription_cli",)
    allow_paid_api_fallback: bool = False
    max_cost_usd: float | None = None
    min_context_limit: int | None = None
    required_capabilities: dict[str, Any] = field(default_factory=dict)
    allow_cold_start: bool = True
    allow_stale_catalog: bool = False
    min_quality_threshold: float = 0.65
    weights: RoutingWeights = field(default_factory=RoutingWeights)


def _is_quota_exhausted(
    provider: str, auth_mode: AuthMode, quotas: list[QuotaSnapshot]
) -> tuple[bool, str]:
    matching = [q for q in quotas if q.provider == provider and q.auth_mode == auth_mode]
    if not matching:
        return False, "Không có dữ liệu"
    for q in matching:
        if q.numerator is not None and q.denominator is not None and q.numerator >= q.denominator:
            return True, f"Quota exhausted ({q.quota_kind}: {q.numerator}/{q.denominator} {q.unit})"
        if q.numerator is None or q.denominator is None:
            return False, "Không có dữ liệu"
    q0 = matching[0]
    return False, f"{q0.numerator}/{q0.denominator} {q0.unit}"


def _score_candidate(
    model: ModelRecord,
    ev: EvaluationEntry | None,
    constraints: RoutingConstraints,
) -> tuple[float, float, str, bool]:
    """
    Return (composite_score, est_cost, evidence_kind, cold_start).
    Higher composite_score is better; prefers cheapest adequate model.
    """
    w = constraints.weights
    if ev is not None and ev.evidence_kind in ("observed", "synthetic_benchmark"):
        quality = ev.correctness_score if ev.correctness_score is not None else (ev.acceptance_rate or 0.7)
        est_cost = ev.avg_effective_cost_usd if ev.avg_effective_cost_usd is not None else 0.0
        latency = ev.avg_latency_ms if ev.avg_latency_ms is not None else 2000.0
        failure = ev.failure_rate if ev.failure_rate is not None else 0.0
        evidence_kind = ev.evidence_kind
        cold_start = False
    else:
        quality = 0.70  # neutral untested prior
        est_cost = (
            ((model.price_input_per_1m or 0.0) * 0.004 + (model.price_output_per_1m or 0.0) * 0.001)
            if model.auth_mode == "api_key"
            else 0.0
        )
        latency = 2000.0
        failure = 0.05
        evidence_kind = "hypothesis"
        cold_start = True

    cost_norm = min(1.0, est_cost / max(constraints.max_cost_usd or 1.0, 0.01))
    lat_norm = min(1.0, latency / 10000.0)
    score = (
        w.quality * quality
        - w.cost * cost_norm
        - w.latency * lat_norm
        - w.failure_penalty * failure
    )
    return score, est_cost, evidence_kind, cold_start


class DeterministicRouter:
    """Selects the cheapest adequate verified model using deterministic rules (0 LLM)."""

    def recommend(
        self,
        *,
        task_type: TaskType,
        complexity: ComplexityTier,
        snapshot: CatalogSnapshot,
        evaluations: EvaluationMatrix,
        constraints: RoutingConstraints,
        quotas: list[QuotaSnapshot] | None = None,
        now: datetime | None = None,
    ) -> RoutingRecommendation:
        if snapshot.is_stale and not constraints.allow_stale_catalog:
            raise CatalogStaleError("Cannot route on stale catalog snapshot without explicit override")

        quotas_list = quotas or []
        effective_auth_modes = set(constraints.allowed_auth_modes)
        if not constraints.allow_paid_api_fallback:
            effective_auth_modes.discard("api_key")

        eligible: list[tuple[float, float, ModelRecord, EvaluationEntry | None, str, bool, str]] = []
        rejected_reasons: list[str] = []

        for m in snapshot.models:
            if m.status != "verified":
                rejected_reasons.append(f"{m.model_id}: status={m.status}")
                continue
            if m.auth_mode not in effective_auth_modes:
                rejected_reasons.append(f"{m.model_id}: auth_mode={m.auth_mode} disallowed")
                continue
            if constraints.min_context_limit and m.context_limit is not None and m.context_limit < constraints.min_context_limit:
                rejected_reasons.append(f"{m.model_id}: context_limit={m.context_limit} < {constraints.min_context_limit}")
                continue
            cap_ok = all(m.capabilities.get(k) == v for k, v in constraints.required_capabilities.items())
            if not cap_ok:
                rejected_reasons.append(f"{m.model_id}: missing required capability")
                continue
            exhausted, quota_note = _is_quota_exhausted(m.provider, m.auth_mode, quotas_list)
            if exhausted:
                rejected_reasons.append(f"{m.model_id}: {quota_note}")
                continue

            ev = evaluations.best_for_model(
                provider=m.provider,
                model_id=m.model_id,
                auth_mode=m.auth_mode,
                task_type=task_type,
                complexity=complexity,
                project_id=constraints.project_id,
            )
            if ev is None and not constraints.allow_cold_start:
                rejected_reasons.append(f"{m.model_id}: cold_start disallowed")
                continue
            if ev is not None and ev.correctness_score is not None and ev.correctness_score < constraints.min_quality_threshold:
                rejected_reasons.append(f"{m.model_id}: correctness={ev.correctness_score} < threshold")
                continue

            score, est_cost, evidence_kind, cold_start = _score_candidate(m, ev, constraints)
            if constraints.max_cost_usd is not None and est_cost > constraints.max_cost_usd:
                rejected_reasons.append(f"{m.model_id}: est_cost={est_cost} > max_cost_usd")
                continue

            eligible.append((score, est_cost, m, ev, evidence_kind, cold_start, quota_note))

        if not eligible:
            raise NoEligibleModelError(
                f"No eligible verified model for ({task_type}, {complexity}): " + "; ".join(rejected_reasons[:5])
            )

        # Sort by adequate score descending, then lower cost, then deterministic model_id
        eligible.sort(key=lambda item: (-round(item[0], 6), item[1], item[2].model_id))
        best_score, best_cost, best_m, best_ev, best_ev_kind, best_cold, best_quota = eligible[0]

        alternatives = [
            {
                "provider": item[2].provider,
                "model_id": item[2].model_id,
                "auth_mode": item[2].auth_mode,
                "score": round(item[0], 4),
                "est_cost_usd": item[1],
                "evidence_kind": item[4],
                "cold_start": item[5],
                "quota_status": item[6],
            }
            for item in eligible[1:4]
        ]

        rationale = (
            f"Selected {best_m.provider}/{best_m.model_id} ({best_m.auth_mode}) "
            f"with score={best_score:.4f}, evidence={best_ev_kind}, "
            f"cold_start={best_cold}, quota={best_quota}."
        )
        return RoutingRecommendation(
            task_type=task_type,
            complexity=complexity,
            provider=best_m.provider,
            model_id=best_m.model_id,
            effort=best_ev.effort if best_ev else None,
            auth_mode=best_m.auth_mode,
            rationale=rationale,
            evidence_kind=best_ev_kind,  # type: ignore[arg-type]
            cold_start=best_cold,
            alternatives=alternatives,
        )
