from unittest.mock import patch
from services.orchestrator import host_brain_agent as host

def test_disabled_by_default():
    assert host.hermes_agy_analyze({}, {"prompt":"hello"})["error_code"] == "disabled"

def test_caller_cannot_choose_scope():
    env = {"HERMES_AGY_ENABLED":"true"}
    assert host.hermes_agy_analyze(env, {"prompt":"hello","project_id":"x"})["error_code"] == "invalid_operator_scope"

def test_invalid_prompt_rejected():
    assert host.hermes_agy_analyze({"HERMES_AGY_ENABLED":"true"}, {"prompt":" "})["error_code"] == "invalid_prompt"

def test_unregistered_worktree_rejected():
    env = {"HERMES_AGY_ENABLED":"true","HERMES_AGY_PROJECT_ID":"00000000-0000-0000-0000-000000000001","HERMES_AGY_WORKTREE_ID":"00000000-0000-0000-0000-000000000002","HERMES_AGY_MODEL":"test"}
    with patch.object(host,"_validate_allowed_workspace",return_value=(False,"","denied")):
        assert host.hermes_agy_analyze(env,{"prompt":"hello"})["error_code"] == "workspace_not_allowed"
