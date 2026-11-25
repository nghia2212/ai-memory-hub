"""V2.2.3 regression tests: enforce authentication on all Dashboard /api/* read routes and CSRF on writes."""
from __future__ import annotations

import uuid

from test_v2_dashboard import v2_bridge_url, v2_dash_client  # noqa: F401

PUBLIC_API_GET = {"/api/status", "/api/auth/session"}
PUBLIC_API_POST = {"/api/auth/login"}


def _materialize_path(path: str) -> str:
    uid = str(uuid.uuid4())
    return (
        path.replace("{project_id}", uid)
        .replace("{plan_id}", uid)
        .replace("{trace_id}", uid)
        .replace("{session_id}", uid)
        .replace("{memory_id}", uid)
        .replace("{job_id}", uid)
        .replace("{task_id}", "V3-M1")
        .replace("{view}", "index")
        .replace("{action}", "approve")
    )


def test_all_dashboard_api_routes_require_auth_and_csrf(v2_dash_client):
    app = v2_dash_client.app
    checked_get = 0
    checked_mut = 0

    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/api/"):
            continue
        methods = set(getattr(route, "methods", set())) - {"HEAD", "OPTIONS"}
        url = _materialize_path(path)
        query = f"?project_id={uuid.uuid4()}&worktree_id={uuid.uuid4()}&session_id={uuid.uuid4()}"

        for method in sorted(methods):
            if method == "GET":
                if path in PUBLIC_API_GET:
                    r = v2_dash_client.get(url)
                    assert r.status_code == 200, f"Public GET {path} failed: {r.status_code}"
                    continue
                r = v2_dash_client.get(url + query)
                assert r.status_code == 401, f"Unauthenticated GET {path} must return 401, got {r.status_code}"
                checked_get += 1
            else:
                if method == "POST" and path in PUBLIC_API_POST:
                    continue
                r = v2_dash_client.request(
                    method,
                    url + query,
                    headers={"Origin": "http://testserver"},
                    json={},
                )
                assert r.status_code == 401, f"Unauthenticated {method} {path} must return 401, got {r.status_code}"
                checked_mut += 1

    assert checked_get >= 18, f"Expected >= 18 protected GET /api/* routes, found {checked_get}"
    assert checked_mut >= 14, f"Expected >= 14 protected mutating /api/* routes, found {checked_mut}"

    # Verify authenticated access works and missing CSRF is rejected with 403
    login = v2_dash_client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"password": "v2-dash-password"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    assert v2_dash_client.get("/api/projects").status_code == 200
    assert v2_dash_client.get("/api/v2/catalog").status_code == 200
    assert v2_dash_client.get("/api/v2/quotas").status_code == 200

    # Authenticated search/code without CSRF must be 403
    r_no_csrf = v2_dash_client.post(
        "/api/search/memory",
        headers={"Origin": "http://testserver"},
        json={"project_id": str(uuid.uuid4()), "worktree_id": str(uuid.uuid4()), "query": "test"},
    )
    assert r_no_csrf.status_code == 403

    # Logout clears access again
    r_out = v2_dash_client.post(
        "/api/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert r_out.status_code == 200
    assert v2_dash_client.get("/api/projects").status_code == 401
