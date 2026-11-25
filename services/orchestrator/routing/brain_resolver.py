"""Adaptive AI Brain role resolver against verified host CLI capabilities and catalog."""
from __future__ import annotations

from datetime import datetime, timezone

from services.orchestrator.catalog.registry import CatalogSnapshot
from services.orchestrator.contracts import (
    BrainPolicyConfig,
    BrainRole,
    BrainRoleConfig,
    HostCapabilityReport,
)

# Default owner preferences from docs/ADAPTIVE-BRAIN-V2.md (display labels only, never assumed callable)
DEFAULT_PREFERRED_LABELS: dict[BrainRole, str] = {
    "tier0_rules": "Local Deterministic Rules + SQLite FTS5",
    "tier1_light": "Gemini 4 Hydrogen",
    "tier2_primary": "Gemini 4 Neon Medium",
    "tier3_advanced": "Gemini 4 Carbon",
    "secondary_fallback": "Claude Sonnet 5.5 Medium",
}

DEFAULT_VERIFIED_FALLBACK_CANDIDATES: dict[BrainRole, list[str]] = {
    "tier1_light": ["gemini-3.8-flash-low", "gemini-3.8-flash-medium"],
    "tier2_primary": ["claude-sonnet-5-5-medium", "gemini-3.8-flash-high"],
    "tier3_advanced": ["claude-opus-5-5-medium", "claude-opus-5-5-high"],
    "secondary_fallback": ["claude-sonnet-5-5-medium", "claude-sonnet-5-5-low"],
}


def _normalize(text: str) -> str:
    return "".join(ch for ch in text.lower() if ch.isalnum())


def resolve_brain_roles(
    *,
    snapshot: CatalogSnapshot,
    host_report: HostCapabilityReport,
    policy: BrainPolicyConfig,
    preferred_labels: dict[BrainRole, str] | None = None,
) -> list[BrainRoleConfig]:
    """
    Verify each Adaptive Brain role against actual HostCapabilityReport and CatalogSnapshot.
    - Never fabricates unsupported CLI selectors (e.g. Gemini 4 Neon when absent from `agy models`).
    - Never enables paid API fallback unless `policy.allow_paid_api_fallback` is True.
    """
    prefs = dict(DEFAULT_PREFERRED_LABELS)
    if preferred_labels:
        prefs.update(preferred_labels)

    verified_cli_models: dict[str, tuple[str, str, str]] = {}
    for cli in host_report.clis:
        if not cli.installed or not cli.noninteractive_supported:
            continue
        if cli.auth_mode not in policy.allowed_auth_modes and not (
            policy.allow_paid_api_fallback and cli.auth_mode == "api_key"
        ):
            continue
        for mid in cli.verified_model_ids:
            verified_cli_models[mid] = (cli.cli_name, cli.auth_mode, cli.verified_at)

    # Map catalog verified models
    catalog_verified = {
        m.model_id: m
        for m in snapshot.models
        if m.status == "verified"
        and (m.auth_mode in policy.allowed_auth_modes or (policy.allow_paid_api_fallback and m.auth_mode == "api_key"))
    }

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    configs: list[BrainRoleConfig] = []

    for role in ("tier0_rules", "tier1_light", "tier2_primary", "tier3_advanced", "secondary_fallback"):
        pref = prefs[role]
        if role == "tier0_rules":
            configs.append(
                BrainRoleConfig(
                    role_name="tier0_rules",
                    preferred_label=pref,
                    effective_provider="local_rules",
                    effective_model_id="deterministic_rules_v2",
                    auth_mode="unknown",
                    availability_status="verified",
                    availability_reason="Deterministic local rules and SQLite FTS5/forecast arithmetic (0 LLM)",
                    fallback_order=[],
                    max_escalations=0,
                    last_verified_at=now_iso,
                )
            )
            continue

        norm_pref = _normalize(pref)
        matched_id: str | None = None
        for mid in verified_cli_models:
            if _normalize(mid) == norm_pref:
                matched_id = mid
                break

        fallback_candidates = [
            cid for cid in DEFAULT_VERIFIED_FALLBACK_CANDIDATES.get(role, []) if cid in verified_cli_models
        ]

        if matched_id is not None:
            cli_name, auth_mode, ver_at = verified_cli_models[matched_id]
            cat_rec = catalog_verified.get(matched_id)
            configs.append(
                BrainRoleConfig(
                    role_name=role,  # type: ignore[arg-type]
                    preferred_label=pref,
                    effective_provider=cat_rec.provider if cat_rec else cli_name,
                    effective_model_id=matched_id,
                    auth_mode=auth_mode,  # type: ignore[arg-type]
                    availability_status="verified",
                    availability_reason=f"Verified via host CLI '{cli_name}'",
                    fallback_order=fallback_candidates,
                    last_verified_at=ver_at,
                )
            )
        else:
            configs.append(
                BrainRoleConfig(
                    role_name=role,  # type: ignore[arg-type]
                    preferred_label=pref,
                    effective_provider=None,
                    effective_model_id=None,
                    auth_mode="unknown",
                    availability_status="unavailable",
                    availability_reason=(
                        f"Preferred label '{pref}' is not present in verified host CLI models; "
                        "no silent substitution or paid API fallback performed."
                    ),
                    fallback_order=fallback_candidates,
                    last_verified_at=host_report.discovered_at,
                )
            )

    return configs
