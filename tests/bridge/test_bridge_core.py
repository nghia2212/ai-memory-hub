"""Comprehensive unit & integration tests for Memory Bridge V1."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

BRIDGE_ROOT = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
if str(BRIDGE_ROOT) not in sys.path:
    sys.path.insert(0, str(BRIDGE_ROOT))

from app.config import Settings
from app.main import create_app


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "bridge.db",
        backup_dir=tmp_path / "backups",
        read_tokens={"tok-read"},
        write_tokens={"tok-write"},
        admin_tokens={"tok-admin"},
        worker_tokens={"tok-worker"},
    )


def h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    app = create_app(make_settings(tmp_path))
    with TestClient(app) as c:
        yield c


def _register_project_and_worktree(client: TestClient, name: str, identity: str, root: str, hermes: bool = False):
    p = client.post(
        "/v1/projects",
        headers=h("tok-admin"),
        json={"name": name, "canonical_identity": identity, "hermes_approved": hermes},
    )
    assert p.status_code == 201
    pid = p.json()["project_id"]
    w = client.post(
        f"/v1/projects/{pid}/worktrees",
        headers=h("tok-admin"),
        json={"canonical_root": root, "branch": "main", "head_commit": "abc1234"},
    )
    assert w.status_code == 201
    assert w.json()["mount_status"] == "pending_host_setup"
    return pid, w.json()["worktree_id"]


def test_health_and_rbac(client: TestClient):
    assert client.get("/livez").status_code == 200
    hz = client.get("/healthz")
    assert hz.status_code == 200
    assert hz.json()["ready"] is True

    # Unauthenticated
    assert client.get("/v1/projects").status_code == 401
    # Read token cannot create project
    r = client.post(
        "/v1/projects",
        headers=h("tok-read"),
        json={"name": "p1", "canonical_identity": "git:p1"},
    )
    assert r.status_code == 403
    # Worker token cannot read projects
    assert client.get("/v1/projects", headers=h("tok-worker")).status_code == 403


def test_strict_validation_and_unsafe_paths(client: TestClient):
    # Unexpected field -> 422
    r = client.post(
        "/v1/projects",
        headers=h("tok-admin"),
        json={"name": "p1", "canonical_identity": "git:p1", "unexpected": 1},
    )
    assert r.status_code == 422

    pid = client.post(
        "/v1/projects",
        headers=h("tok-admin"),
        json={"name": "p1", "canonical_identity": "git:p1"},
    ).json()["project_id"]

    # Path traversal -> 422
    bad_wt = client.post(
        f"/v1/projects/{pid}/worktrees",
        headers=h("tok-admin"),
        json={"canonical_root": "D:\\workspace\\..\\secret"},
    )
    assert bad_wt.status_code == 422

    # Relative path -> 422
    rel_wt = client.post(
        f"/v1/projects/{pid}/worktrees",
        headers=h("tok-admin"),
        json={"canonical_root": "relative/path"},
    )
    assert rel_wt.status_code == 422


def test_isolation_vietnamese_fts_candidate_and_injection(client: TestClient):
    p1, w1 = _register_project_and_worktree(client, "proj1", "git:p1", "D:\\repo\\p1-main")
    w2 = client.post(
        f"/v1/projects/{p1}/worktrees",
        headers=h("tok-admin"),
        json={"canonical_root": "D:\\repo\\p1-feat", "branch": "feat/x", "head_commit": "def5678"},
    ).json()["worktree_id"]
    p2, w3 = _register_project_and_worktree(client, "proj2", "git:p2", "D:\\repo\\p2-main")

    # Create candidate memory in p1/w1 with Vietnamese text
    m1 = client.post(
        "/v1/memories",
        headers=h("tok-write"),
        json={
            "project_id": p1,
            "worktree_id": w1,
            "type": "bugfix",
            "title": "Sửa lỗi kết nối cơ sở dữ liệu SQLite",
            "body": "Đã bật chế độ WAL và khóa ngoại để tránh lỗi khóa bảng khi ghi đồng thời.",
            "tags": ["sqlite", "tiếng-việt"],
            "source_agent": "claude",
            "source_commit": "abc1234",
            "evidence_ref": "commit:abc1234",
            "idempotency_key": "mem-1",
        },
    )
    assert m1.status_code == 201
    assert m1.json()["state"] == "candidate"
    mem_id = m1.json()["memory_id"]

    # Candidate hidden by default
    s_before = client.post(
        "/v1/search/memory",
        headers=h("tok-read"),
        json={"project_id": p1, "worktree_id": w1, "query": "lỗi kết nối cơ sở dữ liệu"},
    )
    assert s_before.status_code == 200
    assert s_before.json()["items"] == []

    # Verify memory
    v = client.post(f"/v1/memories/{mem_id}/verify", headers=h("tok-admin"), json={"action": "verify"})
    assert v.status_code == 200
    assert v.json()["state"] == "verified"

    # Search in p1/w1 finds Vietnamese record
    s_after = client.post(
        "/v1/search/memory",
        headers=h("tok-read"),
        json={"project_id": p1, "worktree_id": w1, "query": "lỗi kết nối cơ sở dữ liệu"},
    )
    assert s_after.status_code == 200
    assert len(s_after.json()["items"]) == 1
    assert s_after.json()["items"][0]["id"] == mem_id

    # Cross-worktree isolation: p1/w2 must NOT see p1/w1 memory
    s_w2 = client.post(
        "/v1/search/memory",
        headers=h("tok-read"),
        json={"project_id": p1, "worktree_id": w2, "query": "lỗi kết nối"},
    )
    assert s_w2.status_code == 200
    assert s_w2.json()["items"] == []

    # Cross-project isolation: p2/w3 must NOT see p1/w1 memory
    s_p2 = client.post(
        "/v1/search/memory",
        headers=h("tok-read"),
        json={"project_id": p2, "worktree_id": w3, "query": "SQLite"},
    )
    assert s_p2.status_code == 200
    assert s_p2.json()["items"] == []

    # SQL / FTS5 injection attempts must not error or leak
    for evil in ['" OR 1=1 --', "title:* OR NOT", "NEAR(a, b)", "'; DROP TABLE memories; --"]:
        res = client.post(
            "/v1/search/memory",
            headers=h("tok-read"),
            json={"project_id": p1, "worktree_id": w1, "query": evil},
        )
        assert res.status_code == 200
        assert res.json()["items"] == []


def test_serialized_budget_strictly_bounded(client: TestClient):
    p1, w1 = _register_project_and_worktree(client, "proj1", "git:p1", "D:\\repo\\p1")
    for i in range(4):
        m = client.post(
            "/v1/memories",
            headers=h("tok-write"),
            json={
                "project_id": p1,
                "worktree_id": w1,
                "type": "note",
                "title": f"Ghi chú dài số {i}",
                "body": "Nội dung thử nghiệm ngân sách ký tự " + ("x" * 600),
                "source_agent": "codex",
                "idempotency_key": f"budget-{i}",
            },
        )
        mid = m.json()["memory_id"]
        client.post(f"/v1/memories/{mid}/verify", headers=h("tok-admin"), json={"action": "verify"})

    res = client.post(
        "/v1/search/memory",
        headers=h("tok-read"),
        json={"project_id": p1, "worktree_id": w1, "query": "ngân sách", "limit": 10, "max_chars": 500},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["truncated"] is True
    assert len(json.dumps(data, ensure_ascii=False)) <= 500


def test_idempotency_and_worker_redaction_and_metrics(client: TestClient):
    p1, w1 = _register_project_and_worktree(client, "proj1", "git:p1", "D:\\repo\\p1", hermes=False)

    # Duplicate event idempotency
    ev_payload = {
        "project_id": p1,
        "worktree_id": w1,
        "task_id": "AIH-04",
        "kind": "error",
        "payload": {"msg": "failed", "api_key": "sk-secret1234567890123456"},
        "idempotency_key": "ev-1",
        "approved": True,
    }
    r1 = client.post("/v1/events", headers=h("tok-write"), json=ev_payload)
    assert r1.status_code == 201
    r2 = client.post("/v1/events", headers=h("tok-write"), json=ev_payload)
    assert r2.status_code == 201
    assert r1.json()["event_id"] == r2.json()["event_id"]

    # Mismatched payload with same idempotency_key -> 409
    bad_replay = dict(ev_payload, kind="different")
    r3 = client.post("/v1/events", headers=h("tok-write"), json=bad_replay)
    assert r3.status_code == 409

    # Worker denied when hermes_approved is False
    w_denied = client.get(f"/v1/worker/events?project_id={p1}", headers=h("tok-worker"))
    assert w_denied.status_code == 403

    # Register approved project
    p2, w2 = _register_project_and_worktree(client, "proj2", "git:p2", "D:\\repo\\p2", hermes=True)
    client.post(
        "/v1/events",
        headers=h("tok-write"),
        json={
            "project_id": p2,
            "worktree_id": w2,
            "task_id": "AIH-07",
            "kind": "fix",
            "payload": {"detail": "Bearer secret_token_1234567890"},
            "idempotency_key": "ev-p2-1",
            "approved": True,
        },
    )
    wev = client.get(f"/v1/worker/events?project_id={p2}", headers=h("tok-worker"))
    assert wev.status_code == 200
    assert len(wev.json()["items"]) == 1
    assert "secret_token_1234567890" not in json.dumps(wev.json())

    # Metrics before any token measurements
    m_before = client.get("/v1/metrics", headers=h("tok-read")).json()
    assert m_before["measured_tokens_available"] is False
    assert m_before["token_usage"] is None

    # Worker submits result with real measured tokens
    wr = client.post(
        "/v1/worker/results",
        headers=h("tok-worker"),
        json={
            "project_id": p2,
            "worktree_id": w2,
            "event_id": wev.json()["items"][0]["event_id"],
            "type": "experience",
            "title": "Kinh nghiệm xử lý token",
            "body": "Tóm tắt đã duyệt",
            "idempotency_key": "wr-1",
            "input_tokens": 120,
            "output_tokens": 45,
            "cost_usd": 0.0012,
        },
    )
    assert wr.status_code == 201
    m_after = client.get("/v1/metrics", headers=h("tok-read")).json()
    assert m_after["measured_tokens_available"] is True
    assert m_after["token_usage"]["input_tokens"] == 120


def test_backup_restore_rollback_and_restart_persistence(tmp_path: Path):
    settings = make_settings(tmp_path)
    app1 = create_app(settings)
    with TestClient(app1) as c1:
        p1, w1 = _register_project_and_worktree(c1, "persist-proj", "git:persist", "D:\\repo\\persist")
        b = c1.post("/v1/backup", headers=h("tok-admin"))
        assert b.status_code == 201
        backup_id = b.json()["backup_id"]

        # Create extra project after backup
        c1.post(
            "/v1/projects",
            headers=h("tok-admin"),
            json={"name": "after-backup", "canonical_identity": "git:after"},
        )
        assert len(c1.get("/v1/projects", headers=h("tok-read")).json()["items"]) == 2

        # Missing backup ID -> 404
        bad_id = "00000000-0000-0000-0000-000000000000"
        assert c1.post("/v1/restore", headers=h("tok-admin"), json={"backup_id": bad_id}).status_code == 404

        # Corrupted backup file -> 422 and active DB untouched
        corrupt_b = c1.post("/v1/backup", headers=h("tok-admin")).json()["backup_id"]
        (settings.backup_dir / f"{corrupt_b}.db").write_bytes(b"not a valid sqlite database")
        assert c1.post("/v1/restore", headers=h("tok-admin"), json={"backup_id": corrupt_b}).status_code == 422
        assert len(c1.get("/v1/projects", headers=h("tok-read")).json()["items"]) == 2

        # Valid restore rolls back DB to 1 project
        res = c1.post("/v1/restore", headers=h("tok-admin"), json={"backup_id": backup_id})
        assert res.status_code == 200
        assert len(c1.get("/v1/projects", headers=h("tok-read")).json()["items"]) == 1

    # Simulate service restart with fresh app instance on same DB path
    app2 = create_app(settings)
    with TestClient(app2) as c2:
        items = c2.get("/v1/projects", headers=h("tok-read")).json()["items"]
        assert len(items) == 1
        assert items[0]["project_id"] == p1


def test_v11_multi_agent_events_and_agents_status(client: TestClient):
    p1, w1 = _register_project_and_worktree(client, "v11-proj", "git:v11", "D:\\repo\\v11")
    p2, w2 = _register_project_and_worktree(client, "v11-other", "git:v11-other", "D:\\repo\\v11-other")

    # Agent C V1.1 payload shape (includes source_agent & timestamp, optional task_id)
    for idx, (agent, kind) in enumerate([("claude", "session_start"), ("claude", "session_end"), ("codex", "handoff_created")]):
        res = client.post(
            "/v1/events",
            headers=h("tok-write"),
            json={
                "project_id": p1,
                "worktree_id": w1,
                "source_agent": agent,
                "kind": kind,
                "timestamp": 1764038400 + idx,
                "payload": {"note": f"{agent}-{kind}"},
                "idempotency_key": f"v11-ev-{idx}",
            },
        )
        assert res.status_code == 201
        assert res.json()["source_agent"] == agent

    st = client.get(f"/v1/agents/status?project_id={p1}&worktree_id={w1}", headers=h("tok-read"))
    assert st.status_code == 200
    items = {item["source_agent"]: item for item in st.json()["items"]}
    assert set(items.keys()) == {"claude", "codex"}
    assert items["claude"]["last_kind"] == "session_end"
    assert items["claude"]["branch"] == "main"
    assert items["claude"]["head_commit"] == "abc1234"

    # Cross-project isolation on /v1/agents/status
    st_p2 = client.get(f"/v1/agents/status?project_id={p2}&worktree_id={w2}", headers=h("tok-read"))
    assert st_p2.status_code == 200
    assert st_p2.json()["items"] == []
