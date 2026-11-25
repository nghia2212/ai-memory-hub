"""V3 M1 — Controlled Coding Execution Engine with 4 policy-enforced modes.

Modes:
- analysis: strictly read-only; verifies git working tree remains clean of new edits.
- coding: dedicated worktree isolation; enforces owned_paths boundary; forbids main/master.
- testing: allowlisted test/build commands only (shell=False, no arbitrary LLM shell).
- git_delivery: scoped commit/push to feature branch only (forbids main/master, --force, auto-merge).
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

ExecutionMode = Literal["analysis", "coding", "testing", "git_delivery"]

PROTECTED_BRANCHES = frozenset({"main", "master", "HEAD"})

ALLOWED_TEST_PREFIXES: tuple[tuple[str, ...], ...] = (
    ("python", "-m", "py_compile"),
    ("python", "-m", "pytest"),
    ("pytest",),
    ("node", "--check"),
    ("npm", "test"),
    ("npm", "run", "build"),
    ("npm", "run", "test"),
)


class ExecutionPolicyError(PermissionError):
    """Raised when an execution step violates its approved mode or path/branch policy."""


@dataclass(frozen=True)
class ExecutionPolicy:
    mode: ExecutionMode = "analysis"
    owned_paths: tuple[str, ...] = ()
    allow_git_push: bool = False
    require_tests_passed: bool = True
    max_timeout_sec: int = 180


@dataclass
class TestRunEvidence:
    command: list[str]
    exit_code: int
    passed: bool
    stdout_excerpt: str
    stderr_excerpt: str
    test_evidence_ref: str


@dataclass
class GitDeliveryResult:
    branch: str
    changed_files: list[str]
    commit_sha: str | None
    pushed: bool
    warnings: list[str] = field(default_factory=list)


def _norm_rel(path_str: str) -> str:
    return path_str.replace("\\", "/").lstrip("./")


def _run_git(cwd: str | Path, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=False,
        check=False,
    )
    return proc.returncode, (proc.stdout or "").rstrip("\r\n"), (proc.stderr or "").strip()


def get_changed_files(worktree_path: str | Path) -> list[str]:
    rc, out, err = _run_git(worktree_path, ["status", "--porcelain"])
    if rc != 0:
        raise ExecutionPolicyError(f"Unable to inspect git status: {err}")
    files: list[str] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1].strip()
        files.append(_norm_rel(raw.strip('"')))
    return sorted(set(files))


def get_current_branch(worktree_path: str | Path) -> str:
    rc, out, err = _run_git(worktree_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if rc != 0:
        raise ExecutionPolicyError(f"Unable to resolve git branch: {err}")
    return out.strip()


def is_path_within_owned(rel_path: str, owned_paths: tuple[str, ...]) -> bool:
    norm = _norm_rel(rel_path)
    for owned in owned_paths:
        o = _norm_rel(owned).rstrip("/")
        if not o:
            continue
        if norm == o or norm.startswith(o + "/"):
            return True
    return False


def enforce_post_execution_policy(
    worktree_path: str | Path,
    policy: ExecutionPolicy,
    *,
    baseline_changed: set[str] | None = None,
) -> list[str]:
    """Verify file mutations conform strictly to execution mode and owned_paths."""
    before = baseline_changed or set()
    after = set(get_changed_files(worktree_path))
    newly_changed = sorted(after - before)

    if policy.mode == "analysis":
        if newly_changed:
            raise ExecutionPolicyError(
                f"Analysis mode is strictly read-only, but files were modified: {newly_changed}"
            )
        return []

    if policy.mode == "coding":
        branch = get_current_branch(worktree_path)
        if branch in PROTECTED_BRANCHES:
            raise ExecutionPolicyError(f"Coding mode is forbidden on protected branch '{branch}'.")
        if not policy.owned_paths:
            raise ExecutionPolicyError("Coding mode requires explicit non-empty owned_paths.")
        violations = [f for f in newly_changed if not is_path_within_owned(f, policy.owned_paths)]
        if violations:
            raise ExecutionPolicyError(
                f"Coding mode modified files outside approved owned_paths: {violations}"
            )
        return newly_changed

    return newly_changed


def create_isolated_worktree(
    repo_root: str | Path,
    target_dir: str | Path,
    branch_name: str,
    base_ref: str = "HEAD",
) -> Path:
    """Create a dedicated git worktree on a new feature branch (never on main/master)."""
    if branch_name in PROTECTED_BRANCHES or branch_name.startswith("-"):
        raise ExecutionPolicyError(f"Invalid or protected branch name: {branch_name}")
    target = Path(target_dir).resolve()
    rc, _out, err = _run_git(repo_root, ["worktree", "add", "-b", branch_name, str(target), base_ref])
    if rc != 0:
        raise ExecutionPolicyError(f"Failed to create isolated worktree: {err}")
    return target


def validate_test_command(cmd: list[str]) -> None:
    """Allow only explicitly allowlisted non-shell test/build commands."""
    if not cmd:
        raise ExecutionPolicyError("Test command cannot be empty.")
    lower_cmd = tuple(c.lower() for c in cmd)
    if any(any(ch in arg for ch in (";", "&&", "||", "|", "`", "$(", "\n", "\r")) for arg in cmd):
        raise ExecutionPolicyError("Shell metacharacters are forbidden in testing mode.")
    for prefix in ALLOWED_TEST_PREFIXES:
        if len(lower_cmd) >= len(prefix) and lower_cmd[: len(prefix)] == prefix:
            return
    raise ExecutionPolicyError(f"Command '{' '.join(cmd[:3])}' is not in the approved testing allowlist.")


def run_allowlisted_test(
    worktree_path: str | Path,
    cmd: list[str],
    *,
    timeout_sec: int = 120,
) -> TestRunEvidence:
    validate_test_command(cmd)
    proc = subprocess.run(
        cmd,
        cwd=str(worktree_path),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=min(timeout_sec, 300),
        shell=False,
        check=False,
    )
    out_ex = (proc.stdout or "").strip()[-1500:]
    err_ex = (proc.stderr or "").strip()[-1500:]
    digest = hashlib.sha256(f"{cmd}:{proc.returncode}:{out_ex}".encode("utf-8")).hexdigest()[:16]
    return TestRunEvidence(
        command=list(cmd),
        exit_code=proc.returncode,
        passed=proc.returncode == 0,
        stdout_excerpt=out_ex,
        stderr_excerpt=err_ex,
        test_evidence_ref=f"qa:{cmd[0]}:{proc.returncode}:{digest}",
    )


def execute_git_delivery(
    worktree_path: str | Path,
    policy: ExecutionPolicy,
    *,
    commit_message: str,
    tests_passed: bool = False,
    remote: str = "origin",
) -> GitDeliveryResult:
    """Perform scoped commit and optional push on a feature branch; never touches main or uses --force."""
    if policy.mode != "git_delivery":
        raise ExecutionPolicyError(f"Git delivery requires mode='git_delivery', got '{policy.mode}'.")
    if policy.require_tests_passed and not tests_passed:
        raise ExecutionPolicyError("Git delivery blocked: required testing evidence has not passed.")
    branch = get_current_branch(worktree_path)
    if branch in PROTECTED_BRANCHES:
        raise ExecutionPolicyError(f"Git delivery is forbidden on protected branch '{branch}'.")
    if not commit_message.strip():
        raise ExecutionPolicyError("Commit message must be non-empty.")

    changed = get_changed_files(worktree_path)
    if not changed:
        return GitDeliveryResult(branch=branch, changed_files=[], commit_sha=None, pushed=False, warnings=["No changes to commit."])

    if policy.owned_paths:
        outside = [f for f in changed if not is_path_within_owned(f, policy.owned_paths)]
        if outside:
            raise ExecutionPolicyError(f"Git delivery aborted: unstaged/modified files outside owned_paths: {outside}")

    rc_add, _, err_add = _run_git(worktree_path, ["add", "--", *changed])
    if rc_add != 0:
        raise ExecutionPolicyError(f"git add failed: {err_add}")

    rc_cm, _, err_cm = _run_git(worktree_path, ["commit", "-m", commit_message.strip()])
    if rc_cm != 0:
        raise ExecutionPolicyError(f"git commit failed: {err_cm}")

    _, sha, _ = _run_git(worktree_path, ["rev-parse", "HEAD"])
    pushed = False
    if policy.allow_git_push:
        rc_p, _, err_p = _run_git(worktree_path, ["push", "-u", remote, branch], timeout=60)
        if rc_p != 0:
            raise ExecutionPolicyError(f"git push failed: {err_p}")
        pushed = True

    return GitDeliveryResult(branch=branch, changed_files=changed, commit_sha=sha, pushed=pushed)
