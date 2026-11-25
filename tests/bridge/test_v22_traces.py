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


def test_memory_operation_evidence(client, monkeypatch):
    pid, wid = _setup_project(client)
    scope = {"project_id":pid,"worktree_id":wid}
    parent = str(uuid4())
    event = dict(trace_id=parent,root_trace_id=parent,**scope,step_type="agent_step",
                 step_name="fixture",actor_type="agent",status="running",
                 started_at="2026-01-01T00:00:00Z",metadata_json={"source":"fixture"})
    assert client.post("/v2/traces",json=event,headers=h("tok-runner")).status_code==201
    headers = {**h("tok-read"),"X-Trace-Parent":parent}
    assert client.get("/v1/context",params=scope,headers=headers).status_code==200
    assert client.post("/v1/search/memory",json={**scope,"query":"private fixture"},headers=headers).status_code==200
    rows = client.get("/v2/traces",params={"project_id":pid},headers=h("tok-read")).json()["items"]
    for name in ("get_project_context","search_memory"):
        step = next(t for t in rows if t["step_name"]==name)
        assert step["parent_trace_id"]==parent
        assert step["used_memory"] is True and step["used_mcp"] is False
        assert step["used_llm"] is False
        assert "private fixture" not in str(step)
    monkeypatch.setenv("BRIDGE_TRACE_TOKEN","fixture-trace-only")
    adapter = dict(event,trace_id=str(uuid4()),step_type="mcp_tool_call",actor_type="mcp",actor_id="codebase-adapter",step_name="trace_path",used_mcp=True)
    adapter["root_trace_id"]=adapter["trace_id"]
    response = client.post("/v2/traces",json=adapter,headers={**h("fixture-trace-only"),"X-Trace-Parent":parent})
    assert response.status_code==201, response.text
    assert response.json()["parent_trace_id"]==parent
    assert client.get("/v2/traces",params={"project_id":pid},headers=h("fixture-trace-only")).status_code==401
    assert client.post("/v2/traces",json=dict(adapter,input_tokens=100),headers=h("fixture-trace-only")).status_code==403


def test_session_usage_projection(client):
    pid,wid=_setup_project(client)
    sid=str(uuid4())
    scope={"project_id":pid,"worktree_id":wid,"session_id":sid}
    r=client.post("/v2/sessions",json={**scope,"role":"manual_cli","provider":"codex","model_id":"unknown"},headers=h("tok-runner"))
    assert r.status_code in (200,201),r.text
    for seq,mode,count in [(1,"delta",10),(2,"cumulative",25),(3,"delta",5)]:
        payload={**scope,"source_event_id":f"usage-{seq}","sequence_no":seq,
                 "usage":{"input_tokens":count,"measurement_kind":"measured","counter_mode":mode,"source":"fixture"}}
        for _ in range(2):
            r=client.post("/v2/usage",json=payload,headers=h("tok-runner"))
            assert r.status_code in (200,201),r.text
    rows=client.get("/v2/traces",params={"project_id":pid,"session_id":sid},headers=h("tok-read")).json()["items"]
    usage=[r for r in rows if r["step_type"]=="session_usage"]
    assert len(usage)==1
    assert usage[0]["input_tokens"]==30
    assert usage[0]["output_tokens"] is None
    assert usage[0]["cost_usd"] is None
    assert usage[0]["used_llm"] is None
    assert usage[0]["duration_ms"] is None
    assert not any(r["step_type"]=="llm_call" for r in rows)
