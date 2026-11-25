from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.orchestrator import host_brain_agent as hba


def test_permission_profiles_cli_commands_keep_sandbox():
    # read_only
    cmd_ro_codex, stdin_ro = hba._build_cli_command("codex", "gpt-6.2-sol", "check", "read_only", effort="medium")
    assert "--sandbox" in cmd_ro_codex and "read-only" in cmd_ro_codex
    assert "sandbox_workspace_write.network_access=true" not in cmd_ro_codex
    assert stdin_ro == "check"

    cmd_ro_agy, _ = hba._build_cli_command("agy", "gemini-4-neon-medium", "check", "read_only")
    assert "--mode" in cmd_ro_agy and "plan" in cmd_ro_agy

    cmd_ro_claude, _ = hba._build_cli_command("claude", "claude-sonnet-5-5", "check", "read_only")
    assert "--tools" in cmd_ro_claude and "" in cmd_ro_claude

    # workspace_write
    cmd_ww_codex, _ = hba._build_cli_command("codex", "gpt-6.2-sol", "edit", "workspace_write")
    assert "--sandbox" in cmd_ww_codex and "workspace-write" in cmd_ww_codex
    assert "sandbox_workspace_write.network_access=true" not in cmd_ww_codex
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd_ww_codex

    cmd_ww_agy, _ = hba._build_cli_command("agy", "gemini-4-neon-medium", "edit", "workspace_write")
    assert "--mode" in cmd_ww_agy and "accept-edits" in cmd_ww_agy

    cmd_ww_claude, _ = hba._build_cli_command("claude", "claude-sonnet-5-5", "edit", "workspace_write")
    assert "--permission-mode" in cmd_ww_claude and "acceptEdits" in cmd_ww_claude

    # workspace_write_git
    cmd_git_codex, _ = hba._build_cli_command("codex", "gpt-6.2-sol", "git fetch", "workspace_write_git")
    assert "workspace-write" in cmd_git_codex
    assert "sandbox_workspace_write.network_access=true" in cmd_git_codex


def test_task_prompt_respects_permission_profile():
    p_ro = hba._build_task_prompt("Inspect Dining V2", "read_only", r"D:\workspace1\doi-cun-sieu-tri-tue")
    assert "Read-only analysis only" in p_ro

    p_ww = hba._build_task_prompt("Fix Dining V2", "workspace_write", r"D:\workspace1\doi-cun-sieu-tri-tue")
    assert "Read-only analysis only" not in p_ww
    assert "workspace_write" in p_ww
    assert "never merge/push to main" in p_ww

    p_git = hba._build_task_prompt("Fetch Dining V2", "workspace_write_git", r"D:\workspace1\doi-cun-sieu-tri-tue")
    assert "Read-only analysis only" not in p_git
    assert "workspace_write_git" in p_git
    assert "never force-push" in p_git


def test_workspace_allowlist_and_workspace_ops_guardrails(tmp_path: Path):
    allowed_dir = tmp_path / "allowed_repo"
    allowed_dir.mkdir()
    outside_dir = tmp_path / "outside_repo"
    outside_dir.mkdir()

    fake_resp = (200, {"items": [{"worktree_id": "wt-1", "canonical_root": str(allowed_dir)}]})
    with patch.object(hba, "_bridge_request", return_value=fake_resp):
        ok, resolved, err = hba._validate_allowed_workspace(
            {"BRIDGE_READ_TOKEN": "tok"},
            project_id="proj-1",
            worktree_id="wt-1",
            worktree_path=str(allowed_dir),
        )
        assert ok is True
        assert Path(resolved).resolve() == allowed_dir.resolve()
        assert err == ""

        # Reject outside directory
        ok2, _, err2 = hba._validate_allowed_workspace(
            {"BRIDGE_READ_TOKEN": "tok"},
            project_id="proj-1",
            worktree_id="wt-1",
            worktree_path=str(outside_dir),
        )
        assert ok2 is False
        assert "workspace_not_allowed" in err2

        # Reject path traversal
        ok3, _, err3 = hba._validate_allowed_workspace(
            {"BRIDGE_READ_TOKEN": "tok"},
            project_id="proj-1",
            worktree_id="wt-1",
            worktree_path=str(allowed_dir) + "/../outside_repo",
        )
        assert ok3 is False
        assert "path traversal" in err3

        # Reject disallowed workspace op (e.g. git_push / deploy)
        res_bad = hba.run_workspace_op(
            {"BRIDGE_READ_TOKEN": "tok"},
            project_id="proj-1",
            worktree_id="wt-1",
            action="git_push",
            worktree_path=str(allowed_dir),
        )
        assert res_bad["ok"] is False
        assert res_bad["status_code"] == 403
        assert res_bad["error_code"] == "action_not_allowed"

        # Redact credentials from command output
        with patch.object(
            hba,
            "_run_cmd",
            return_value=(0, "Bearer secret_token_1234567890 https://alice:mypass@github.com/org/repo ghp_1234567890abcdef1234", ""),
        ):
            res_ok = hba.run_workspace_op(
                {"BRIDGE_READ_TOKEN": "tok"},
                project_id="proj-1",
                worktree_id="wt-1",
                action="git_status",
                worktree_path=str(allowed_dir),
            )
            assert res_ok["ok"] is True
            assert "secret_token_1234567890" not in res_ok["stdout"]
            assert "mypass" not in res_ok["stdout"]
            assert "ghp_1234567890abcdef1234" not in res_ok["stdout"]
            assert "[REDACTED]" in res_ok["stdout"]
            assert "[REDACTED_TOKEN]" in res_ok["stdout"]


def test_worker_info_reports_hybrid_capabilities():
    info = hba.get_worker_info({})
    assert info["ok"] is True
    assert info["execution_architecture"] == "hybrid_docker_hub_plus_windows_worker"
    assert info["sandbox_enforced"] is True
    assert info["workspace_allowlist_enforced"] is True
    assert set(info["permission_profiles"].keys()) == {"read_only", "workspace_write", "workspace_write_git"}
    assert "git" in info["tools"] and "codex" in info["tools"] and "agy" in info["tools"]
