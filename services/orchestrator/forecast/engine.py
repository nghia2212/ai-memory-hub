"""Calibrated token/cost/quota forecast engine (0 LLM, strictly pre-execution features)."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from services.orchestrator.contracts import (
    AuthMode,
    ComplexityTier,
    ConfidenceTier,
    ModelRecord,
    PreExecutionFeatures,
    QuotaSnapshot,
    TaskForecast,
    TaskType,
)


@dataclass(frozen=True)
class HistoricalSample:
    project_id: UUID
    provider: str
    model_id: str
    effort: str | None
    auth_mode: AuthMode
    task_type: TaskType
    complexity: ComplexityTier
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    peak_context_tokens: int | None
    cost_usd: float | None
    accepted: bool | None
    observed_at: str
    allow_cross_project: bool = False


@dataclass(frozen=True)
class QuotaCompatibilityResult:
    quota_impact_pct: float | None
    quota_unit: str | None
    note: str


@dataclass(frozen=True)
class PlanForecast:
    forecast_version: str
    task_forecasts: list[TaskForecast]
    total_input_tokens_p50: int | None
    total_input_tokens_p90: int | None
    total_output_tokens_p50: int | None
    total_output_tokens_p90: int | None
    total_cost_usd_p50: float | None
    total_cost_usd_p90: float | None
    confidence: ConfidenceTier
    assumptions: list[str] = field(default_factory=list)


def _weighted_quantile(values_weights: list[tuple[float, float]], q: float) -> float:
    """Compute weighted empirical quantile in [0, 1]."""
    if not values_weights:
        raise ValueError("Empty distribution")
    sorted_vw = sorted(values_weights, key=lambda x: x[0])
    total_w = sum(w for _, w in sorted_vw)
    if total_w <= 0:
        return sorted_vw[min(len(sorted_vw) - 1, int(q * (len(sorted_vw) - 1)))][0]
    target = q * total_w
    acc = 0.0
    for val, w in sorted_vw:
        acc += w
        if acc >= target:
            return val
    return sorted_vw[-1][0]


def _recency_weight(observed_at_iso: str, now: datetime, half_life_days: float = 30.0) -> float:
    try:
        dt = datetime.strptime(observed_at_iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
        return math.exp(-math.log(2.0) * age_days / max(half_life_days, 1.0))
    except ValueError:
        return 1.0


def compute_quota_impact(
    *,
    expected_tokens: int | None,
    expected_cost_usd: float | None,
    quota: QuotaSnapshot | None,
) -> QuotaCompatibilityResult:
    """
    Only compute subscription/budget percentage when documented unit and denominator exist.
    Never infer subscription quota from context_window or missing denominator.
    """
    if quota is None or quota.denominator is None or quota.denominator <= 0:
        return QuotaCompatibilityResult(
            quota_impact_pct=None,
            quota_unit=quota.unit if quota else None,
            note="Không có dữ liệu",
        )
    if quota.quota_kind == "context_window":
        return QuotaCompatibilityResult(
            quota_impact_pct=None,
            quota_unit=quota.unit,
            note="Context window cannot be used to infer subscription quota",
        )

    unit_low = quota.unit.lower()
    if unit_low in ("tokens", "token") and expected_tokens is not None:
        pct = round(100.0 * expected_tokens / quota.denominator, 2)
        return QuotaCompatibilityResult(quota_impact_pct=pct, quota_unit=quota.unit, note="compatible_tokens")
    if unit_low in ("usd", "dollars") and expected_cost_usd is not None:
        pct = round(100.0 * expected_cost_usd / quota.denominator, 2)
        return QuotaCompatibilityResult(quota_impact_pct=pct, quota_unit=quota.unit, note="compatible_usd")

    return QuotaCompatibilityResult(
        quota_impact_pct=None,
        quota_unit=quota.unit,
        note=f"Incompatible quota unit '{quota.unit}'; no calibrated conversion",
    )


_COLD_START_MULT: dict[ComplexityTier, tuple[int, int]] = {
    "XS": (1500, 400),
    "S": (3500, 900),
    "M": (8000, 2000),
    "L": (18000, 4500),
    "XL": (35000, 9000),
}


class ForecastEngine:
    """Deterministic cohort & cold-start forecaster."""

    def __init__(
        self,
        *,
        forecast_version: str = "v2.0",
        high_conf_min_samples: int = 50,
        medium_conf_min_samples: int = 20,
        low_conf_min_samples: int = 5,
    ) -> None:
        self.forecast_version = forecast_version
        self.high_conf_min_samples = high_conf_min_samples
        self.medium_conf_min_samples = medium_conf_min_samples
        self.low_conf_min_samples = low_conf_min_samples

    def _select_cohort(
        self,
        *,
        samples: list[HistoricalSample],
        project_id: UUID,
        provider: str,
        model_id: str,
        effort: str | None,
        auth_mode: AuthMode,
        task_type: TaskType,
        complexity: ComplexityTier,
        allow_cross_project: bool,
    ) -> tuple[list[HistoricalSample], list[str]]:
        scoped = [
            s
            for s in samples
            if (s.project_id == project_id or (allow_cross_project and s.allow_cross_project))
            and s.provider == provider
            and s.model_id == model_id
            and s.auth_mode == auth_mode
        ]
        exact = [
            s
            for s in scoped
            if s.effort == effort and s.task_type == task_type and s.complexity == complexity
        ]
        if len(exact) >= self.medium_conf_min_samples:
            return exact, [f"exact_cohort(n={len(exact)})"]

        # Hierarchical fallback 1: relax task_type, keep complexity + effort
        tier1 = [s for s in scoped if s.effort == effort and s.complexity == complexity]
        if len(tier1) >= self.low_conf_min_samples:
            return tier1, [f"fallback_relaxed_task_type(n={len(tier1)})"]

        # Hierarchical fallback 2: relax effort + task_type, keep complexity
        tier2 = [s for s in scoped if s.complexity == complexity]
        if len(tier2) >= self.low_conf_min_samples:
            return tier2, [f"fallback_model_complexity(n={len(tier2)})"]

        return exact if exact else scoped, [f"insufficient_samples(n={len(exact if exact else scoped)})"]

    def forecast_task(
        self,
        *,
        task_key: str,
        project_id: UUID,
        features: PreExecutionFeatures,
        model: ModelRecord,
        effort: str | None,
        samples: list[HistoricalSample],
        quotas: list[QuotaSnapshot] | None = None,
        allow_cross_project: bool = False,
        calibration_ok: bool = True,
        alternative_models: list[ModelRecord] | None = None,
        now: datetime | None = None,
    ) -> TaskForecast:
        current_dt = now or datetime.now(timezone.utc)
        cohort, cohort_notes = self._select_cohort(
            samples=samples,
            project_id=project_id,
            provider=model.provider,
            model_id=model.model_id,
            effort=effort,
            auth_mode=model.auth_mode,
            task_type=features.task_type,
            complexity=features.complexity,
            allow_cross_project=allow_cross_project,
        )
        complete = [s for s in cohort if s.input_tokens is not None and s.output_tokens is not None]
        n = len(complete)

        assumptions = [f"forecast_version={self.forecast_version}", *cohort_notes]
        if not calibration_ok:
            assumptions.append("calibration_drift_detected: marked uncalibrated")

        if n < self.low_conf_min_samples:
            confidence: ConfidenceTier = "cold_start" if calibration_ok else "uncalibrated"
            base_in, base_out = _COLD_START_MULT[features.complexity]
            ctx_hint = features.context_pack_tokens or 0
            in_p50 = max(base_in, ctx_hint + base_in // 2)
            in_p90 = int(in_p50 * 2.2)
            out_p50 = base_out
            out_p90 = int(out_p50 * 2.5)
            cache_p50 = None
            peak_p90 = min(in_p90, model.context_limit) if model.context_limit else in_p90
            cost_p50, cost_p90 = self._estimate_cost_range(model, in_p50, out_p50, in_p90, out_p90)
            assumptions.append("cold_start_heuristic_range: <5 complete samples")
        else:
            if not calibration_ok:
                confidence = "uncalibrated"
            elif n >= self.high_conf_min_samples:
                confidence = "high"
            elif n >= self.medium_conf_min_samples:
                confidence = "medium"
            else:
                confidence = "low"

            weights = [_recency_weight(s.observed_at, current_dt) for s in complete]
            in_p50 = int(_weighted_quantile([(float(s.input_tokens), w) for s, w in zip(complete, weights)], 0.50))
            in_p90 = int(_weighted_quantile([(float(s.input_tokens), w) for s, w in zip(complete, weights)], 0.90))
            out_p50 = int(_weighted_quantile([(float(s.output_tokens), w) for s, w in zip(complete, weights)], 0.50))
            out_p90 = int(_weighted_quantile([(float(s.output_tokens), w) for s, w in zip(complete, weights)], 0.90))

            cache_vals = [(float(s.cache_read_tokens), w) for s, w in zip(complete, weights) if s.cache_read_tokens is not None]
            cache_p50 = int(_weighted_quantile(cache_vals, 0.50)) if cache_vals else None

            peak_vals = [(float(s.peak_context_tokens), w) for s, w in zip(complete, weights) if s.peak_context_tokens is not None]
            peak_p90 = int(_weighted_quantile(peak_vals, 0.90)) if peak_vals else None

            cost_vals = [(float(s.cost_usd), w) for s, w in zip(complete, weights) if s.cost_usd is not None]
            if cost_vals:
                cost_p50 = round(_weighted_quantile(cost_vals, 0.50), 6)
                cost_p90 = round(_weighted_quantile(cost_vals, 0.90), 6)
            else:
                cost_p50, cost_p90 = self._estimate_cost_range(model, in_p50, out_p50, in_p90, out_p90)

        matching_quota = next(
            (q for q in (quotas or []) if q.provider == model.provider and q.auth_mode == model.auth_mode),
            None,
        )
        expected_total_p90 = (in_p90 or 0) + (out_p90 or 0)
        q_compat = compute_quota_impact(
            expected_tokens=expected_total_p90,
            expected_cost_usd=cost_p90,
            quota=matching_quota,
        )
        if q_compat.quota_impact_pct is None:
            assumptions.append(f"quota_impact: {q_compat.note}")

        alts: list[dict[str, Any]] = []
        if alternative_models:
            for alt_m in alternative_models[:3]:
                if alt_m.model_id == model.model_id and alt_m.auth_mode == model.auth_mode:
                    continue
                alt_fc = self.forecast_task(
                    task_key=task_key,
                    project_id=project_id,
                    features=features,
                    model=alt_m,
                    effort=effort,
                    samples=samples,
                    quotas=quotas,
                    allow_cross_project=allow_cross_project,
                    calibration_ok=calibration_ok,
                    alternative_models=None,
                    now=current_dt,
                )
                alts.append(
                    {
                        "provider": alt_fc.provider,
                        "model_id": alt_fc.model_id,
                        "auth_mode": alt_fc.auth_mode,
                        "confidence": alt_fc.confidence,
                        "input_tokens_p50": alt_fc.input_tokens_p50,
                        "output_tokens_p50": alt_fc.output_tokens_p50,
                        "cost_usd_p50": alt_fc.cost_usd_p50,
                        "cost_usd_p90": alt_fc.cost_usd_p90,
                    }
                )

        return TaskForecast(
            task_key=task_key,
            provider=model.provider,
            model_id=model.model_id,
            effort=effort,
            auth_mode=model.auth_mode,
            complexity=features.complexity,
            confidence=confidence,
            sample_count=n,
            input_tokens_p50=in_p50,
            input_tokens_p90=in_p90,
            output_tokens_p50=out_p50,
            output_tokens_p90=out_p90,
            cache_read_tokens_p50=cache_p50,
            peak_context_tokens_p90=peak_p90,
            cost_usd_p50=cost_p50,
            cost_usd_p90=cost_p90,
            quota_impact_pct=q_compat.quota_impact_pct,
            quota_unit=q_compat.quota_unit,
            assumptions=assumptions,
            alternatives=alts,
        )

    @staticmethod
    def _estimate_cost_range(
        model: ModelRecord, in_p50: int, out_p50: int, in_p90: int, out_p90: int
    ) -> tuple[float | None, float | None]:
        if model.auth_mode != "api_key":
            # Subscription CLI is not priced as API usage unless explicit cost samples exist
            return None, None
        if model.price_input_per_1m is None or model.price_output_per_1m is None:
            return None, None
        c50 = round((in_p50 * model.price_input_per_1m + out_p50 * model.price_output_per_1m) / 1_000_000.0, 6)
        c90 = round((in_p90 * model.price_input_per_1m + out_p90 * model.price_output_per_1m) / 1_000_000.0, 6)
        return c50, c90

    def aggregate_plan(
        self,
        *,
        task_forecasts: list[TaskForecast],
        planner_overhead_input: int = 0,
        planner_overhead_output: int = 0,
        planner_cost_usd: float | None = None,
        resamples: int = 500,
        seed: int = 42,
    ) -> PlanForecast:
        """
        Aggregate task forecasts using deterministic resampled joint distributions
        rather than naively summing independent P90 quantiles.
        """
        if not task_forecasts:
            return PlanForecast(
                forecast_version=self.forecast_version,
                task_forecasts=[],
                total_input_tokens_p50=planner_overhead_input,
                total_input_tokens_p90=planner_overhead_input,
                total_output_tokens_p50=planner_overhead_output,
                total_output_tokens_p90=planner_overhead_output,
                total_cost_usd_p50=planner_cost_usd,
                total_cost_usd_p90=planner_cost_usd,
                confidence="cold_start",
                assumptions=["empty_plan"],
            )

        rng = random.Random(seed)
        in_totals: list[float] = []
        out_totals: list[float] = []
        cost_totals: list[float] = []
        has_costs = all(tf.cost_usd_p50 is not None and tf.cost_usd_p90 is not None for tf in task_forecasts)

        for _ in range(resamples):
            s_in = float(planner_overhead_input)
            s_out = float(planner_overhead_output)
            s_cost = float(planner_cost_usd or 0.0)
            for tf in task_forecasts:
                # Sample between P50 and P90 / lower tail using triangular draw on empirical range
                in_lo = float(tf.input_tokens_p50 or 0) * 0.6
                in_mid = float(tf.input_tokens_p50 or 0)
                in_hi = float(tf.input_tokens_p90 or in_mid)
                s_in += rng.triangular(in_lo, max(in_hi, in_lo), in_mid)

                out_lo = float(tf.output_tokens_p50 or 0) * 0.6
                out_mid = float(tf.output_tokens_p50 or 0)
                out_hi = float(tf.output_tokens_p90 or out_mid)
                s_out += rng.triangular(out_lo, max(out_hi, out_lo), out_mid)

                if has_costs:
                    c_lo = float(tf.cost_usd_p50 or 0.0) * 0.6
                    c_mid = float(tf.cost_usd_p50 or 0.0)
                    c_hi = float(tf.cost_usd_p90 or c_mid)
                    s_cost += rng.triangular(c_lo, max(c_hi, c_lo), c_mid)

            in_totals.append(s_in)
            out_totals.append(s_out)
            if has_costs:
                cost_totals.append(s_cost)

        in_totals.sort()
        out_totals.sort()
        idx50 = int(0.50 * (resamples - 1))
        idx90 = int(0.90 * (resamples - 1))

        conf_order = {"uncalibrated": 0, "cold_start": 1, "low": 2, "medium": 3, "high": 4}
        min_conf = min(task_forecasts, key=lambda t: conf_order[t.confidence]).confidence

        c50 = round(sorted(cost_totals)[idx50], 6) if has_costs else None
        c90 = round(sorted(cost_totals)[idx90], 6) if has_costs else None

        return PlanForecast(
            forecast_version=self.forecast_version,
            task_forecasts=task_forecasts,
            total_input_tokens_p50=int(in_totals[idx50]),
            total_input_tokens_p90=int(in_totals[idx90]),
            total_output_tokens_p50=int(out_totals[idx50]),
            total_output_tokens_p90=int(out_totals[idx90]),
            total_cost_usd_p50=c50,
            total_cost_usd_p90=c90,
            confidence=min_conf,
            assumptions=[
                f"resampled_joint_plan(resamples={resamples}, seed={seed})",
                "independent_p90_summation_avoided",
            ],
        )
