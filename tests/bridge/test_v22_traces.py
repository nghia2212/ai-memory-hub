"""Synthetic fixtures only; verify trace ingestion, isolation and lifecycle."""
from uuid import uuid4
from test_v2_bridge import client, h, _setup_project

def test_trace_lifecycle(client):
    pid, wid = _setup_project(client)
    tid = str(uuid4())
    event = dict(trace_id=tid, root_trace_id=tid, project_id=pid, worktree_id=wid,
                 step_type="local_rule", step_name="fixture", actor_type="planner",
                 status="running", started_at="2026-01-01T07:00:00+07:00",
                 used_llm=False, metadata_json={"source":"synthetic test"})
    url="/v2/traces"
    assert client.post(url,json=event,headers=h("tok-read")).status_code==403
    r=client.post(url,json=event,headers=h("tok-runner"))
    assert r.status_code==201, r.text
    assert r.json()["started_at"].startswith("2026-01-01T00:00:00")
    assert r.json()["input_tokens"] is None
    assert r.json()["used_llm"] is False
    assert client.post(url,json=event,headers=h("tok-runner")).status_code==201
    done=dict(event,status="succeeded",ended_at="2026-01-01T07:00:01+07:00")
    r=client.post(url,json=done,headers=h("tok-runner"))
    assert r.status_code==201, r.text
    assert r.json()["duration_ms"]==1000
    assert client.post(url,json=event,headers=h("tok-runner")).status_code==409
    child=dict(done,trace_id=str(uuid4()),parent_trace_id=tid,step_type="ai_memory")
    assert client.post(url,json=child,headers=h("tok-write")).status_code==201
    result=client.get(url,params={"project_id":pid,"root_trace_id":tid},headers=h("tok-read"))
    assert result.json()["total"]==2
    other, other_wt = _setup_project(client,"other","git:other")
    assert client.get(url+"/"+tid,params={"project_id":other},headers=h("tok-read")).status_code==404
    assert client.post(url,json=dict(child,trace_id=str(uuid4()),project_id=other,worktree_id=other_wt),headers=h("tok-write")).status_code==422
    assert client.post(url,json=dict(event,input_tokens=-1),headers=h("tok-write")).status_code==422
