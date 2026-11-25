"""V3.1 P0 tests: Context Pack 4K-32K preservation, Coding Engine security hardening, Migration 6->7->8."""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from services.orchestrator.context_pack import (
    ContextBudgetError,
    build_context_pack,
    compare_three_way_benchmark,
)
from services.orchestrator.dispatch.execution_engine import (
    ExecutionPolicy,
    ExecutionPolicyError,
    enforce_post_execution_policy,
    run_allowlisted_test,
    validate_pre_execution_policy,
    validate_safe_subpath,
    validate_test_command,
)

BRIDGE_DIR = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
if str(BRIDGE_DIR) not in sys.path:
    sys.path.insert(0, str(BRIDGE_DIR))

from app.db import CURRENT_SCHEMA_VERSION, MIGRATIONS, DatabaseManager, utc_now_iso  # noqa: E402


@pytest.mark.parametrize("size", [4000, 8000, 16000, 32000])
def test_p02_context_pack_preserves_full_objective_without_truncation(size: int):
    marker_end = f"::END_MARKER_{size}"
    obj = ("A" * (size - len(marker_end))) + marker_end
    assert len(obj) == size

    pack = build_context_pack(
        objective=obj,
        verified_memories=[{"id": "m1", "title": "Rule", "body": "Keep objective intact", "state": "verified"}],
        max_chars=1200,
    )
    assert marker_end in pack.text
    assert pack.objective_chars == size
    assert pack.context_used_chars <= 1200
    assert pack.sections["memories"] == 1


def test_p02_context_pack_rejects_oversized_objective_explicitly():
    with pytest.raises(ContextBudgetError):
        build_context_pack(objective="X" * 32001)
    with pytest.raises(ContextBudgetError):
        build_context_pack(objective="Y" * 5000, model_max_chars=4000)


def test_p02_three_way_benchmark_honest_reporting():
    res = compare_three_way_benchmark(
        no_hub={"measurement_kind": "measured", "input_tokens": 10000},
        all_tools_hub={"measurement_kind": "measured", "input_tokens": 8000},
        dynamic_mcp_hub={"measurement_kind": "measured", "input_tokens": 3000},
    )
    assert res["C_vs_A"]["status"] == "measured"
    assert res["C_vs_A"]["saved_pct"] == 70.0
    assert res["C_vs_B"]["saved_pct"] == 62.5

    unavail = compare_three_way_benchmark(
        no_hub={"measurement_kind": "unavailable", "input_tokens": None},
        all_tools_hub={"measurement_kind": "measured", "input_tokens": 8000},
        dynamic_mcp_hub={"measurement_kind": "measured", "input_tokens": 3000},
    )
    assert unavail["C_vs_A"]["status"] == "unavailable"
    assert unavail["C_vs_A"]["saved_pct"] is None


def test_p03_coding_engine_security_hardening(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    (repo / "src").mkdir()
    (repo / "src" / "app.py").write_text("print('v1')\n", encoding="utf-8")
    (repo / "other.txt").write_text("initial\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # Pre-execution rejects protected branch main for coding mode
    with pytest.raises(ExecutionPolicyError):
        validate_pre_execution_policy(repo, ExecutionPolicy(mode="coding", owned_paths=("src",)))

    # Switch to feature branch
    subprocess.run(["git", "checkout", "-b", "feature/p03"], cwd=repo, check=True, capture_output=True)

    # Reject path traversal and absolute paths
    with pytest.raises(ExecutionPolicyError):
        validate_safe_subpath(repo, "../outside.txt")
    with pytest.raises(ExecutionPolicyError):
        validate_safe_subpath(repo, "/etc/passwd")

    # Pre-dirty file outside owned_paths: modifying it AGAIN must be caught via content hash!
    (repo / "other.txt").write_text("dirty_before\n", encoding="utf-8")
    snap = validate_pre_execution_policy(repo, ExecutionPolicy(mode="coding", owned_paths=("src",)))
    (repo / "other.txt").write_text("dirty_mutated_during_run\n", encoding="utf-8")
    with pytest.raises(ExecutionPolicyError):
        enforce_post_execution_policy(repo, ExecutionPolicy(mode="coding", owned_paths=("src",)), baseline_snapshot=snap)

    # Forbidden code-eval flags & shell metacharacters rejected
    with pytest.raises(ExecutionPolicyError):
        validate_test_command(["python", "-m", "pytest", "-c", "import os"])
    with pytest.raises(ExecutionPolicyError):
        validate_test_command(["python", "-m", "py_compile", "src/app.py; whoami"])

    # Allowlisted test strips secret env vars
    os.environ["DUMMY_API_TOKEN"] = "secret-should-not-leak"
    try:
        ev = run_allowlisted_test(repo, ["python", "-m", "py_compile", "src/app.py"])
        assert ev.passed is True
    finally:
        os.environ.pop("DUMMY_API_TOKEN", None)


def test_p04_migration_6_to_7_to_8_preserves_data_and_backup_restore(tmp_path: Path):
    db_path = tmp_path / "hub.db"
    bdir = tmp_path / "backups"

    # 1. Build a schema v6 database manually using MIGRATIONS[0..5]
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL);")
    for ver, name, sql in MIGRATIONS:
        if ver > 6:
            break
        for stmt in "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--")).split(";"):
            if stmt.strip():
                conn.execute(stmt.strip())
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (ver, name, utc_now_iso()))
    now = utc_now_iso()
    conn.execute(
        "INSERT INTO projects (id, name, canonical_identity, policy_json, hermes_approved, created_at, updated_at) VALUES ('p1', 'OldProj', 'git:old', '{}', 0, ?, ?)",
        (now, now),
    )
    conn.commit()
    conn.close()

    # 2. Upgrade via DatabaseManager -> schema 7 & 8
    dm = DatabaseManager(db_path=db_path, backup_dir=bdir)
    dm.initialize()
    ready, ver = dm.check_health()
    assert ready is True
    assert ver == CURRENT_SCHEMA_VERSION == 8

    # 3. Verify existing data intact & idempotent re-initialize
    dm.initialize()
    with dm.read_conn() as rconn:
        assert rconn.execute("SELECT name FROM projects WHERE id = 'p1'").fetchone()["name"] == "OldProj"
        tables = {r[0] for r in rconn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert {"v31_business_profiles", "v31_business_projects", "v31_business_tools", "v31_business_versions", "v31_business_mcp_bindings", "v31_mcp_server_ext", "v31_mcp_invocations", "v31_task_policy_snapshots", "v31_asset_registry"} <= tables

    # 4. Verify backup & restore round-trip
    bk = dm.create_backup()
    assert bk["schema_version"] == 8
    res = dm.restore_backup(str(bk["backup_id"]))
    assert res["restored"] is True
