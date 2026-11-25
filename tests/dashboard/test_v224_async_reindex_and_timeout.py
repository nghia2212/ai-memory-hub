"""V2.2.4 regression tests: async reindex proxy, GET /api/index/{job_id} auth, and 504 timeout vs 503 offline distinction."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import httpx

from test_v2_dashboard import v2_bridge_url, v2_dash_client  # noqa: F401


def test_dashboard_distinguishes_504_timeout_from_503_offline_and_protects_index_status(v2_dash_client):
    # GET /api/index/{job_id} requires authentication
    r_unauth = v2_dash_client.get(f"/api/index/{uuid.uuid4()}")
    assert r_unauth.status_code == 401

    login = v2_dash_client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"password": "v2-dash-password"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    # Simulate timeout from Bridge -> must return 504 bridge_timeout (NOT 503 bridge_unavailable)
    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=httpx.ReadTimeout("timed out"))):
        r_to = v2_dash_client.post(
            "/api/index",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={"project_id": str(uuid.uuid4()), "worktree_id": str(uuid.uuid4())},
        )
    assert r_to.status_code == 504
    assert r_to.json()["error"]["code"] == "bridge_timeout"

    # Simulate connection refused -> must return 503 bridge_unavailable
    with patch("httpx.AsyncClient.request", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
        r_off = v2_dash_client.post(
            "/api/index",
            headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
            json={"project_id": str(uuid.uuid4()), "worktree_id": str(uuid.uuid4())},
        )
    assert r_off.status_code == 503
    assert r_off.json()["error"]["code"] == "bridge_unavailable"
