"""Tests for Vietnamese Admin Dashboard (auth, CSRF, Origin, proxy, no leaked secrets)."""
from __future__ import annotations

import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from fastapi.testclient import TestClient

BRIDGE_ROOT = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
DASH_ROOT = Path(__file__).resolve().parents[2] / "services" / "dashboard"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture()
def bridge_url(tmp_path: Path):
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    if str(BRIDGE_ROOT) in sys.path:
        sys.path.remove(str(BRIDGE_ROOT))
    sys.path.insert(0, str(BRIDGE_ROOT))
    from app.config import Settings
    from app.main import create_app

    port = _free_port()
    app = create_app(
        Settings(
            data_dir=tmp_path,
            db_path=tmp_path / "dash_bridge.db",
            backup_dir=tmp_path / "backups",
            read_tokens={"secret-read-token"},
            write_tokens={"secret-write-token"},
            admin_tokens={"secret-admin-token"},
            worker_tokens={"secret-worker-token"},
        )
    )
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            if httpx.get(f"{url}/healthz", timeout=0.5).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    yield url
    server.should_exit = True
    t.join(timeout=3)


@pytest.fixture()
def dash_client(bridge_url: str) -> TestClient:
    # Clear cached `app` module from bridge before importing dashboard's `app`
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    if str(DASH_ROOT) in sys.path:
        sys.path.remove(str(DASH_ROOT))
    sys.path.insert(0, str(DASH_ROOT))

    from app.config import DashboardSettings
    from app.main import create_dashboard_app

    cfg = DashboardSettings(
        bridge_url=bridge_url,
        bridge_read_token="secret-read-token",
        bridge_admin_token="secret-admin-token",
        dashboard_password="admin-pass-vn",
        session_secret="test-session-secret-1234567890",
        allowed_origins={"http://testserver", "http://127.0.0.1:8080"},
    )
    with TestClient(create_dashboard_app(cfg)) as c:
        yield c


def test_dashboard_html_vietnamese_and_no_secrets(dash_client: TestClient):
    assert dash_client.get("/livez").status_code == 200
    assert dash_client.get("/healthz").status_code == 200

    html = dash_client.get("/").text
    assert "Bảng điều khiển quản trị" in html
    assert "Duyệt bộ nhớ" in html
    assert "Bộ não điều phối" in html
    assert "Điều phối (Planner)" in html
    assert "Không có dữ liệu" in html
    assert "Sao lưu & Khôi phục" in html
    assert "secret-admin-token" not in html
    assert "secret-read-token" not in html
    assert "admin-pass-vn" not in html


def test_dashboard_auth_origin_csrf_and_proxy(dash_client: TestClient):
    # Unauthenticated mutation rejected
    r_unauth = dash_client.post(
        "/api/projects",
        headers={"Origin": "http://testserver"},
        json={"name": "p", "canonical_identity": "git:p"},
    )
    assert r_unauth.status_code == 401

    # Login without Origin -> 403
    r_no_origin = dash_client.post("/api/auth/login", json={"password": "admin-pass-vn"})
    assert r_no_origin.status_code == 403

    # Login with evil Origin -> 403
    r_bad_origin = dash_client.post(
        "/api/auth/login",
        headers={"Origin": "https://evil.example"},
        json={"password": "admin-pass-vn"},
    )
    assert r_bad_origin.status_code == 403

    # Login with wrong password -> 401
    r_bad_pw = dash_client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"password": "wrong"},
    )
    assert r_bad_pw.status_code == 401

    # Valid login
    r_ok = dash_client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"password": "admin-pass-vn"},
    )
    assert r_ok.status_code == 200
    csrf = r_ok.json()["csrf_token"]
    assert csrf

    # Mutation without CSRF -> 403
    r_no_csrf = dash_client.post(
        "/api/projects",
        headers={"Origin": "http://testserver"},
        json={"name": "p1", "canonical_identity": "git:p1"},
    )
    assert r_no_csrf.status_code == 403

    # Mutation with valid session + Origin + CSRF -> 201
    r_create = dash_client.post(
        "/api/projects",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={"name": "Dự án thử nghiệm", "canonical_identity": "git:test-vn"},
    )
    assert r_create.status_code == 201
    assert r_create.json()["name"] == "Dự án thử nghiệm"

    # Read-only metrics & agents status via proxy
    metrics = dash_client.get("/api/metrics")
    assert metrics.status_code == 200
    assert metrics.json()["measured_tokens_available"] is False

    pid = r_create.json()["project_id"]
    agents_st = dash_client.get(f"/api/agents/status?project_id={pid}")
    assert agents_st.status_code == 200
    assert agents_st.json()["items"] == []

    # V2 Adaptive Brain config & separate Planner/Executor usage endpoints
    brain_cfg = dash_client.get(f"/api/v2/brain/config?project_id={pid}")
    assert brain_cfg.status_code == 200
    cfg_data = brain_cfg.json()
    assert cfg_data["approval_mode"] == "REVIEW_REQUIRED"
    assert cfg_data["allow_paid_api_fallback"] is False
    roles = {r["role_name"]: r for r in cfg_data["roles"]}
    assert roles["tier2_primary"]["preferred_label"] == "Gemini 4 Neon Medium"
    assert roles["tier2_primary"]["availability_status"] == "unavailable"

    v2_usage = dash_client.get(f"/api/v2/usage?project_id={pid}")
    assert v2_usage.status_code == 200
    assert v2_usage.json()["planner"]["input_tokens"] is None
    assert v2_usage.json()["executor"]["input_tokens"] is None

    # Unauthenticated PUT /api/v2/brain/config must be rejected (403 CSRF missing)
    r_brain_no_csrf = dash_client.put(
        "/api/v2/brain/config",
        headers={"Origin": "http://testserver"},
        json={"project_id": pid, "approval_mode": "REVIEW_REQUIRED"},
    )
    assert r_brain_no_csrf.status_code == 403
