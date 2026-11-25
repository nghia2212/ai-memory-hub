"""V2 Dashboard proxy, Vietnamese UI, CSRF/Origin and unknown quota tests."""
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
def v2_bridge_url(tmp_path: Path):
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
            db_path=tmp_path / "dash_v2_bridge.db",
            backup_dir=tmp_path / "backups",
            read_tokens={"v2-read-secret"},
            write_tokens={"v2-write-secret"},
            planner_tokens={"v2-planner-secret"},
            runner_tokens={"v2-runner-secret"},
            admin_tokens={"v2-admin-secret"},
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
def v2_dash_client(v2_bridge_url: str) -> TestClient:
    for mod in list(sys.modules):
        if mod == "app" or mod.startswith("app."):
            del sys.modules[mod]
    if str(DASH_ROOT) in sys.path:
        sys.path.remove(str(DASH_ROOT))
    sys.path.insert(0, str(DASH_ROOT))

    from app.config import DashboardSettings
    from app.main import create_dashboard_app

    cfg = DashboardSettings(
        bridge_url=v2_bridge_url,
        bridge_read_token="v2-read-secret",
        bridge_admin_token="v2-admin-secret",
        dashboard_password="v2-dash-password",
        session_secret="v2-session-secret-1234567890",
        allowed_origins={"http://testserver", "http://127.0.0.1:8080"},
    )
    with TestClient(create_dashboard_app(cfg)) as c:
        yield c


def test_v2_dashboard_ui_and_proxy_flows(v2_dash_client: TestClient):
    html = v2_dash_client.get("/").text
    assert "Bộ não điều phối" in html
    assert "Kế hoạch & Duyệt DAG" in html
    assert "Phiên & Dự báo" in html
    assert "Danh mục Model" in html
    assert "Không có dữ liệu" in html
    assert "v2-admin-secret" not in html
    assert "v2-dash-password" not in html

    # Login
    login = v2_dash_client.post(
        "/api/auth/login",
        headers={"Origin": "http://testserver"},
        json={"password": "v2-dash-password"},
    )
    assert login.status_code == 200
    csrf = login.json()["csrf_token"]

    # Create project via proxy
    p = v2_dash_client.post(
        "/api/projects",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={"name": "v2-ui-proj", "canonical_identity": "git:v2-ui"},
    )
    assert p.status_code == 201
    pid = p.json()["project_id"]

    # Read V2 brain config & usage via proxy
    bc = v2_dash_client.get(f"/api/v2/brain/config?project_id={pid}")
    assert bc.status_code == 200
    assert any(r["role_name"] == "tier2_primary" and r["availability_status"] == "unavailable" for r in bc.json()["roles"])

    usage = v2_dash_client.get(f"/api/v2/usage?project_id={pid}")
    assert usage.status_code == 200
    assert usage.json()["planner"]["input_tokens"] is None

    # Update policy without CSRF -> 403
    assert (
        v2_dash_client.put(
            "/api/v2/policies",
            headers={"Origin": "http://testserver"},
            json={"project_id": pid, "approval_mode": "REVIEW_REQUIRED"},
        ).status_code
        == 403
    )

    # Update policy with CSRF -> 200
    pol = v2_dash_client.put(
        "/api/v2/policies",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
        json={"project_id": pid, "approval_mode": "REVIEW_REQUIRED", "max_concurrency": 2},
    )
    assert pol.status_code == 200
    assert pol.json()["max_concurrency"] == 2

    # Read cohorts, calibration, catalog, quotas
    assert v2_dash_client.get(f"/api/v2/analytics/cohorts?project_id={pid}").status_code == 200
    assert v2_dash_client.get(f"/api/v2/analytics/calibration?project_id={pid}").status_code == 200
    assert v2_dash_client.get("/api/v2/catalog").status_code == 200
    assert v2_dash_client.get("/api/v2/quotas").status_code == 200
