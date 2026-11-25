"""Regression: exact model quota scope, manual recovery, claim guard."""
from datetime import datetime, timezone, timedelta
from test_v2_bridge import client, h, _setup_project

def test_quota_routing_and_claim(client):
    pid,wid=_setup_project(client)
    models=[dict(provider="agy",model_id=m,auth_mode="subscription_cli",status="verified") for m in ("claude-fixture","gemini-fixture")]
    assert client.post("/v2/catalog/refresh",headers=h("tok-admin"),json={"models":models}).status_code==201
    def quota(provider,model,used,reset=None):
        return client.post("/v2/quotas",headers=h("tok-admin"),json=dict(provider=provider,auth_mode="subscription_cli",account_bucket="model:"+model,quota_kind="subscription_quota",unit="availability_flag",numerator=used,denominator=1,source="fixture",observed_at=datetime.now(timezone.utc).isoformat(),reset_at=reset))
    def recommend():
        return client.post("/v2/routing/recommend",headers=h("tok-planner"),json=dict(project_id=pid,task_type="implementation",complexity="M"))
    assert quota("agy","claude-fixture",1).status_code==201
    assert recommend().json()["model_id"]=="gemini-fixture"
    assert quota("agy","gemini-fixture",1).status_code==201
    assert recommend().status_code==409
    assert quota("agy","gemini-fixture",0).status_code==201
    assert recommend().json()["model_id"]=="gemini-fixture"
    task=dict(task_key="A",title="Fixture",objective="Fixture",features=dict(task_type="implementation",complexity="M"),assigned_agent="agy",assigned_provider="agy",assigned_model_id="gemini-fixture")
    p=client.post("/v2/plans",headers=h("tok-planner"),json=dict(project_id=pid,worktree_id=wid,objective="Fixture",tasks=[task]))
    assert p.status_code==201,p.text
    plan=p.json()
    approval=client.post("/v2/plans/"+plan["id"]+"/approve",params={"project_id":pid},headers=h("tok-admin"),json=dict(revision_id=plan["current_revision_id"],revision_hash=plan["revisions"][0]["revision_hash"],actor="admin"))
    assert approval.status_code==200,approval.text
    assert quota("agy","gemini-fixture",1).status_code==201
    claim=dict(project_id=pid,worktree_id=wid,runner_id="fixture",plan_id=plan["id"])
    r=client.post("/v2/jobs/claim",headers=h("tok-runner"),json=claim)
    assert r.json()=={"claimed":False,"reason":"quota_exhausted"}
    assert quota("agy","gemini-fixture",0).status_code==201
    r=client.post("/v2/jobs/claim",headers=h("tok-runner"),json=claim)
    assert r.json()["claimed"] is True,r.text
    j=r.json()
    r=client.post("/v2/jobs/"+j["job_id"]+"/complete",headers=h("tok-runner"),json=dict(project_id=pid,lease_id=j["lease_id"],outcome="failed",error_code="quota_exceeded"))
    assert r.status_code==200,r.text
    assert recommend().status_code==409

def test_quota_scope_and_reset():
    from app.quota_guard import quota_status
    now=datetime.now(timezone.utc)
    row=dict(provider="claude",auth_mode="subscription_cli",account_bucket="model:opus",quota_kind="subscription_quota",numerator=1,denominator=1,source="fixture",observed_at=now.isoformat())
    assert quota_status([row],"agy","opus","subscription_cli",now)["state"]=="unknown"
    assert quota_status([row],"claude","sonnet","subscription_cli",now)["state"]=="unknown"
    assert quota_status([row],"claude","opus","subscription_cli",now)["state"]=="exhausted"
    row["reset_at"]=(now-timedelta(seconds=1)).isoformat()
    assert quota_status([row],"claude","opus","subscription_cli",now)["state"]=="unknown"
