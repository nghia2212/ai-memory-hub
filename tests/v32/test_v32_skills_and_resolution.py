"""V3.2 (SK32) P1/P2/P5/P7/P8 tests: Skill Registry, Immutable Versions, Scoped Resolution, Adapters & Experience-to-Skill."""
from __future__ import annotations

import sys
from pathlib import Path

BRIDGE_TESTS = Path(__file__).resolve().parents[1] / "bridge"
if str(BRIDGE_TESTS) not in sys.path:
    sys.path.insert(0, str(BRIDGE_TESTS))

from test_v2_bridge import client, h, _setup_project  # noqa: F401
from services.orchestrator.skill_adapters import inspect_skill_adapters, sync_managed_skills

BRIDGE_APP = Path(__file__).resolve().parents[2] / "services" / "memory-bridge"
if str(BRIDGE_APP) not in sys.path:
    sys.path.insert(0, str(BRIDGE_APP))
from app.v32_starter_skills import compare_skill_benchmark  # noqa: E402


def test_sk32_registry_versions_security_and_scoped_resolution(client, tmp_path: Path):
    pid1, wid1 = _setup_project(client)
    # Create second project for cross-project isolation tests
    p2 = client.post("/v1/projects", headers=h("tok-admin"), json={"name": "Proj2", "canonical_identity": "git:proj2"}).json()
    pid2 = p2["project_id"]

    # 30 starter skills seeded once
    lst = client.get("/v32/skills", headers=h("tok-read")).json()
    assert lst["total"] >= 30

    # Security: reject <script>, secret leak, and attachment path traversal
    for bad_body, bad_atts in [
        ("# Bad\n<script>alert(1)</script>", []),
        ("# Bad\nkey = sk-1234567890abcdef1234567890", []),
        ("# Safe body text here", [{"rel_path": "../escape.txt", "kind": "reference", "content": "x"}]),
    ]:
        r_bad = client.post(
            "/v32/skills",
            headers=h("tok-admin"),
            json={
                "namespace": "global",
                "slug": "bad-skill",
                "title": "Bad Skill",
                "manifest": {"name": "Bad Skill", "version": "1.0.0"},
                "markdown_body": bad_body,
                "attachments": bad_atts,
            },
        )
        assert r_bad.status_code == 422

    # Create global skill v1 draft -> publish -> bind to two projects without duplicate content
    r_c = client.post(
        "/v32/skills",
        headers=h("tok-admin"),
        json={
            "namespace": "global",
            "slug": "clean-arch",
            "title": "Clean Architecture Rule",
            "manifest": {"name": "Clean Architecture", "version": "1.0.0", "tags": ["arch"], "applicable_capabilities": ["planning"]},
            "markdown_body": "# Clean Arch v1\nKeep boundaries strict and testable.",
        },
    )
    assert r_c.status_code == 201
    sk = r_c.json()
    sk_id = sk["id"]
    v1_id = sk["versions"][0]["id"]
    v1_hash = sk["versions"][0]["content_hash"]

    assert client.post(f"/v32/skills/{sk_id}/publish", headers=h("tok-admin"), json={"version_id": v1_id}).status_code == 200

    # Published version is immutable
    r_imm = client.post(
        f"/v32/skills/{sk_id}/drafts",
        headers=h("tok-admin"),
        json={"version_id": v1_id, "manifest": {"name": "Mutated", "version": "1.0.0"}, "markdown_body": "# Mutated content here"},
    )
    assert r_imm.status_code == 409

    # Bind globally (enabled), disable in pid2 only -> pid1 still gets it, pid2 does not
    assert client.post("/v32/bindings", headers=h("tok-admin"), json={"scope_type": "global", "scope_id": "global", "skill_id": sk_id, "mode": "enabled", "priority": 10}).status_code == 200
    assert client.post("/v32/bindings", headers=h("tok-admin"), json={"scope_type": "project", "scope_id": pid2, "skill_id": sk_id, "mode": "disabled"}).status_code == 200

    res1 = client.post("/v32/resolve", headers=h("tok-write"), json={"project_id": pid1, "worktree_id": wid1, "required_capabilities": ["planning"]}).json()
    assert res1["status"] == "ready"
    assert any(s["slug"] == "clean-arch" and s["content_hash"] == v1_hash for s in res1["selected_skills"])
    run_id = res1["selected_skills"][0]["run_id"]
    assert run_id is not None

    res2 = client.post("/v32/resolve", headers=h("tok-write"), json={"project_id": pid2, "required_capabilities": ["planning"]}).json()
    assert not any(s["slug"] == "clean-arch" for s in res2["selected_skills"])

    # Mandatory parent binding CANNOT be disabled by project scope!
    assert client.post("/v32/bindings", headers=h("tok-admin"), json={"scope_type": "global", "scope_id": "global", "skill_id": sk_id, "mode": "enabled", "mandatory": True}).status_code == 200
    r_mand_dis = client.post("/v32/bindings", headers=h("tok-admin"), json={"scope_type": "project", "scope_id": pid1, "skill_id": sk_id, "mode": "disabled"})
    assert r_mand_dis.status_code == 403

    # Create & publish v2, then rollback to v1 -> historical run still keeps v1_hash
    r_v2 = client.post(
        f"/v32/skills/{sk_id}/drafts",
        headers=h("tok-admin"),
        json={"version": "2.0.0", "manifest": {"name": "Clean Architecture", "version": "2.0.0", "applicable_capabilities": ["planning"]}, "markdown_body": "# Clean Arch v2\nUpdated guidance."},
    )
    assert r_v2.status_code == 200
    v2_id = next(v["id"] for v in r_v2.json()["versions"] if v["version"] == "2.0.0")
    assert client.post(f"/v32/skills/{sk_id}/publish", headers=h("tok-admin"), json={"version_id": v2_id}).status_code == 200
    assert client.post(f"/v32/skills/{sk_id}/rollback", headers=h("tok-admin"), json={"version_id": v1_id}).status_code == 200
    assert client.get(f"/v32/skills/{sk_id}", headers=h("tok-read")).json()["active_version_id"] == v1_id

    runs = client.get(f"/v32/runs?project_id={pid1}", headers=h("tok-read")).json()["items"]
    assert any(r["id"] == run_id and r["content_hash"] == v1_hash for r in runs)

    # Required missing MCP blocks resolution (skills cannot grant permissions!)
    r_req = client.post(
        "/v32/skills",
        headers=h("tok-admin"),
        json={
            "namespace": "global",
            "slug": "needs-missing-mcp",
            "title": "Needs Missing MCP",
            "manifest": {"name": "Needs Missing MCP", "version": "1.0.0", "required_mcp_ids": ["nonexistent-mcp"]},
            "markdown_body": "# Requires nonexistent MCP server.",
        },
    ).json()
    assert client.post(f"/v32/skills/{r_req['id']}/publish", headers=h("tok-admin"), json={"version_id": r_req["versions"][0]["id"]}).status_code == 200
    assert client.post("/v32/bindings", headers=h("tok-admin"), json={"scope_type": "project", "scope_id": pid1, "skill_id": r_req["id"], "mode": "enabled", "required": True}).status_code == 200
    res_blk = client.post("/v32/resolve", headers=h("tok-write"), json={"project_id": pid1}).json()
    assert res_blk["status"] == "blocked"
    assert len(res_blk["blockers"]) >= 1

    # Import preview & explicit approval
    imp_prev = client.post(
        "/v32/skills/import",
        headers=h("tok-admin"),
        json={
            "namespace": "imported",
            "slug": "imported-rule",
            "title": "Imported Rule",
            "source": "https://example.org/skills",
            "license": "MIT",
            "manifest": {"name": "Imported Rule", "version": "1.0.0"},
            "markdown_body": "# Imported Rule\nSafe markdown body.",
            "preview_only": True,
        },
    )
    assert imp_prev.status_code == 200
    assert imp_prev.json()["preview_only"] is True

    # P5 Provider Skill Adapter sync preserves unrelated user skills
    user_skill = tmp_path / "my-personal-skill" / "SKILL.md"
    user_skill.parent.mkdir(parents=True)
    user_skill.write_text("# Personal Skill", encoding="utf-8")
    adapters = inspect_skill_adapters()
    assert len(adapters) >= 6
    sync_res = sync_managed_skills(target_root=tmp_path, resolved_skills=res1["selected_skills"], preview_only=False)
    assert sync_res["delivery_status"] == "delivered"
    assert sync_res["load_status"] == "unknown"
    assert (tmp_path / "hub-managed" / "clean-arch" / "SKILL.md").is_file()
    assert user_skill.read_text(encoding="utf-8") == "# Personal Skill"

    # P7 Experience -> Skill Candidate: reject unverified, accept verified, human review approve/reject
    m_cand = client.post(
        "/v1/memories",
        headers=h("tok-write"),
        json={"project_id": pid1, "worktree_id": wid1, "type": "decision", "title": "Lesson 1", "body": "Always run isolated tests before push.", "source_agent": "lead", "idempotency_key": "sk32-m1"},
    ).json()
    mid = m_cand["memory_id"]
    # Unverified memory rejected
    r_unv = client.post(
        "/v32/candidates/propose",
        headers=h("tok-write"),
        json={"project_id": pid1, "memory_ids": [mid], "proposed_slug": "lesson-1", "proposed_title": "Lesson 1 Skill"},
    )
    assert r_unv.status_code == 422

    # Verify memory -> propose -> approve
    assert client.post(f"/v1/memories/{mid}/verify", headers=h("tok-admin"), json={"action": "verify"}).status_code == 200
    # Cross-project memory rejected
    r_cross = client.post(
        "/v32/candidates/propose",
        headers=h("tok-write"),
        json={"project_id": pid2, "memory_ids": [mid], "proposed_slug": "leak-skill", "proposed_title": "Leak"},
    )
    assert r_cross.status_code == 403

    cand = client.post(
        "/v32/candidates/propose",
        headers=h("tok-write"),
        json={"project_id": pid1, "memory_ids": [mid], "proposed_slug": "lesson-1", "proposed_title": "Lesson 1 Skill"},
    )
    assert cand.status_code == 201
    cid = cand.json()["id"]
    rev = client.post(f"/v32/candidates/{cid}/review", headers=h("tok-admin"), json={"decision": "approve", "note": "Verified & approved"})
    assert rev.status_code == 200
    assert rev.json()["status"] == "approved"

    # P8 Honest paired skill benchmark
    bm_ok = compare_skill_benchmark(
        {"measurement_kind": "measured", "input_tokens": 5000},
        {"measurement_kind": "measured", "input_tokens": 3500},
    )
    assert bm_ok["status"] == "measured" and bm_ok["delta_pct"] == 30.0
    bm_na = compare_skill_benchmark(
        {"measurement_kind": "unavailable", "input_tokens": None},
        {"measurement_kind": "measured", "input_tokens": 3500},
    )
    assert bm_na["status"] == "unavailable" and bm_na["delta_pct"] is None
