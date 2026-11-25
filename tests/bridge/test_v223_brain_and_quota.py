"""V2.2.3 regression tests: live Gemini 4 brain config resolution & pre-approval quota guard."""
from __future__ import annotations

from datetime import datetime, timezone

from test_v2_bridge import client, h, _setup_project  # noqa: F401


def test_brain_config_resolves_live_gemini4_catalog_without_legacy_strings(client):
    pid, _ = _setup_project(client)

    # Without catalog snapshot, tier2_primary stays unavailable (no legacy v1.1.27 string)
    before = client.get("/v2/brain/config", params={"project_id": pid}, headers=h("tok-read"))
    assert before.status_code == 200
    for role in before.json()["roles"]:
        assert "v1.1.27" not in (role.get("availability_reason") or "")

    # Sync live agy 1.5.16 models including Gemini 4 series
    models = [
        dict(provider="agy", model_id=m, cli_family="agy", cli_version="1.5.16", auth_mode="subscription_cli", status="verified")
        for m in ("gemini-4-hydrogen-medium", "gemini-4-neon-medium", "gemini-4-carbon-medium", "claude-sonnet-5-5-medium")
    ]
    assert client.post("/v2/catalog/refresh", headers=h("tok-admin"), json={"models": models}).status_code == 201

    after = client.get("/v2/brain/config", params={"project_id": pid}, headers=h("tok-read"))
    assert after.status_code == 200
    roles = {r["role_name"]: r for r in after.json()["roles"]}
    assert roles["tier1_light"]["effective_model_id"] == "gemini-4-hydrogen-medium"
    assert roles["tier1_light"]["availability_status"] == "verified"
    assert roles["tier2_primary"]["effective_model_id"] == "gemini-4-neon-medium"
    assert roles["tier2_primary"]["availability_status"] == "verified"
    assert roles["tier3_advanced"]["effective_model_id"] == "gemini-4-carbon-medium"
    assert roles["tier3_advanced"]["availability_status"] == "verified"
    # Catalog verified must not claim quota is confirmed
    assert "quota chưa xác nhận" in roles["tier2_primary"]["availability_reason"].lower()


def test_pre_approval_quota_guard_blocks_exhausted_without_silent_swap(client):
    pid, wid = _setup_project(client)
    models = [
        dict(provider="agy", model_id=m, auth_mode="subscription_cli", status="verified")
        for m in ("gemini-4-neon-medium", "claude-sonnet-5-5-medium")
    ]
    assert client.post("/v2/catalog/refresh", headers=h("tok-admin"), json={"models": models}).status_code == 201

    task = dict(
        task_key="T1",
        title="Approval quota guard fixture",
        objective="Check pre-approval quota guard",
        features=dict(task_type="implementation", complexity="M"),
        assigned_agent="agy",
        assigned_provider="agy",
        assigned_model_id="gemini-4-neon-medium",
    )
    created = client.post(
        "/v2/plans",
        headers=h("tok-planner"),
        json=dict(project_id=pid, worktree_id=wid, objective="Approval quota test", tasks=[task]),
    )
    assert created.status_code == 201, created.text
    plan = created.json()

    # Mark gemini-4-neon-medium exhausted
    q_ex = client.post(
        "/v2/quotas",
        headers=h("tok-admin"),
        json=dict(
            provider="agy",
            auth_mode="subscription_cli",
            account_bucket="model:gemini-4-neon-medium",
            quota_kind="subscription_quota",
            unit="availability_flag",
            numerator=1,
            denominator=1,
            source="manual_user_confirmation",
            observed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert q_ex.status_code == 201

    # Approval must be rejected with 409 quota_exhausted (no silent model swap)
    approve_body = dict(
        revision_id=plan["current_revision_id"],
        revision_hash=plan["revisions"][0]["revision_hash"],
        actor="admin",
    )
    r_block = client.post(
        f"/v2/plans/{plan['id']}/approve",
        params={"project_id": pid},
        headers=h("tok-admin"),
        json=approve_body,
    )
    assert r_block.status_code == 409
    assert r_block.json()["error"]["code"] == "quota_exhausted"

    # Manual quota recovery restores approval cleanly with exact original model
    q_ok = client.post(
        "/v2/quotas",
        headers=h("tok-admin"),
        json=dict(
            provider="agy",
            auth_mode="subscription_cli",
            account_bucket="model:gemini-4-neon-medium",
            quota_kind="subscription_quota",
            unit="availability_flag",
            numerator=0,
            denominator=1,
            source="manual_user_confirmation",
            observed_at=datetime.now(timezone.utc).isoformat(),
        ),
    )
    assert q_ok.status_code == 201

    r_ok = client.post(
        f"/v2/plans/{plan['id']}/approve",
        params={"project_id": pid},
        headers=h("tok-admin"),
        json=approve_body,
    )
    assert r_ok.status_code == 200, r_ok.text
    detail = client.get(f"/v2/plans/{plan['id']}", params={"project_id": pid}, headers=h("tok-read")).json()
    assert detail["tasks"][0]["assigned_model_id"] == "gemini-4-neon-medium"
