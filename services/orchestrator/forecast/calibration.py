"""Rolling holdout calibration (MAE, WAPE, empirical P90 coverage) and drift detection."""
from __future__ import annotations

from dataclasses import dataclass

from services.orchestrator.contracts import TaskForecast


@dataclass(frozen=True)
class ActualOutcome:
    task_key: str
    actual_input_tokens: int
    actual_output_tokens: int
    actual_cost_usd: float | None = None


@dataclass(frozen=True)
class CalibrationReport:
    sample_count: int
    mae_total_tokens: float | None
    wape_total_tokens: float | None
    empirical_p90_coverage: float | None
    drift_detected: bool
    status: str


def evaluate_calibration(
    pairs: list[tuple[TaskForecast, ActualOutcome]],
    *,
    min_p90_coverage: float = 0.75,
    max_wape: float = 0.35,
) -> CalibrationReport:
    """
    Evaluate rolling holdout MAE, WAPE (safe when actual sum is 0), and empirical P90 coverage.
    Flags drift when empirical coverage drops below min_p90_coverage or WAPE exceeds max_wape.
    """
    valid = [
        (fc, act)
        for fc, act in pairs
        if fc.input_tokens_p50 is not None
        and fc.output_tokens_p50 is not None
        and fc.input_tokens_p90 is not None
        and fc.output_tokens_p90 is not None
    ]
    if not valid:
        return CalibrationReport(
            sample_count=0,
            mae_total_tokens=None,
            wape_total_tokens=None,
            empirical_p90_coverage=None,
            drift_detected=False,
            status="insufficient_data",
        )

    abs_errors: list[int] = []
    actual_sum = 0
    within_p90_count = 0

    for fc, act in valid:
        pred_p50 = (fc.input_tokens_p50 or 0) + (fc.output_tokens_p50 or 0)
        pred_p90 = (fc.input_tokens_p90 or 0) + (fc.output_tokens_p90 or 0)
        actual_tot = act.actual_input_tokens + act.actual_output_tokens
        abs_errors.append(abs(pred_p50 - actual_tot))
        actual_sum += actual_tot
        if actual_tot <= pred_p90:
            within_p90_count += 1

    n = len(valid)
    mae = round(sum(abs_errors) / n, 2)
    # Avoid division by zero (percentage error at zero)
    wape = round(sum(abs_errors) / actual_sum, 4) if actual_sum > 0 else None
    coverage = round(within_p90_count / n, 4)

    drift = (coverage < min_p90_coverage) or (wape is not None and wape > max_wape)
    status = "drift_detected" if drift else "calibrated"
    return CalibrationReport(
        sample_count=n,
        mae_total_tokens=mae,
        wape_total_tokens=wape,
        empirical_p90_coverage=coverage,
        drift_detected=drift,
        status=status,
    )
