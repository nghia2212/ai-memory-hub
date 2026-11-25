"""Tests for V2 official catalog adapters, lifecycle, host CLI discovery, routing, and Adaptive Brain."""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.orchestrator.catalog import (  # noqa: E402
    AnthropicCatalogAdapter,
    CatalogService,
    CatalogStaleError,
    GoogleGeminiCatalogAdapter,
    OpenAICatalogAdapter,
)
from services.orchestrator.catalog.sources import SourceFetchError  # noqa: E402
from services.orchestrator.contracts import (  # noqa: E402
    BrainPolicyConfig,
    HostCapabilityReport,
    HostCLICapability,
    QuotaSnapshot,
)
from services.orchestrator.routing import (  # noqa: E402
    DeterministicRouter,
    EvaluationEntry,
    EvaluationMatrix,
    NoEligibleModelError,
    RoutingConstraints,
    resolve_brain_roles,
)


def _verified_agy_host_report() -> HostCapabilityReport:
    return HostCapabilityReport(
        host_os="windows",
        discovered_at="2026-11-25T10:16:57Z",
        clis=[
            HostCLICapability(
                cli_name="agy",
                installed=True,
                version="1.1.27",
                auth_mode="subscription_cli",
                noninteractive_supported=True,
                structured_json_supported=True,
                verified_model_ids=[
                    "gemini-3.8-flash-high",
                    "gemini-3.8-flash-medium",
                    "gemini-3.8-flash-low",
                    "claude-opus-5-5-low",
                    "claude-opus-5-5-medium",
                    "claude-opus-5-5-high",
                    "claude-sonnet-5-5-low",
                    "claude-sonnet-5-5-medium",
                    "claude-sonnet-5-5-high",
                    "gpt-oss-120b-medium",
                ],
                supported_telemetry_fields=["input_tokens", "output_tokens"],
                unsupported_fields=["subscription_quota"],
                verified_at="2026-11-25T10:16:57Z",
            )
        ],
    )


def test_official_adapter_allowlist_and_quarantine_and_retirement() -> None:
    bad_adapter = AnthropicCatalogAdapter(source_url="https://untrusted.example.com/models.json")
    with pytest.raises(SourceFetchError):
        bad_adapter.fetch_models()

    payload_v1 = json.dumps(
        {
            "models": [
                {
                    "model_id": "claude-sonnet-5-5",
                    "auth_mode": "api_key",
                    "status": "verified",
                    "context_limit": 1000000,
                    "output_limit": 128000,
                    "price_input_per_1m": 2.0,
                    "price_output_per_1m": 10.0,
                },
                {
                    "model_id": "deepseek-reasoner",
                    "auth_mode": "api_key",
                    "status": "verified",
                    "price_input_per_1m": 0.5,
                    "price_output_per_1m": 2.0,
                },
            ]
        }
    ).encode("utf-8")

    payload_v2 = json.dumps(
        {
            "models": [
                {
                    "model_id": "claude-sonnet-5-5",
                    "auth_mode": "api_key",
                    "status": "verified",
                    "context_limit": 1000000,
                }
                # deepseek-reasoner removed -> must emit retirement alert, no silent removal
            ]
        }
    ).encode("utf-8")

    state = {"raw": payload_v1}
    adapter = AnthropicCatalogAdapter(fetcher=lambda _u, _t, _m: state["raw"])
    svc = CatalogService(adapters=[adapter], ttl_hours=24)

    t0 = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    snap1 = svc.refresh(host_report=_verified_agy_host_report(), now=t0)
    assert snap1.version == 1
    assert snap1.is_stale is False
    # New official API models enter candidate quarantine
    api_sonnet = next(m for m in snap1.models if m.model_id == "claude-sonnet-5-5" and m.auth_mode == "api_key")
    assert api_sonnet.status == "candidate"
    # Host-verified subscription CLI models are verified
    cli_sonnet = next(m for m in snap1.models if m.model_id == "claude-sonnet-5-5-medium")
    assert cli_sonnet.status == "verified"

    # Refresh v2 with missing model -> retirement alert
    state["raw"] = payload_v2
    snap2 = svc.refresh(host_report=_verified_agy_host_report(), now=t0 + timedelta(hours=1))
    assert any(a.model_id == "deepseek-reasoner" for a in snap2.alerts)
    dep = next(m for m in snap2.models if m.model_id == "deepseek-reasoner")
    assert dep.status == "deprecated"


def test_stale_fallback_blocks_automatic_routing() -> None:
    def failing_fetcher(_u: str, _t: float, _m: int) -> bytes:
        raise SourceFetchError("network down")

    good_payload = json.dumps({"models": [{"model_id": "gpt-6", "auth_mode": "api_key", "status": "verified"}]}).encode()
    flag = {"fail": False}

    def toggle_fetcher(u: str, t: float, m: int) -> bytes:
        if flag["fail"]:
            return failing_fetcher(u, t, m)
        return good_payload

    svc = CatalogService(adapters=[OpenAICatalogAdapter(fetcher=toggle_fetcher)])
    snap1 = svc.refresh()
    assert snap1.is_stale is False

    flag["fail"] = True
    snap2 = svc.refresh()
    assert snap2.is_stale is True

    router = DeterministicRouter()
    with pytest.raises(CatalogStaleError):
        router.recommend(
            task_type="implementation",
            complexity="M",
            snapshot=snap2,
            evaluations=EvaluationMatrix(),
            constraints=RoutingConstraints(),
        )


def test_adaptive_brain_neon_unavailable_and_no_paid_fallback() -> None:
    svc = CatalogService(adapters=[GoogleGeminiCatalogAdapter(fetcher=lambda _u, _t, _m: b'{"models":[]}')])
    host_report = _verified_agy_host_report()
    snap = svc.refresh(host_report=host_report)

    policy = BrainPolicyConfig(
        project_id=uuid.uuid4(),
        allow_paid_api_fallback=False,
        allowed_auth_modes=["subscription_cli"],
    )
    roles = resolve_brain_roles(snapshot=snap, host_report=host_report, policy=policy)
    by_name = {r.role_name: r for r in roles}

    assert by_name["tier0_rules"].availability_status == "verified"
    assert by_name["tier0_rules"].effective_model_id == "deterministic_rules_v2"

    # Gemini 4 Neon Medium is NOT in `agy models` -> MUST be unavailable, never fabricated
    assert by_name["tier2_primary"].preferred_label == "Gemini 4 Neon Medium"
    assert by_name["tier2_primary"].availability_status == "unavailable"
    assert by_name["tier2_primary"].effective_model_id is None
    assert "claude-sonnet-5-5-medium" in by_name["tier2_primary"].fallback_order
    assert "gemini-3.8-flash-high" in by_name["tier2_primary"].fallback_order

    assert by_name["tier1_light"].availability_status == "unavailable"
    assert "gemini-3.8-flash-low" in by_name["tier1_light"].fallback_order


def test_deterministic_router_prefers_cheapest_adequate_and_respects_quota() -> None:
    svc = CatalogService(adapters=[])
    snap = svc.refresh(host_report=_verified_agy_host_report())

    matrix = EvaluationMatrix()
    proj_id = uuid.uuid4()
    # Both flash-medium and opus-medium are adequate (>=0.80), flash-medium is cheaper/faster
    matrix.add(
        EvaluationEntry(
            project_id=proj_id,
            model_id="gemini-3.8-flash-medium",
            provider="google",
            auth_mode="subscription_cli",
            task_type="implementation",
            complexity="S",
            evidence_kind="observed",
            correctness_score=0.88,
            avg_latency_ms=900.0,
            avg_effective_cost_usd=0.01,
            failure_rate=0.02,
            sample_count=25,
        )
    )
    matrix.add(
        EvaluationEntry(
            project_id=proj_id,
            model_id="claude-opus-5-5-medium",
            provider="anthropic",
            auth_mode="subscription_cli",
            task_type="implementation",
            complexity="S",
            evidence_kind="observed",
            correctness_score=0.90,
            avg_latency_ms=4500.0,
            avg_effective_cost_usd=0.15,
            failure_rate=0.02,
            sample_count=25,
        )
    )

    router = DeterministicRouter()
    rec = router.recommend(
        task_type="implementation",
        complexity="S",
        snapshot=snap,
        evaluations=matrix,
        constraints=RoutingConstraints(project_id=proj_id, max_cost_usd=0.5),
    )
    assert rec.model_id == "gemini-3.8-flash-medium"
    assert rec.cold_start is False
    assert rec.evidence_kind == "observed"

    # If Google subscription quota is exhausted, router skips google and picks anthropic
    exhausted_quota = [
        QuotaSnapshot(
            provider="google",
            auth_mode="subscription_cli",
            quota_kind="subscription_quota",
            unit="requests",
            numerator=100.0,
            denominator=100.0,
            source="mock",
            observed_at="2026-11-25T10:00:00Z",
        )
    ]
    rec2 = router.recommend(
        task_type="implementation",
        complexity="S",
        snapshot=snap,
        evaluations=matrix,
        constraints=RoutingConstraints(project_id=proj_id, max_cost_usd=0.5),
        quotas=exhausted_quota,
    )
    assert rec2.provider == "anthropic"

    # If paid API fallback is disabled and no subscription model fits, fail closed
    with pytest.raises(NoEligibleModelError):
        router.recommend(
            task_type="implementation",
            complexity="S",
            snapshot=snap,
            evaluations=matrix,
            constraints=RoutingConstraints(project_id=proj_id, allowed_auth_modes=("api_key",), allow_paid_api_fallback=False),
        )
