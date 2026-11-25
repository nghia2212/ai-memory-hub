"""V3 M1 / V3.1 P0.3 — Controlled Coding Execution Engine with pre- and post-execution security enforcement.

Modes:
- analysis: strictly read-only; verifies git working tree remains clean of new or modified edits.
- coding: dedicated worktree isolation; enforces owned_paths boundary, symlink/traversal checks, pre-dirty file hash checks; forbids main/master.
- testing: allowlisted test/build commands only (shell=False, forbidden eval flags, sanitized env without secrets).
- git_delivery: scoped commit/push to feature branch only (forbids main/master, --force, auto-merge).
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

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
    ("mvn", "test"),
    ("mvn", "test-compile"),
    ("mvn", "compile"),
    ("mvn", "-q", "test"),
)

FORBIDDEN_TEST_FLAGS = frozenset({
    "-c",
    "-e",
    "--eval",
    "--exec",
    "--print",
    "-p",
    "--import",
})

SENSITIVE_ENV_FRAGMENTS = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "PRIVATE_KEY",
    "CREDENTIAL",
    "AUTH",
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
    approved_root: str | None = None


@dataclass
class WorktreeSnapshot:
    changed_files: set[str]
    file_hashes: dict[str, str]


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
    s = path_str.replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s


def _file_sha256(path: Path) -> str:
    if not path.is_file():
        return "<missing>"
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


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


def validate_safe_subpath(worktree_path: str | Path, rel_path: str) -> Path:
    """Reject path traversal, absolute paths, and symlinks escaping the worktree boundary."""
    if not rel_path or rel_path.startswith(("/", "\\")) or ":" in rel_path.split("/")[0]:
        raise ExecutionPolicyError(f"Absolute or drive-qualified path is forbidden: {rel_path}")
    norm = _norm_rel(rel_path)
    if ".." in Path(norm).parts:
        raise ExecutionPolicyError(f"Path traversal ('..') is forbidden: {rel_path}")
    root = Path(worktree_path).resolve()
    candidate = root / norm
    if candidate.is_symlink():
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise ExecutionPolicyError(f"Symlink escapes worktree boundary: {rel_path} -> {resolved}") from exc
    resolved_cand = candidate.resolve()
    try:
        resolved_cand.relative_to(root)
    except ValueError as exc:
        raise ExecutionPolicyError(f"Resolved path escapes worktree boundary: {rel_path}") from exc
    return resolved_cand


def validate_pre_execution_policy(worktree_path: str | Path, policy: ExecutionPolicy) -> WorktreeSnapshot:
    """Pre-execution security gate: validate root, branch, owned_paths, and snapshot pre-existing dirty files."""
    wt = Path(worktree_path)
    if not wt.exists() or not wt.is_dir():
        raise ExecutionPolicyError(f"Worktree path does not exist or is not a directory: {worktree_path}")
    wt_resolved = wt.resolve()

    if policy.approved_root:
        approved = Path(policy.approved_root).resolve()
        try:
            wt_resolved.relative_to(approved)
        except ValueError as exc:
            raise ExecutionPolicyError(f"Worktree '{wt_resolved}' is outside approved root '{approved}'.") from exc

    rc_top, top_out, err_top = _run_git(wt_resolved, ["rev-parse", "--show-toplevel"])
    if rc_top != 0 or not top_out:
        raise ExecutionPolicyError(f"Worktree is not a valid git repository: {err_top}")

    for owned in policy.owned_paths:
        validate_safe_subpath(wt_resolved, owned)

    if policy.mode in ("coding", "git_delivery"):
        branch = get_current_branch(wt_resolved)
        if branch in PROTECTED_BRANCHES:
            raise ExecutionPolicyError(f"Mode '{policy.mode}' is forbidden on protected branch '{branch}'.")
        if policy.mode == "coding" and not policy.owned_paths:
            raise ExecutionPolicyError("Coding mode requires explicit non-empty owned_paths.")

    return snapshot_worktree_state(wt_resolved)


def snapshot_worktree_state(worktree_path: str | Path) -> WorktreeSnapshot:
    """Capture both filenames and content hashes of currently modified files so edits to pre-dirty files are detected."""
    wt = Path(worktree_path).resolve()
    changed = set(get_changed_files(wt))
    hashes = {f: _file_sha256(wt / f) for f in changed}
    return WorktreeSnapshot(changed_files=changed, file_hashes=hashes)


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
    if ".." in Path(norm).parts or norm.startswith("/"):
        return False
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
    baseline_snapshot: WorktreeSnapshot | None = None,
) -> list[str]:
    """Verify file mutations conform strictly to execution mode, owned_paths, symlink safety, and pre-dirty hash integrity."""
    wt = Path(worktree_path).resolve()
    after_list = get_changed_files(wt)
    after = set(after_list)

    if baseline_snapshot is not None:
        before = baseline_snapshot.changed_files
        newly_changed = set(after - before)
        # Also detect mutations to files that were ALREADY dirty before execution!
        for pre_file in before & after:
            if _file_sha256(wt / pre_file) != baseline_snapshot.file_hashes.get(pre_file):
                newly_changed.add(pre_file)
        mutated = sorted(newly_changed)
    else:
        before = baseline_changed or set()
        mutated = sorted(after - before)

    # Check symlink / path traversal safety for every mutated file
    for f in mutated:
        validate_safe_subpath(wt, f)

    if policy.mode == "analysis":
        if mutated:
            raise ExecutionPolicyError(
                f"Analysis mode is strictly read-only, but files were modified: {mutated}"
            )
        return []

    if policy.mode == "coding":
        branch = get_current_branch(wt)
        if branch in PROTECTED_BRANCHES:
            raise ExecutionPolicyError(f"Coding mode is forbidden on protected branch '{branch}'.")
        if not policy.owned_paths:
            raise ExecutionPolicyError("Coding mode requires explicit non-empty owned_paths.")
        violations = [f for f in mutated if not is_path_within_owned(f, policy.owned_paths)]
        if violations:
            raise ExecutionPolicyError(
                f"Coding mode modified files outside approved owned_paths: {violations}"
            )
        return mutated

    return mutated


def create_isolated_worktree(
    repo_root: str | Path,
    target_dir: str | Path,
    branch_name: str,
    base_ref: str = "HEAD",
) -> Path:
    """Create a dedicated git worktree on a new feature branch (never on main/master)."""
    if branch_name in PROTECTED_BRANCHES or branch_name.startswith("-") or ".." in branch_name:
        raise ExecutionPolicyError(f"Invalid or protected branch name: {branch_name}")
    target = Path(target_dir).resolve()
    rc, _out, err = _run_git(repo_root, ["worktree", "add", "-b", branch_name, str(target), base_ref])
    if rc != 0:
        raise ExecutionPolicyError(f"Failed to create isolated worktree: {err}")
    return target


def validate_test_command(cmd: list[str]) -> None:
    """Allow only explicitly allowlisted non-shell test/build commands without code-eval flags."""
    if not cmd:
        raise ExecutionPolicyError("Test command cannot be empty.")
    lower_cmd = tuple(c.lower() for c in cmd)
    if any(any(ch in arg for ch in (";", "&&", "||", "|", "`", "$(", "\n", "\r", ">", "<")) for arg in cmd):
        raise ExecutionPolicyError("Shell metacharacters are forbidden in testing mode.")
    matched_prefix: tuple[str, ...] | None = None
    for prefix in ALLOWED_TEST_PREFIXES:
        if len(lower_cmd) >= len(prefix) and lower_cmd[: len(prefix)] == prefix:
            matched_prefix = prefix
            break
    if matched_prefix is None:
        raise ExecutionPolicyError(f"Command '{' '.join(cmd[:3])}' is not in the approved testing allowlist.")
    # Reject arbitrary code execution flags after the allowlisted prefix
    rest = lower_cmd[len(matched_prefix):]
    for arg in rest:
        if arg in FORBIDDEN_TEST_FLAGS or arg.startswith(("--eval=", "--exec=")):
            raise ExecutionPolicyError(f"Forbidden code-evaluation flag '{arg}' in testing mode.")


def _sanitized_subprocess_env() -> dict[str, str]:
    """Strip sensitive tokens/secrets from subprocess environment."""
    clean: dict[str, str] = {}
    for k, v in os.environ.items():
        ku = k.upper()
        if any(frag in ku for frag in SENSITIVE_ENV_FRAGMENTS):
            continue
        clean[k] = v
    return clean


def run_allowlisted_test(
    worktree_path: str | Path,
    cmd: list[str],
    *,
    timeout_sec: int = 120,
) -> TestRunEvidence:
    validate_test_command(cmd)
    wt = Path(worktree_path).resolve()
    if not wt.is_dir():
        raise ExecutionPolicyError(f"Invalid worktree directory for testing: {worktree_path}")
    proc = subprocess.run(
        cmd,
        cwd=str(wt),
        env=_sanitized_subprocess_env(),
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

    for f in changed:
        validate_safe_subpath(worktree_path, f)

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
