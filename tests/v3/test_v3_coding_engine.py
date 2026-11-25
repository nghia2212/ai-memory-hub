"""V3 M1 unit tests: 4-mode Coding Execution Engine (analysis, coding, testing, git_delivery) on isolated temp git repo."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from services.orchestrator.dispatch.execution_engine import (
    ExecutionPolicy,
    ExecutionPolicyError,
    create_isolated_worktree,
    enforce_post_execution_policy,
    execute_git_delivery,
    run_allowlisted_test,
    validate_test_command,
)


def _init_temp_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=str(root), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "qa@example.local"], cwd=str(root), check=True)
    subprocess.run(["git", "config", "user.name", "QA Bot"], cwd=str(root), check=True)
    (root / "src").mkdir()
    (root / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Demo\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=str(root), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(root), check=True, capture_output=True)


class TestCodingExecutionEngine(unittest.TestCase):
    def test_four_modes_and_policy_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_temp_repo(repo)

            # 1. Analysis mode: clean -> OK; mutation -> PolicyError
            self.assertEqual(enforce_post_execution_policy(repo, ExecutionPolicy(mode="analysis")), [])
            (repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
            with self.assertRaises(ExecutionPolicyError):
                enforce_post_execution_policy(repo, ExecutionPolicy(mode="analysis"))
            subprocess.run(["git", "checkout", "--", "src/app.py"], cwd=str(repo), check=True)

            # 2. Coding mode: forbidden on main; allowed in isolated feature worktree within owned_paths
            with self.assertRaises(ExecutionPolicyError):
                create_isolated_worktree(repo, Path(tmp) / "wt_main", "main")
            wt = create_isolated_worktree(repo, Path(tmp) / "wt_feat", "feature/test-m1")

            (wt / "src" / "app.py").write_text("x = 42\n", encoding="utf-8")
            changed = enforce_post_execution_policy(wt, ExecutionPolicy(mode="coding", owned_paths=("src/",)))
            self.assertEqual(changed, ["src/app.py"])

            # Mutation outside owned_paths -> blocked
            (wt / "README.md").write_text("hacked\n", encoding="utf-8")
            with self.assertRaises(ExecutionPolicyError):
                enforce_post_execution_policy(wt, ExecutionPolicy(mode="coding", owned_paths=("src/",)))
            subprocess.run(["git", "checkout", "--", "README.md"], cwd=str(wt), check=True)

            # 3. Testing mode: allowlisted passes; arbitrary shell rejected
            ev = run_allowlisted_test(wt, ["python", "-m", "py_compile", "src/app.py"])
            self.assertTrue(ev.passed)
            self.assertTrue(ev.test_evidence_ref.startswith("qa:python:0:"))
            with self.assertRaises(ExecutionPolicyError):
                validate_test_command(["rm", "-rf", "/"])
            with self.assertRaises(ExecutionPolicyError):
                validate_test_command(["pytest", "tests; whoami"])

            # 4. Git delivery: blocked without tests_passed; succeeds with tests_passed on feature branch
            pol_git = ExecutionPolicy(mode="git_delivery", owned_paths=("src/",), allow_git_push=False, require_tests_passed=True)
            with self.assertRaises(ExecutionPolicyError):
                execute_git_delivery(wt, pol_git, commit_message="feat: update app", tests_passed=False)
            res = execute_git_delivery(wt, pol_git, commit_message="feat: update app", tests_passed=True)
            self.assertEqual(res.branch, "feature/test-m1")
            self.assertIsNotNone(res.commit_sha)
            self.assertFalse(res.pushed)


if __name__ == "__main__":
    unittest.main()
