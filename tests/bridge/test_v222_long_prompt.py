"""Long prompt regression tests; synthetic data only."""
from test_v2_bridge import client, h, _setup_project

def test_long_prompt_roundtrip(client):
    pid,wid=_setup_project(client)
    objective="Yêu cầu triển khai V3.\n"+"Kiểm tra bằng chứng.\n"*1000+"END_SENTINEL"
    assert 4000<len(objective)<32000
    task=dict(task_key="A",title="Long prompt fixture",objective=objective,features=dict(task_type="implementation",complexity="L"),assigned_agent="agy",assigned_provider="agy",assigned_model_id="fixture")
    body=dict(project_id=pid,worktree_id=wid,objective=objective,tasks=[task])
    result=client.post("/v2/plans",headers=h("tok-planner"),json=body)
    assert result.status_code==201,result.text
    plan=result.json()
    assert plan["objective"]==objective
    assert plan["editable_tasks"][0]["objective"]==objective
    oversized=dict(body,objective="x"*32001)
    assert client.post("/v2/plans",headers=h("tok-planner"),json=oversized).status_code==422
