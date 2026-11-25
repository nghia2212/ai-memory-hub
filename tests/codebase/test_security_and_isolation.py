"""Unit tests for path security, auth, scope isolation, exclusions, and bounded budgets."""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SERVICE_ROOT = Path(__file__).resolve().parents[2] / "services" / "codebase"
if str(SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVICE_ROOT))

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.models import WorktreeEntry  # noqa: E402
from app.registry import WorktreeRegistry, cbm_project_name  # noqa: E402
from app.security import (  # noqa: E402
    UnsafePathError,
    inspect_worktree_git,
    is_excluded_path,
    map_to_host_path,
    resolve_safe_path,
)


class FakeCBMBackend:
    """In-memory mock of codebase-memory-mcp for deterministic unit tests."""

    def __init__(self) -> None:
        self.index_calls: list[dict[str, Any]] = []
        self.projects: dict[str, list[dict[str, Any]]] = {}

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def is_ready(self) -> bool:
        return True

    async def index_repository(
        self, *, repo_path: str, project_name: str, mode: str = "fast", timeout: float = 120.0
    ) -> dict[str, Any]:
        self.index_calls.append({"repo_path": repo_path, "project_name": project_name, "mode": mode})
        return {"project": project_name, "nodes": 4, "edges": 2, "status": "indexed"}

    async def search_graph(
        self, *, project_name: str, name_pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]:
        rows = self.projects.get(project_name, [])
        groups = []
        for r in rows[:limit]:
            groups.append(
                {
                    "qn_prefix": f"{project_name}.src",
                    "file": r["file"],
                    "rows": [[r["name"], "Function", r["lines"], 0, 0]],
                }
            )
        return {"groups": groups, "truncated": False, "has_more": False}

    async def search_code(
        self, *, project_name: str, pattern: str, limit: int = 10, timeout: float = 10.0
    ) -> dict[str, Any]:
        return {"cols": ["qn", "label", "file", "lines"], "rows": [], "raw_matches": {"cols": [], "rows": []}}

    async def index_status(self, *, project_name: str, timeout: float = 10.0) -> dict[str, Any]:
        return {"project": project_name, "status": "ready"}


def test_path_traversal_and_absolute_rejection(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src" / "Main.java").write_text("class Main {}", encoding="utf-8")

    rel, resolved = resolve_safe_path(repo, "src\\Main.java")
    assert rel == "src/Main.java"
    assert resolved == (repo / "src" / "Main.java").resolve()

    for bad in ("../secret.txt", "src/../../etc/passwd", "/etc/passwd", "C:\\Windows\\win.ini", "a\x00b"):
        with pytest.raises(UnsafePathError):
            resolve_safe_path(repo, bad)


def test_symlink_escape_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside.txt"
    repo.mkdir()
    outside.write_text("SECRET", encoding="utf-8")

    # Simulate symlink escape via realpath returning outside path
    orig_realpath = __import__("os").path.realpath

    def fake_realpath(p: Any) -> str:
        if str(p).endswith("escape.txt"):
            return str(outside)
        return orig_realpath(p)

    monkeypatch.setattr("os.path.realpath", fake_realpath)
    with pytest.raises(UnsafePathError):
        resolve_safe_path(repo, "escape.txt")


def test_secret_and_dependency_exclusions() -> None:
    assert is_excluded_path(".env")
    assert is_excluded_path(".env.production")
    assert is_excluded_path("certs/server.pem")
    assert is_excluded_path("secrets/api.key")
    assert is_excluded_path("node_modules/lodash/index.js")
    assert is_excluded_path("dist/bundle.js")
    assert is_excluded_path("config/credentials.json")
    assert not is_excluded_path("src/service/PaymentService.java")


def test_windows_host_path_mapping() -> None:
    mapped = map_to_host_path(r"D:\workspace\my-repo", "src/App.ts")
    assert mapped == r"D:\workspace\my-repo\src\App.ts"


def test_linked_worktree_and_detached_head(tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: D:/workspace/main-repo/.git/worktrees/wt-1\n", encoding="utf-8")
    (wt / "App.ts").write_text("export const x = 1;", encoding="utf-8")

    state1 = inspect_worktree_git(wt, fallback_head="abc123", fallback_branch="main")
    assert state1.is_linked_worktree is True
    assert state1.head_commit == "abc123"

    # Dirty edit changes dirty_fingerprint
    (wt / "App.ts").write_text("export const x = 2;", encoding="utf-8")
    state2 = inspect_worktree_git(wt, fallback_head="abc123", fallback_branch="main")
    assert state2.dirty_fingerprint != state1.dirty_fingerprint

    # Detached HEAD in .git dir
    wt_det = tmp_path / "wt_det"
    (wt_det / ".git").mkdir(parents=True)
    (wt_det / ".git" / "HEAD").write_text("deadbeef1234567890\n", encoding="utf-8")
    state_det = inspect_worktree_git(wt_det)
    assert state_det.detached is True
    assert state_det.head_commit == "deadbeef1234567890"
    assert state_det.branch is None


def test_auth_scope_isolation_and_budget(tmp_path: Path) -> None:
    repos_root = tmp_path / "repos"
    repo_a = repos_root / "repo-a"
    repo_b = repos_root / "repo-b"
    (repo_a / "src").mkdir(parents=True)
    (repo_b / "src").mkdir(parents=True)
    (repo_a / "src" / "Order.ts").write_text(
        "export function processOrder() {\n  return 'TS_BODY_' + 'X'.repeat(500);\n}\n",
        encoding="utf-8",
    )
    (repo_a / ".env").write_text("API_KEY=supersecret\n", encoding="utf-8")
    (repo_b / "src" / "Order.java").write_text(
        "class Order {\n  int processOrder() { return 42; }\n}\n",
        encoding="utf-8",
    )

    p_a, w_a = uuid.uuid4(), uuid.uuid4()
    p_b, w_b = uuid.uuid4(), uuid.uuid4()

    cfg = Settings(
        data_dir=tmp_path / "data",
        cbm_cache_dir=tmp_path / "cbm-cache",
        cbm_allowed_root=repos_root,
        read_tokens={"read-tok"},
        admin_tokens={"admin-tok"},
    )
    reg = WorktreeRegistry(cfg)
    reg.register(
        WorktreeEntry(
            project_id=p_a,
            worktree_id=w_a,
            canonical_repo_id="repo-a",
            container_root=str(repo_a),
            host_root=r"D:\workspace\repo-a",
            head_commit="commit-a",
        )
    )
    reg.register(
        WorktreeEntry(
            project_id=p_b,
            worktree_id=w_b,
            canonical_repo_id="repo-b",
            container_root=str(repo_b),
            host_root=r"D:\workspace\repo-b",
            head_commit="commit-b",
        )
    )

    fake_cbm = FakeCBMBackend()
    fake_cbm.projects[cbm_project_name(p_a, w_a)] = [
        {"file": "src/Order.ts", "name": "processOrder", "lines": "1-3"},
        {"file": ".env", "name": "API_KEY", "lines": "1-1"},
        {"file": "../outside.txt", "name": "leak", "lines": "1-1"},
    ]
    fake_cbm.projects[cbm_project_name(p_b, w_b)] = [
        {"file": "src/Order.java", "name": "processOrder", "lines": "2-2"},
    ]

    app = create_app(settings=cfg, cbm_backend=fake_cbm, registry=reg)
    with TestClient(app) as client:
        assert client.get("/livez").status_code == 200
        assert client.get("/healthz").status_code == 200

        # Auth checks
        assert client.post("/v1/search/code", json={}).status_code == 401
        r_forbid = client.post(
            "/v1/index",
            headers={"Authorization": "Bearer read-tok"},
            json={"project_id": str(p_a), "worktree_id": str(w_a)},
        )
        assert r_forbid.status_code == 403

        # Unexpected field & invalid query length -> 422
        r_bad = client.post(
            "/v1/search/code",
            headers={"Authorization": "Bearer read-tok"},
            json={"project_id": str(p_a), "worktree_id": str(w_a), "query": "x", "extra_field": 1},
        )
        assert r_bad.status_code == 422

        # Unknown scope -> 404
        r_404 = client.post(
            "/v1/search/code",
            headers={"Authorization": "Bearer read-tok"},
            json={"project_id": str(uuid.uuid4()), "worktree_id": str(w_a), "query": "processOrder"},
        )
        assert r_404.status_code == 404

        # Index repo A
        r_idx1 = client.post(
            "/v1/index",
            headers={"Authorization": "Bearer admin-tok"},
            json={"project_id": str(p_a), "worktree_id": str(w_a)},
        )
        assert r_idx1.status_code == 200
        assert r_idx1.json()["no_op"] is False
        assert len(fake_cbm.index_calls) == 1
        job_id = r_idx1.json()["job_id"]

        # GET /v1/index/{job_id} in-scope 200 and cross-scope 404
        r_job_ok = client.get(
            f"/v1/index/{job_id}",
            headers={"Authorization": "Bearer read-tok"},
            params={"project_id": str(p_a), "worktree_id": str(w_a)},
        )
        assert r_job_ok.status_code == 200
        assert r_job_ok.json()["job_id"] == job_id

        r_job_cross = client.get(
            f"/v1/index/{job_id}",
            headers={"Authorization": "Bearer read-tok"},
            params={"project_id": str(p_b), "worktree_id": str(w_b)},
        )
        assert r_job_cross.status_code == 404

        # Second index without changes -> no_op (no full reindex on session start)
        r_idx2 = client.post(
            "/v1/index",
            headers={"Authorization": "Bearer admin-tok"},
            json={"project_id": str(p_a), "worktree_id": str(w_a)},
        )
        assert r_idx2.status_code == 200
        assert r_idx2.json()["no_op"] is True
        assert len(fake_cbm.index_calls) == 1

        # Search A: excludes .env and ../outside.txt, enforces max_chars=260
        r_search = client.post(
            "/v1/search/code",
            headers={"Authorization": "Bearer read-tok"},
            json={
                "project_id": str(p_a),
                "worktree_id": str(w_a),
                "query": "processOrder",
                "max_chars": 260,
            },
        )
        assert r_search.status_code == 200
        assert len(r_search.text) <= 260
        body = r_search.json()
        assert body["truncated"] is True
        for item in body["items"]:
            assert item["path"] == "src/Order.ts"
