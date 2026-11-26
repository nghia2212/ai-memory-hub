from unittest.mock import patch
from services.orchestrator import host_brain_agent as host

def test_disabled_by_default():
    assert host.hermes_agy_analyze({}, {"prompt":"hello"})["error_code"] == "disabled"

def test_caller_cannot_choose_scope():
    env = {"HERMES_AGY_ENABLED":"true"}
    assert host.hermes_agy_analyze(env, {"prompt":"hello","project_id":"x"})["error_code"] == "invalid_request"

def test_invalid_prompt_rejected():
    assert host.hermes_agy_analyze({"HERMES_AGY_ENABLED":"true"}, {"prompt":" "})["error_code"] == "invalid_prompt"

def test_unregistered_worktree_rejected():
    env = {"HERMES_AGY_ENABLED":"true","HERMES_AGY_PROJECT_ID":"00000000-0000-0000-0000-000000000001","HERMES_AGY_WORKTREE_ID":"00000000-0000-0000-0000-000000000002","HERMES_AGY_MODEL":"test"}
    with patch.object(host,"_validate_allowed_workspace",return_value=(False,"","denied")):
        assert host.hermes_agy_analyze(env,{"prompt":"hello"})["error_code"] == "workspace_not_allowed"

def test_configured_model_and_scope_never_taken_from_request():
    env = {"HERMES_AGY_ENABLED":"true","HERMES_AGY_PROJECT_ID":"00000000-0000-0000-0000-000000000001","HERMES_AGY_WORKTREE_ID":"00000000-0000-0000-0000-000000000002","HERMES_AGY_MODEL":"test"}
    with patch.object(host,"_validate_allowed_workspace",return_value=(True,"/scratch","")) as validate, patch.object(host.shutil,"which",return_value="/scratch/agy.exe"), patch.object(host,"_run_cmd",side_effect=[(0,"--print --mode --sandbox --model --print-timeout",""),(0,"different-model","")]) as run:
        result = host.hermes_agy_analyze(env,{"prompt":"hello"})
        assert result["error_code"] == "model_not_discovered"
        validate.assert_called_once_with(env,project_id=env["HERMES_AGY_PROJECT_ID"],worktree_id=env["HERMES_AGY_WORKTREE_ID"],worktree_path=None)
        assert run.call_count == 2

def test_missing_capability_fails_closed():
    env = {"HERMES_AGY_ENABLED":"true","HERMES_AGY_PROJECT_ID":"00000000-0000-0000-0000-000000000001","HERMES_AGY_WORKTREE_ID":"00000000-0000-0000-0000-000000000002","HERMES_AGY_MODEL":"test"}
    with patch.object(host,"_validate_allowed_workspace",return_value=(True,"/scratch","")), patch.object(host.shutil,"which",return_value="/scratch/agy.exe"), patch.object(host,"_run_cmd",return_value=(0,"--print","")) as run:
        assert host.hermes_agy_analyze(env,{"prompt":"hello"})["error_code"] == "unsupported_cli_flags"
        assert run.call_count == 1

def test_quota_unknown_and_auth_unverified_without_model_call():
    with patch.object(host,"_run_cmd",return_value=(0,"agy test","")) as run:
        state = host.hermes_agy_status({})
        assert state["installed"] and state["quota"] is None and not state["auth_verified"]
        run.assert_called_once_with(["agy","--version"],timeout=10)
