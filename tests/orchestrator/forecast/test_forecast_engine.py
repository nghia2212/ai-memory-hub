"""Tests for V2 complexity rubric, cohort forecasting, plan aggregation, quota compatibility, and drift."""
from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.orchestrator.contracts import (  # noqa: E402
    ModelRecord,
    PreExecutionFeatures,
    QuotaSnapshot,
)
from services.orchestrator.forecast import (  # noqa: E402
    ActualOutcome,
    ForecastEngine,
    HistoricalSample,
    classify_complexity,
    compute_quota_impact,
    evaluate_calibration,
)


def test_complexity_rubric_pre_execution_only() -> None:
    xs = PreExecutionFeatures(task_type="routine", complexity="M", file_count=1, expected_edits=1, uncertainty_level="low")
    xl = PreExecutionFeatures(
        task_type="architecture",
        complexity="S",
        file_count=20,
        expected_edits=12,
        dependency_count=5,
        expected_tests=6,
        uncertainty_level="high",
    )
    assert classify_complexity(xs) == "XS"
    assert classify_complexity(xl) == "XL"


def test_cold_start_and_unknown_quota_never_fabricated() -> None:
    engine = ForecastEngine()
    proj_id = uuid.uuid4()
    other_proj = uuid.uuid4()
    model = ModelRecord(provider="anthropic", model_id="claude-sonnet-5-5-medium", auth_mode="subscription_cli", status="verified")
    features = PreExecutionFeatures(task_type="implementation", complexity="M", context_pack_tokens=2000)

    # Samples from another project MUST be isolated when allow_cross_project=False
    foreign_samples = [
        HistoricalSample(
            project_id=other_proj,
            provider="anthropic",
            model_id="claude-sonnet-5-5-medium",
            effort="medium",
            auth_mode="subscription_cli",
            task_type="implementation",
            complexity="M",
            input_tokens=5000,
            output_tokens=1200,
            cache_read_tokens=1000,
            peak_context_tokens=6000,
            cost_usd=None,
            accepted=True,
            observed_at="2026-11-24T10:00:00Z",
            allow_cross_project=False,
        )
        for _ in range(25)
    ]

    # Quota with incompatible unit ('messages') and context_window must return quota_impact_pct=None
    msg_quota = QuotaSnapshot(
        provider="anthropic",
        auth_mode="subscription_cli",
        quota_kind="subscription_quota",
        unit="messages",
        numerator=10.0,
        denominator=100.0,
        source="mock",
        observed_at="2026-11-25T10:00:00Z",
    )
    fc = engine.forecast_task(
        task_key="t1",
        project_id=proj_id,
        features=features,
        model=model,
        effort="medium",
        samples=foreign_samples,
        quotas=[msg_quota],
    )
    assert fc.confidence == "cold_start"
    assert fc.sample_count == 0
    assert fc.cost_usd_p50 is None  # subscription_cli is not priced as API usage
    assert fc.quota_impact_pct is None  # unknown/incompatible quota never fabricated

    # Context window cannot be used to infer subscription quota
    ctx_only = QuotaSnapshot(
        provider="anthropic",
        auth_mode="subscription_cli",
        quota_kind="context_window",
        unit="tokens",
        numerator=1000.0,
        denominator=1000000.0,
        source="spec",
        observed_at="2026-11-25T10:00:00Z",
    )
    q_res = compute_quota_impact(expected_tokens=10000, expected_cost_usd=None, quota=ctx_only)
    assert q_res.quota_impact_pct is None


def test_cohort_quantiles_hierarchical_fallback_and_plan_aggregation() -> None:
    engine = ForecastEngine()
    proj_id = uuid.uuid4()
    model = ModelRecord(
        provider="openai",
        model_id="gpt-6",
        auth_mode="api_key",
        status="verified",
        price_input_per_1m=2.0,
        price_output_per_1m=8.0,
    )
    alt_model = ModelRecord(
        provider="google",
        model_id="gemini-3.8-flash-medium",
        auth_mode="subscription_cli",
        status="verified",
    )

    samples = [
        HistoricalSample(
            project_id=proj_id,
            provider="openai",
            model_id="gpt-6",
            effort="medium",
            auth_mode="api_key",
            task_type="implementation",
            complexity="M",
            input_tokens=4000 + i * 200,
            output_tokens=1000 + i * 50,
            cache_read_tokens=1500,
            peak_context_tokens=5000 + i * 200,
            cost_usd=0.02 + i * 0.001,
            accepted=True,
            observed_at="2026-11-24T10:00:00Z",
        )
        for i in range(25)
    ]

    token_quota = QuotaSnapshot(
        provider="openai",
        auth_mode="api_key",
        quota_kind="api_budget",
        unit="usd",
        numerator=1.0,
        denominator=10.0,
        source="account",
        observed_at="2026-11-25T10:00:00Z",
    )

    fc1 = engine.forecast_task(
        task_key="t1",
        project_id=proj_id,
        features=PreExecutionFeatures(task_type="implementation", complexity="M"),
        model=model,
        effort="medium",
        samples=samples,
        quotas=[token_quota],
        alternative_models=[alt_model],
        now=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
    )
    assert fc1.confidence == "medium"
    assert fc1.sample_count == 25
    assert fc1.input_tokens_p50 is not None and fc1.input_tokens_p90 is not None
    assert fc1.input_tokens_p90 >= fc1.input_tokens_p50
    assert fc1.quota_impact_pct is not None and fc1.quota_unit == "usd"
    assert len(fc1.alternatives) == 1

    # Hierarchical fallback when exact task_type ('testing') has 0 samples, but same complexity ('M') has 25
    fc2 = engine.forecast_task(
        task_key="t2",
        project_id=proj_id,
        features=PreExecutionFeatures(task_type="testing", complexity="M"),
        model=model,
        effort="medium",
        samples=samples,
        quotas=[token_quota],
        now=datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc),
    )
    assert fc2.confidence == "medium"
    assert any("fallback_relaxed_task_type" in a for a in fc2.assumptions)

    plan_fc = engine.aggregate_plan(
        task_forecasts=[fc1, fc2],
        planner_overhead_input=500,
        planner_overhead_output=200,
        planner_cost_usd=0.005,
    )
    naive_p90_sum = (fc1.input_tokens_p90 or 0) + (fc2.input_tokens_p90 or 0) + 500
    assert plan_fc.total_input_tokens_p90 is not None
    assert plan_fc.total_input_tokens_p90 <= naive_p90_sum


def test_calibration_mae_wape_and_drift_detection() -> None:
    engine = ForecastEngine()
    proj_id = uuid.uuid4()
    model = ModelRecord(provider="google", model_id="gemini-3.8-flash-low", auth_mode="subscription_cli", status="verified")
    fc = engine.forecast_task(
        task_key="t",
        project_id=proj_id,
        features=PreExecutionFeatures(task_type="routine", complexity="XS"),
        model=model,
        effort="low",
        samples=[],
    )
    # All actuals exceed P90 -> drift_detected=True
    bad_pairs = [(fc, ActualOutcome(task_key="t", actual_input_tokens=50000, actual_output_tokens=10000)) for _ in range(10)]
    rep = evaluate_calibration(bad_pairs)
    assert rep.drift_detected is True
    assert rep.empirical_p90_coverage == 0.0
    assert rep.wape_total_tokens is not None

    # Zero-actual safety: WAPE must be None rather than ZeroDivisionError
    zero_pairs = [(fc, ActualOutcome(task_key="t", actual_input_tokens=0, actual_output_tokens=0))]
    rep_zero = evaluate_calibration(zero_pairs)
    assert rep_zero.wape_total_tokens is None
