"""Regression cases for lease recovery; synthetic DB, no provider calls."""
from __future__ import annotations

import sqlite3
from fastapi.testclient import TestClient
from test_v2_bridge import make_v2_settings, h, _setup_project
from app.main import create_app


def _claim(c):
    pid, wid = _setup_project(c)
    plan = c.post("/v2/plans", headers=h("tok-planner"), json={
        "project_id": pid, "worktree_id": wid, "objective": "Synthetic read-only task",
        "tasks": [{"task_key": "A", "title": "Read", "objective": "Read",
                   "dependencies": [], "features": {"task_type": "testing", "complexity": "XS"},
                   "assigned_agent": "agy", "assigned_provider": "agy",
                   "assigned_model_id": "claude-sonnet-5-5-medium"}],
    })
    assert plan.status_code == 201, plan.text
    plan = plan.json()
    rev = plan["revisions"][0]
    assert c.post(f"/v2/plans/{plan['id']}/approve?project_id={pid}", headers=h("tok-admin"),
                  json={"revision_id": rev["id"], "revision_hash": rev["revision_hash"], "actor": "admin"}).status_code == 200
    job = c.post("/v2/jobs/claim", headers=h("tok-runner"),
                 json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-1"}).json()
    assert job["claimed"]
    return pid, wid, plan["id"], job


def _heartbeat(c, pid, job, runner="runner-1"):
    return c.post(f"/v2/jobs/{job['job_id']}/heartbeat", headers=h("tok-runner"),
                  json={"project_id": pid, "lease_id": job["lease_id"], "runner_id": runner})


def test_valid_lease_survives_bridge_restart(tmp_path):
    settings = make_v2_settings(tmp_path)
    with TestClient(create_app(settings)) as c:
        pid, wid, plan_id, job = _claim(c)
        assert _heartbeat(c, pid, job, "runner-other").status_code == 409
    with TestClient(create_app(settings)) as c:
        assert _heartbeat(c, pid, job).status_code == 200
        assert c.get(f"/v2/plans/{plan_id}?project_id={pid}", headers=h("tok-read")).json()["status"] == "RUNNING"
        assert not c.post("/v2/jobs/claim", headers=h("tok-runner"),
                          json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-2"}).json()["claimed"]


def test_expired_lease_is_blocked_not_replayed(tmp_path):
    settings = make_v2_settings(tmp_path)
    with TestClient(create_app(settings)) as c:
        pid, wid, plan_id, job = _claim(c)
        # Only synthetic fixture manipulation, never the real user's DB.
        with sqlite3.connect(settings.db_path) as db:
            db.execute("UPDATE v2_job_leases SET expires_at = '2000-01-01T00:00:00+00:00' WHERE lease_id = ?", (job["lease_id"],))
        assert _heartbeat(c, pid, job).status_code == 409
        assert c.post(f"/v2/jobs/{job['job_id']}/complete", headers=h("tok-runner"),
                      json={"project_id": pid, "lease_id": job["lease_id"], "outcome": "succeeded"}).status_code == 409
        plan = c.get(f"/v2/plans/{plan_id}?project_id={pid}", headers=h("tok-read")).json()
        assert plan["status"] == "PAUSED"
        assert plan["tasks"][0]["state"] == "blocked"
        assert not c.post("/v2/jobs/claim", headers=h("tok-runner"),
                          json={"project_id": pid, "worktree_id": wid, "runner_id": "runner-2"}).json()["claimed"]
        session = c.get(f"/v2/sessions/{job['session_id']}?project_id={pid}", headers=h("tok-read")).json()
        assert session["status"] == "partial"
    with TestClient(create_app(settings)) as c:
        assert c.get(f"/v2/plans/{plan_id}?project_id={pid}", headers=h("tok-read")).json()["tasks"][0]["state"] == "blocked"
