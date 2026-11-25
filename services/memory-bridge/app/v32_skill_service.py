"""V3.2 (SK32) — Centralized Skill Registry, Scoped Resolution, Run Telemetry & Experience-to-Skill Service."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .v32_skill_schema import (
    SkillBindingUpsertRequest,
    SkillCandidateProposeRequest,
    SkillCandidateReviewRequest,
    SkillCreateRequest,
    SkillDraftUpdateRequest,
    SkillImportRequest,
    SkillPublishRequest,
    SkillResolveRequest,
    SkillRollbackRequest,
    SkillRunStatusUpdateRequest,
)
from .v32_starter_skills import STARTER_SKILLS

_HTML_EXEC_RE = re.compile(r"(?:<\s*script\b|javascript:|on(?:error|load|click|mouseover)\s*=|<\s*iframe\b)", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z\-_]{30,}|-----BEGIN [A-Z ]+PRIVATE KEY-----|password\s*=\s*['\"][^'\"]{4,}['\"])",
    re.IGNORECASE,
)
_SCOPE_ORDER = {"global": 0, "business": 1, "project": 2, "agent_role": 3}


def _content_hash(manifest: dict[str, Any], markdown_body: str, attachments: list[dict[str, Any]]) -> str:
    payload = json.dumps({"manifest": manifest, "markdown_body": markdown_body, "attachments": attachments}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


def _validate_safe_skill_content(markdown_body: str, attachments: list[dict[str, Any]]) -> None:
    if _HTML_EXEC_RE.search(markdown_body):
        raise BridgeError(422, "unsafe_skill_html", "Raw executable HTML/script tags are forbidden in SKILL.md.")
    if _SECRET_RE.search(markdown_body):
        raise BridgeError(422, "secret_leak_denied", "Secret/credential pattern detected in SKILL.md; skills must never contain secrets.")
    total_att_bytes = 0
    for att in attachments:
        rp = str(att.get("rel_path") or "")
        if rp.startswith(("/", "\\")) or ":" in rp.split("/")[0] or ".." in Path(rp.replace("\\", "/")).parts:
            raise BridgeError(422, "unsafe_attachment_path", f"Path traversal or absolute attachment path forbidden: {rp}")
        content = str(att.get("content") or "")
        if _HTML_EXEC_RE.search(content) or _SECRET_RE.search(content):
            raise BridgeError(422, "unsafe_attachment_content", f"Unsafe script or secret detected in attachment: {rp}")
        total_att_bytes += len(content.encode("utf-8"))
    if total_att_bytes > 128_000:
        raise BridgeError(422, "attachment_quota_exceeded", "Total attachment size exceeds 128KB quota.")


class V32SkillService:
    """Centralized provider-neutral Skill Registry, Resolver, Run Telemetry & Candidate Learning."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._seeded = False

    def ensure_starter_library(self) -> None:
        """Seed the 30 generic reviewed starter skills ONCE (published, NOT indiscriminately bound)."""
        if self._seeded:
            return
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            flag = conn.execute("SELECT value FROM sk32_meta WHERE key = 'starter_library_seeded'").fetchone()
            if not flag:
                for st in STARTER_SKILLS:
                    if conn.execute("SELECT 1 FROM sk32_skills WHERE namespace = ? AND slug = ?", (st["namespace"], st["slug"])).fetchone():
                        continue
                    sid = str(uuid.uuid4())
                    vid = str(uuid.uuid4())
                    manifest = {
                        "name": st["title"],
                        "description": f"Starter skill: {st['title']}",
                        "version": "1.0.0",
                        "tags": st["tags"],
                        "applicable_capabilities": st["caps"],
                        "required_mcp_ids": [],
                        "optional_mcp_ids": [],
                        "required_tool_names": [],
                        "permissions": [],
                        "references": [],
                    }
                    chash = _content_hash(manifest, st["body"], [])
                    conn.execute(
                        """
                        INSERT INTO sk32_skills (
                            id, namespace, slug, title, description, owner, lifecycle, active_version_id,
                            provenance_json, is_starter, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 'starter-library', 'active', ?, ?, 1, ?, ?)
                        """,
                        (sid, st["namespace"], st["slug"], st["title"], manifest["description"], vid, json.dumps({"source": "builtin_starter", "license": "MIT"}), now, now),
                    )
                    conn.execute(
                        """
                        INSERT INTO sk32_skill_versions (
                            id, skill_id, version, content_hash, manifest_json, markdown_body, attachments_json,
                            state, author, approved_by, approved_at, created_at
                        ) VALUES (?, ?, '1.0.0', ?, ?, ?, '[]', 'published', 'starter-library', 'starter-review', ?, ?)
                        """,
                        (vid, sid, chash, json.dumps(manifest, ensure_ascii=False), st["body"], now, now),
                    )
                conn.execute("INSERT INTO sk32_meta (key, value, updated_at) VALUES ('starter_library_seeded', '1', ?)", (now,))
        self._seeded = True

    # --- P1: Registry, CRUD, Immutable Versions, Import/Export ---
    def list_skills(
        self,
        *,
        namespace: str | None = None,
        lifecycle: str | None = "active",
        tag: str | None = None,
        search: str = "",
        include_starter: bool = True,
    ) -> dict[str, Any]:
        self.ensure_starter_library()
        clauses = ["1=1"]
        args: list[Any] = []
        if namespace:
            clauses.append("s.namespace = ?")
            args.append(namespace)
        if lifecycle and lifecycle != "all":
            clauses.append("s.lifecycle = ?")
            args.append(lifecycle)
        if not include_starter:
            clauses.append("s.is_starter = 0")
        if search.strip():
            clauses.append("(s.title LIKE ? OR s.slug LIKE ? OR s.description LIKE ?)")
            p = f"%{search.strip()}%"
            args.extend([p, p, p])
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*, v.version AS active_version, v.content_hash AS active_hash, v.state AS active_state, v.manifest_json AS active_manifest_json
                FROM sk32_skills s
                LEFT JOIN sk32_skill_versions v ON v.id = s.active_version_id
                WHERE {' AND '.join(clauses)}
                ORDER BY s.is_starter ASC, s.updated_at DESC
                """,
                args,
            ).fetchall()
            items = []
            for r in rows:
                manifest = json.loads(r["active_manifest_json"] or "{}")
                tags = manifest.get("tags") or []
                if tag and tag.lower() not in [t.lower() for t in tags]:
                    continue
                bind_cnt = conn.execute("SELECT COUNT(*) FROM sk32_skill_bindings WHERE skill_id = ?", (r["id"],)).fetchone()[0]
                ver_cnt = conn.execute("SELECT COUNT(*) FROM sk32_skill_versions WHERE skill_id = ?", (r["id"],)).fetchone()[0]
                items.append(
                    {
                        "id": r["id"],
                        "namespace": r["namespace"],
                        "slug": r["slug"],
                        "title": r["title"],
                        "description": r["description"],
                        "owner": r["owner"],
                        "lifecycle": r["lifecycle"],
                        "is_starter": bool(r["is_starter"]),
                        "active_version_id": r["active_version_id"],
                        "active_version": r["active_version"],
                        "active_hash": r["active_hash"],
                        "active_state": r["active_state"],
                        "tags": tags,
                        "binding_count": bind_cnt,
                        "version_count": ver_cnt,
                        "updated_at": r["updated_at"],
                    }
                )
            return {"items": items, "total": len(items)}

    def get_skill_detail(self, skill_id: str) -> dict[str, Any]:
        self.ensure_starter_library()
        with self.db.read_conn() as conn:
            s = conn.execute("SELECT * FROM sk32_skills WHERE id = ?", (skill_id,)).fetchone()
            if not s:
                raise BridgeError(404, "skill_not_found", "Skill not found.")
            vers = conn.execute("SELECT * FROM sk32_skill_versions WHERE skill_id = ? ORDER BY created_at DESC", (skill_id,)).fetchall()
            binds = conn.execute("SELECT * FROM sk32_skill_bindings WHERE skill_id = ? ORDER BY priority ASC", (skill_id,)).fetchall()
            return {
                "id": s["id"],
                "namespace": s["namespace"],
                "slug": s["slug"],
                "title": s["title"],
                "description": s["description"],
                "owner": s["owner"],
                "lifecycle": s["lifecycle"],
                "is_starter": bool(s["is_starter"]),
                "active_version_id": s["active_version_id"],
                "provenance": json.loads(s["provenance_json"] or "{}"),
                "created_at": s["created_at"],
                "updated_at": s["updated_at"],
                "versions": [
                    {
                        "id": v["id"],
                        "version": v["version"],
                        "content_hash": v["content_hash"],
                        "manifest": json.loads(v["manifest_json"]),
                        "markdown_body": v["markdown_body"],
                        "attachments": json.loads(v["attachments_json"] or "[]"),
                        "state": v["state"],
                        "author": v["author"],
                        "approved_by": v["approved_by"],
                        "approved_at": v["approved_at"],
                        "created_at": v["created_at"],
                    }
                    for v in vers
                ],
                "bindings": [dict(b) for b in binds],
            }

    def get_skill_version(self, skill_id: str, version_id: str | None = None) -> dict[str, Any]:
        self.ensure_starter_library()
        with self.db.read_conn() as conn:
            s = conn.execute("SELECT * FROM sk32_skills WHERE id = ?", (skill_id,)).fetchone()
            if not s:
                raise BridgeError(404, "skill_not_found", "Skill not found.")
            target_vid = version_id or s["active_version_id"]
            if not target_vid:
                raise BridgeError(404, "skill_version_not_found", "No active version published for this skill.")
            v = conn.execute("SELECT * FROM sk32_skill_versions WHERE id = ? AND skill_id = ?", (target_vid, skill_id)).fetchone()
            if not v:
                raise BridgeError(404, "skill_version_not_found", "Skill version not found.")
            return {
                "skill_id": s["id"],
                "namespace": s["namespace"],
                "slug": s["slug"],
                "title": s["title"],
                "version_id": v["id"],
                "version": v["version"],
                "content_hash": v["content_hash"],
                "state": v["state"],
                "manifest": json.loads(v["manifest_json"]),
                "markdown_body": v["markdown_body"],
                "attachments": json.loads(v["attachments_json"] or "[]"),
                "created_at": v["created_at"],
            }

    def create_skill(self, req: SkillCreateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_starter_library()
        atts = [a.model_dump() for a in req.attachments]
        _validate_safe_skill_content(req.markdown_body, atts)
        now = utc_now_iso()
        sid = str(uuid.uuid4())
        vid = str(uuid.uuid4())
        manifest = req.manifest.model_dump()
        chash = _content_hash(manifest, req.markdown_body, atts)
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM sk32_skills WHERE namespace = ? AND slug = ?", (req.namespace, req.slug)).fetchone():
                raise BridgeError(409, "skill_exists", f"Skill '{req.namespace}/{req.slug}' already exists.")
            conn.execute(
                """
                INSERT INTO sk32_skills (
                    id, namespace, slug, title, description, owner, lifecycle, active_version_id,
                    provenance_json, is_starter, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, 0, ?, ?)
                """,
                (sid, req.namespace, req.slug, req.title, req.description, actor, json.dumps(redact_payload(req.provenance), ensure_ascii=False), now, now),
            )
            conn.execute(
                """
                INSERT INTO sk32_skill_versions (
                    id, skill_id, version, content_hash, manifest_json, markdown_body, attachments_json,
                    state, author, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                """,
                (vid, sid, req.manifest.version, chash, json.dumps(manifest, ensure_ascii=False), req.markdown_body, json.dumps(atts, ensure_ascii=False), actor, now),
            )
        return self.get_skill_detail(sid)

    def create_or_update_draft(self, skill_id: str, req: SkillDraftUpdateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_starter_library()
        atts = [a.model_dump() for a in req.attachments]
        _validate_safe_skill_content(req.markdown_body, atts)
        now = utc_now_iso()
        manifest = req.manifest.model_dump()
        chash = _content_hash(manifest, req.markdown_body, atts)
        with self.db.write_tx() as conn:
            s = conn.execute("SELECT * FROM sk32_skills WHERE id = ?", (skill_id,)).fetchone()
            if not s:
                raise BridgeError(404, "skill_not_found", "Skill not found.")
            if req.expected_updated_at and s["updated_at"] != req.expected_updated_at:
                raise BridgeError(409, "optimistic_concurrency_conflict", "Skill was modified concurrently; refresh and retry.")
            if req.version_id:
                ex = conn.execute("SELECT * FROM sk32_skill_versions WHERE id = ? AND skill_id = ?", (req.version_id, skill_id)).fetchone()
                if not ex:
                    raise BridgeError(404, "skill_version_not_found", "Version not found.")
                if ex["state"] != "draft":
                    raise BridgeError(409, "version_immutable", "Published or retired versions are immutable; create a new draft version instead.")
                conn.execute(
                    "UPDATE sk32_skill_versions SET content_hash = ?, manifest_json = ?, markdown_body = ?, attachments_json = ? WHERE id = ?",
                    (chash, json.dumps(manifest, ensure_ascii=False), req.markdown_body, json.dumps(atts, ensure_ascii=False), req.version_id),
                )
            else:
                if conn.execute("SELECT 1 FROM sk32_skill_versions WHERE skill_id = ? AND version = ?", (skill_id, req.version)).fetchone():
                    raise BridgeError(409, "version_exists", f"Version '{req.version}' already exists for this skill.")
                vid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO sk32_skill_versions (
                        id, skill_id, version, content_hash, manifest_json, markdown_body, attachments_json,
                        state, author, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
                    """,
                    (vid, skill_id, req.version, chash, json.dumps(manifest, ensure_ascii=False), req.markdown_body, json.dumps(atts, ensure_ascii=False), actor, now),
                )
            conn.execute(
                "UPDATE sk32_skills SET title = COALESCE(?, title), description = COALESCE(?, description), updated_at = ? WHERE id = ?",
                (req.title, req.description, now, skill_id),
            )
        return self.get_skill_detail(skill_id)

    def publish_version(self, skill_id: str, req: SkillPublishRequest, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            v = conn.execute("SELECT * FROM sk32_skill_versions WHERE id = ? AND skill_id = ?", (req.version_id, skill_id)).fetchone()
            if not v:
                raise BridgeError(404, "skill_version_not_found", "Skill version not found.")
            if v["state"] == "retired":
                raise BridgeError(409, "version_retired", "Cannot publish a retired version.")
            conn.execute(
                "UPDATE sk32_skill_versions SET state = 'published', approved_by = ?, approved_at = ? WHERE id = ?",
                (actor, now, req.version_id),
            )
            if req.set_active:
                conn.execute("UPDATE sk32_skills SET active_version_id = ?, updated_at = ? WHERE id = ?", (req.version_id, now, skill_id))
        return self.get_skill_detail(skill_id)

    def rollback_active_version(self, skill_id: str, req: SkillRollbackRequest, actor: str = "admin") -> dict[str, Any]:
        """Pin/rollback active version WITHOUT mutating historical versions."""
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            v = conn.execute("SELECT * FROM sk32_skill_versions WHERE id = ? AND skill_id = ?", (req.version_id, skill_id)).fetchone()
            if not v or v["state"] != "published":
                raise BridgeError(422, "invalid_rollback_target", "Rollback target must be an existing published version.")
            conn.execute("UPDATE sk32_skills SET active_version_id = ?, updated_at = ? WHERE id = ?", (req.version_id, now, skill_id))
        return self.get_skill_detail(skill_id)

    def delete_or_archive_skill(self, skill_id: str) -> dict[str, Any]:
        """Delete ONLY unreferenced drafts; archive referenced/published skills to preserve historical evidence."""
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            s = conn.execute("SELECT * FROM sk32_skills WHERE id = ?", (skill_id,)).fetchone()
            if not s:
                raise BridgeError(404, "skill_not_found", "Skill not found.")
            has_published = conn.execute("SELECT 1 FROM sk32_skill_versions WHERE skill_id = ? AND state != 'draft'", (skill_id,)).fetchone()
            has_runs = conn.execute("SELECT 1 FROM sk32_skill_runs WHERE skill_id = ?", (skill_id,)).fetchone()
            has_binds = conn.execute("SELECT 1 FROM sk32_skill_bindings WHERE skill_id = ?", (skill_id,)).fetchone()
            if not has_published and not has_runs and not has_binds:
                conn.execute("DELETE FROM sk32_skills WHERE id = ?", (skill_id,))
                return {"id": skill_id, "action": "deleted_draft"}
            conn.execute("UPDATE sk32_skills SET lifecycle = 'archived', updated_at = ? WHERE id = ?", (now, skill_id))
            return {"id": skill_id, "action": "archived", "preserved_history": True}

    def export_skill(self, skill_id: str) -> dict[str, Any]:
        detail = self.get_skill_detail(skill_id)
        return {"schema": "ai-memory-hub-sk32-skill-export", "exported_at": utc_now_iso(), "skill": redact_payload(detail)}

    def import_skill(self, req: SkillImportRequest, actor: str = "admin") -> dict[str, Any]:
        atts = [a.model_dump() for a in req.attachments]
        _validate_safe_skill_content(req.markdown_body, atts)
        manifest = req.manifest.model_dump()
        chash = _content_hash(manifest, req.markdown_body, atts)
        preview = {
            "preview_only": req.preview_only,
            "namespace": req.namespace,
            "slug": req.slug,
            "title": req.title,
            "source": req.source,
            "license": req.license,
            "content_hash": chash,
            "bytes": len(req.markdown_body.encode("utf-8")),
            "attachments_count": len(atts),
            "requires_explicit_approval": True,
        }
        if req.preview_only:
            return preview
        if not req.approved:
            raise BridgeError(403, "import_approval_required", "Explicit approval=true is required to complete skill import.")
        created = self.create_skill(
            SkillCreateRequest(
                namespace=req.namespace,
                slug=req.slug,
                title=req.title,
                description=req.manifest.description,
                manifest=req.manifest,
                markdown_body=req.markdown_body,
                attachments=req.attachments,
                provenance={"source": req.source, "license": req.license, "imported_by": actor},
            ),
            actor=actor,
        )
        return {"imported": True, "preview": preview, "skill": created}

    # --- P2: Scoped Bindings & Deterministic Resolution ---
    def upsert_binding(self, req: SkillBindingUpsertRequest) -> dict[str, Any]:
        self.ensure_starter_library()
        now = utc_now_iso()
        bid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM sk32_skills WHERE id = ?", (req.skill_id,)).fetchone():
                raise BridgeError(404, "skill_not_found", "Skill not found.")
            # Mandatory policy in parent scope cannot be weakened by less privileged scope!
            if req.mode == "disabled":
                parent_mand = conn.execute(
                    "SELECT scope_type FROM sk32_skill_bindings WHERE skill_id = ? AND mandatory = 1 AND mode = 'enabled'",
                    (req.skill_id,),
                ).fetchall()
                for pm in parent_mand:
                    if _SCOPE_ORDER[pm["scope_type"]] < _SCOPE_ORDER[req.scope_type]:
                        raise BridgeError(403, "mandatory_policy_protected", f"Skill is mandatory at scope '{pm['scope_type']}' and cannot be disabled at '{req.scope_type}'.")
            conn.execute(
                """
                INSERT INTO sk32_skill_bindings (
                    id, scope_type, scope_id, skill_id, mode, version_constraint, priority, required, mandatory, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope_type, scope_id, skill_id) DO UPDATE SET
                    mode = excluded.mode,
                    version_constraint = excluded.version_constraint,
                    priority = excluded.priority,
                    required = excluded.required,
                    mandatory = excluded.mandatory,
                    updated_at = excluded.updated_at
                """,
                (bid, req.scope_type, req.scope_id, req.skill_id, req.mode, req.version_constraint, req.priority, 1 if req.required else 0, 1 if req.mandatory else 0, now, now),
            )
            row = conn.execute(
                "SELECT * FROM sk32_skill_bindings WHERE scope_type = ? AND scope_id = ? AND skill_id = ?",
                (req.scope_type, req.scope_id, req.skill_id),
            ).fetchone()
            return dict(row)

    def delete_binding(self, binding_id: str) -> dict[str, Any]:
        with self.db.write_tx() as conn:
            cur = conn.execute("DELETE FROM sk32_skill_bindings WHERE id = ?", (binding_id,))
            if cur.rowcount == 0:
                raise BridgeError(404, "binding_not_found", "Binding not found.")
        return {"deleted": True, "binding_id": binding_id}

    def list_bindings(self, *, scope_type: str | None = None, scope_id: str | None = None, skill_id: str | None = None) -> dict[str, Any]:
        clauses = ["1=1"]
        args: list[Any] = []
        if scope_type:
            clauses.append("b.scope_type = ?")
            args.append(scope_type)
        if scope_id:
            clauses.append("b.scope_id = ?")
            args.append(scope_id)
        if skill_id:
            clauses.append("b.skill_id = ?")
            args.append(skill_id)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT b.*, s.namespace, s.slug, s.title
                FROM sk32_skill_bindings b
                JOIN sk32_skills s ON s.id = b.skill_id
                WHERE {' AND '.join(clauses)}
                ORDER BY b.priority ASC, s.slug ASC
                """,
                args,
            ).fetchall()
            return {"items": [dict(r) for r in rows]}

    def resolve_skills(self, req: SkillResolveRequest) -> dict[str, Any]:
        """Deterministic 0-LLM skill resolution: global -> business -> project -> agent_role, MCP & budget checks."""
        self.ensure_starter_library()
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        now = utc_now_iso()

        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")

            # Resolve business scope & detect multi-business ambiguity
            biz_id = req.business_id
            if not biz_id:
                linked_biz = [
                    r["business_id"]
                    for r in conn.execute(
                        "SELECT bp.business_id FROM v31_business_projects bp JOIN v31_business_profiles b ON b.id = bp.business_id WHERE bp.project_id = ? AND b.archived = 0 AND b.enabled = 1",
                        (pid,),
                    ).fetchall()
                ]
                if len(linked_biz) == 1:
                    biz_id = linked_biz[0]
                elif len(linked_biz) > 1:
                    # Check if businesses have conflicting skill bindings
                    b_ph = ",".join("?" for _ in linked_biz)
                    biz_binds = conn.execute(
                        f"SELECT skill_id, COUNT(DISTINCT mode) AS modes FROM sk32_skill_bindings WHERE scope_type = 'business' AND scope_id IN ({b_ph}) GROUP BY skill_id HAVING modes > 1",
                        linked_biz,
                    ).fetchall()
                    if biz_binds:
                        raise BridgeError(
                            409,
                            "ambiguous_business_bindings",
                            f"Project belongs to {len(linked_biz)} businesses with conflicting skill bindings; pass explicit business_id.",
                        )

            # Gather bindings along scope chain
            scope_pairs: list[tuple[str, str]] = [("global", "global")]
            if biz_id:
                scope_pairs.append(("business", biz_id))
            scope_pairs.append(("project", pid))
            if req.agent_role:
                scope_pairs.append(("agent_role", req.agent_role))

            effective: dict[str, dict[str, Any]] = {}
            for stype, sid in scope_pairs:
                rows = conn.execute(
                    "SELECT * FROM sk32_skill_bindings WHERE scope_type = ? AND scope_id = ? ORDER BY priority ASC",
                    (stype, sid),
                ).fetchall()
                for r in rows:
                    sk_id = r["skill_id"]
                    prev = effective.get(sk_id)
                    if r["mode"] == "inherit":
                        continue
                    if prev and prev["mandatory"] and r["mode"] == "disabled":
                        # Mandatory parent policy always wins
                        continue
                    effective[sk_id] = {
                        "skill_id": sk_id,
                        "mode": r["mode"],
                        "version_constraint": r["version_constraint"],
                        "priority": r["priority"],
                        "required": bool(r["required"]) or (bool(prev["required"]) if prev else False),
                        "mandatory": bool(r["mandatory"]) or (bool(prev["mandatory"]) if prev else False),
                        "source_scope": f"{stype}:{sid}",
                    }

            # Also include explicitly requested slugs (within active published skills, never overriding explicit disabled)
            wanted_caps = {c.strip().lower() for c in req.required_capabilities if c.strip()}
            req_slugs = {s.strip().lower() for s in req.requested_skill_slugs if s.strip()}
            for slug in req_slugs:
                srow = conn.execute("SELECT id FROM sk32_skills WHERE slug = ? AND lifecycle = 'active'", (slug,)).fetchone()
                if srow and srow["id"] not in effective:
                    effective[srow["id"]] = {
                        "skill_id": srow["id"],
                        "mode": "enabled",
                        "version_constraint": "active",
                        "priority": 50,
                        "required": False,
                        "mandatory": False,
                        "source_scope": "explicit_request",
                    }

            # Build approved MCP & tool lookup (skills NEVER grant permissions!)
            approved_mcps = {
                r["id"]: r["name"]
                for r in conn.execute("SELECT id, name FROM v3_mcp_servers WHERE approval_state = 'approved'").fetchall()
            }
            approved_mcp_names = set(approved_mcps.values()) | set(approved_mcps.keys())
            allowed_tools: set[str] = set()
            if biz_id:
                allowed_tools = {
                    r["tool_name"]
                    for r in conn.execute(
                        """
                        SELECT t.tool_name FROM v31_business_tools bt
                        JOIN v3_mcp_tools t ON t.id = bt.tool_id
                        WHERE bt.business_id = ? AND bt.policy IN ('allow', 'approval')
                        """,
                        (biz_id,),
                    ).fetchall()
                }

            selected: list[dict[str, Any]] = []
            excluded: list[dict[str, Any]] = []
            blockers: list[str] = []
            degraded_notes: list[str] = []
            used_chars = 0

            # Deterministic stable ordering: mandatory -> required -> priority -> skill_id
            ordered = sorted(
                effective.values(),
                key=lambda x: (0 if x["mandatory"] else 1, 0 if x["required"] else 1, x["priority"], x["skill_id"]),
            )

            for entry in ordered:
                sk_id = entry["skill_id"]
                if entry["mode"] != "enabled":
                    excluded.append({"skill_id": sk_id, "reason": f"disabled_at_{entry['source_scope']}"})
                    continue
                srow = conn.execute("SELECT * FROM sk32_skills WHERE id = ?", (sk_id,)).fetchone()
                if not srow or srow["lifecycle"] != "active":
                    excluded.append({"skill_id": sk_id, "reason": "skill_archived_or_missing"})
                    continue

                # Resolve immutable version (pinned or active)
                vc = entry["version_constraint"]
                if vc and vc != "active":
                    vrow = conn.execute(
                        "SELECT * FROM sk32_skill_versions WHERE skill_id = ? AND (id = ? OR version = ?) AND state = 'published'",
                        (sk_id, vc, vc),
                    ).fetchone()
                else:
                    vrow = conn.execute(
                        "SELECT * FROM sk32_skill_versions WHERE id = ? AND state = 'published'",
                        (srow["active_version_id"],),
                    ).fetchone() if srow["active_version_id"] else None
                if not vrow:
                    if entry["required"] or entry["mandatory"]:
                        blockers.append(f"Required skill '{srow['slug']}' has no published version matching '{vc}'.")
                    else:
                        excluded.append({"skill_id": sk_id, "slug": srow["slug"], "reason": "no_published_version"})
                    continue

                manifest = json.loads(vrow["manifest_json"])
                skill_caps = {c.lower() for c in (manifest.get("applicable_capabilities") or [])}
                cap_match = sorted(wanted_caps & skill_caps)
                if wanted_caps and not cap_match and not entry["required"] and not entry["mandatory"] and srow["slug"].lower() not in req_slugs:
                    excluded.append({"skill_id": sk_id, "slug": srow["slug"], "reason": "capability_not_needed"})
                    continue

                # Verify required MCP & tool dependencies against actual permissions
                missing_mcps = [m for m in (manifest.get("required_mcp_ids") or []) if m not in approved_mcp_names]
                missing_tools = [t for t in (manifest.get("required_tool_names") or []) if biz_id and t not in allowed_tools]
                if missing_mcps or missing_tools:
                    msg = f"Skill '{srow['slug']}' missing authorized dependencies: mcps={missing_mcps}, tools={missing_tools}"
                    if entry["required"] or entry["mandatory"]:
                        blockers.append(msg)
                    else:
                        degraded_notes.append(msg)
                        excluded.append({"skill_id": sk_id, "slug": srow["slug"], "reason": "missing_mcp_dependency"})
                    continue

                # Optional MCP degraded check
                missing_opt = [m for m in (manifest.get("optional_mcp_ids") or []) if m not in approved_mcp_names]
                if missing_opt:
                    degraded_notes.append(f"Skill '{srow['slug']}' optional MCP unavailable: {missing_opt}")

                body_len = len(vrow["markdown_body"])
                if used_chars + body_len > req.max_chars:
                    if entry["required"] or entry["mandatory"]:
                        blockers.append(f"Required skill '{srow['slug']}' ({body_len} chars) exceeds remaining context budget ({req.max_chars - used_chars} chars); dispatch blocked instead of silent truncation.")
                    else:
                        excluded.append({"skill_id": sk_id, "slug": srow["slug"], "reason": "budget_exceeded"})
                    continue

                used_chars += body_len
                reason = f"scope={entry['source_scope']}" + (f", caps={','.join(cap_match)}" if cap_match else "")
                run_id = None
                if req.record_run:
                    run_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO sk32_skill_runs (
                            id, project_id, worktree_id, business_id, plan_id, task_id, attempt_id, session_id, trace_id,
                            skill_id, version_id, version_str, content_hash, selection_reason, adapter, status,
                            evidence_json, bytes_count, input_tokens, measurement_kind, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'selected', ?, ?, NULL, 'unavailable', ?)
                        """,
                        (
                            run_id, pid, wid, biz_id,
                            str(req.plan_id) if req.plan_id else None,
                            req.task_id, req.attempt_id,
                            str(req.session_id) if req.session_id else None,
                            str(req.trace_id) if req.trace_id else None,
                            sk_id, vrow["id"], vrow["version"], vrow["content_hash"],
                            reason, req.adapter,
                            json.dumps({"source_scope": entry["source_scope"], "required": entry["required"], "mandatory": entry["mandatory"]}),
                            len(vrow["markdown_body"].encode("utf-8")),
                            now,
                        ),
                    )
                selected.append(
                    {
                        "run_id": run_id,
                        "skill_id": sk_id,
                        "namespace": srow["namespace"],
                        "slug": srow["slug"],
                        "title": srow["title"],
                        "version_id": vrow["id"],
                        "version": vrow["version"],
                        "content_hash": vrow["content_hash"],
                        "source_scope": entry["source_scope"],
                        "selection_reason": reason,
                        "required": entry["required"],
                        "mandatory": entry["mandatory"],
                        "bytes_count": len(vrow["markdown_body"].encode("utf-8")),
                        "manifest": manifest,
                        "markdown_body": vrow["markdown_body"],
                    }
                )

        return {
            "status": "blocked" if blockers else ("degraded" if degraded_notes else "ready"),
            "project_id": pid,
            "business_id": biz_id,
            "used_chars": used_chars,
            "max_chars": req.max_chars,
            "selected_skills": selected,
            "excluded_skills": excluded,
            "blockers": blockers,
            "degraded_notes": degraded_notes,
        }

    def update_skill_run_status(self, run_id: str, req: SkillRunStatusUpdateRequest) -> dict[str, Any]:
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM sk32_skill_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise BridgeError(404, "skill_run_not_found", "Skill run not found.")
            conn.execute(
                """
                UPDATE sk32_skill_runs
                SET status = ?, adapter = COALESCE(?, adapter), evidence_json = ?, input_tokens = COALESCE(?, input_tokens), measurement_kind = ?
                WHERE id = ?
                """,
                (req.status, req.adapter, json.dumps(redact_payload(req.evidence), ensure_ascii=False), req.input_tokens, req.measurement_kind, run_id),
            )
            return dict(conn.execute("SELECT * FROM sk32_skill_runs WHERE id = ?", (run_id,)).fetchone())

    def list_skill_runs(
        self,
        *,
        project_id: str,
        plan_id: str | None = None,
        task_id: str | None = None,
        attempt_id: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        for col, val in (("plan_id", plan_id), ("task_id", task_id), ("attempt_id", attempt_id), ("session_id", session_id)):
            if val:
                clauses.append(f"{col} = ?")
                args.append(val)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM sk32_skill_runs WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?",
                [*args, min(max(1, limit), 200)],
            ).fetchall()
            return {"items": [{**dict(r), "evidence": json.loads(r["evidence_json"])} for r in rows]}

    # --- P7: Experience (Verified Memory) -> Skill Candidate ---
    def propose_candidate_from_memories(self, req: SkillCandidateProposeRequest, actor: str = "agent") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            bodies: list[str] = []
            mids: list[str] = []
            for mid_uuid in req.memory_ids:
                mid = str(mid_uuid)
                m = conn.execute("SELECT * FROM memories WHERE id = ?", (mid,)).fetchone()
                if not m:
                    raise BridgeError(404, "memory_not_found", f"Memory '{mid}' not found.")
                if m["project_id"] != pid:
                    raise BridgeError(403, "cross_project_memory_denied", "Cannot propose skill from memory belonging to another project.")
                if m["state"] != "verified":
                    raise BridgeError(422, "unverified_memory_denied", f"Memory '{mid}' is state='{m['state']}'; only verified memories can propose skills.")
                mids.append(mid)
                bodies.append(f"## {m['title']}\n- Evidence: {m['evidence_ref'] or 'none'}\n{m['body']}")
            markdown = f"# {req.proposed_title}\n\n" + "\n\n".join(bodies)
            _validate_safe_skill_content(markdown, [])
            manifest = {
                "name": req.proposed_title,
                "description": f"Candidate skill distilled from {len(mids)} verified memories.",
                "version": "1.0.0",
                "tags": req.tags or ["learned"],
                "applicable_capabilities": [],
                "required_mcp_ids": [],
                "optional_mcp_ids": [],
                "required_tool_names": [],
                "permissions": [],
                "references": mids,
            }
            cid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sk32_skill_candidates (
                    id, project_id, worktree_id, target_skill_id, memory_ids_json, proposed_title, proposed_slug,
                    proposed_manifest_json, proposed_markdown, diff_summary, validation_evidence_json, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    cid, pid, str(req.worktree_id) if req.worktree_id else None, req.target_skill_id,
                    json.dumps(mids), req.proposed_title, req.proposed_slug,
                    json.dumps(manifest, ensure_ascii=False), markdown,
                    f"Proposed from {len(mids)} verified memories by {actor}",
                    json.dumps({"memory_ids": mids, "proposed_by": actor}),
                    now, now,
                ),
            )
            row = conn.execute("SELECT * FROM sk32_skill_candidates WHERE id = ?", (cid,)).fetchone()
            return dict(row)

    def list_candidates(self, project_id: str, status: str | None = None) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        if status:
            clauses.append("status = ?")
            args.append(status)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM sk32_skill_candidates WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 100",
                args,
            ).fetchall()
            return {"items": [dict(r) for r in rows]}

    def review_candidate(self, candidate_id: str, req: SkillCandidateReviewRequest, actor: str = "admin") -> dict[str, Any]:
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            c = conn.execute("SELECT * FROM sk32_skill_candidates WHERE id = ?", (candidate_id,)).fetchone()
            if not c:
                raise BridgeError(404, "candidate_not_found", "Skill candidate not found.")
            if c["status"] != "pending":
                raise BridgeError(409, "candidate_already_reviewed", f"Candidate is already '{c['status']}'.")
            if req.decision == "reject":
                conn.execute(
                    "UPDATE sk32_skill_candidates SET status = 'rejected', reviewer = ?, review_note = ?, updated_at = ? WHERE id = ?",
                    (actor, req.note, now, candidate_id),
                )
                return {"id": candidate_id, "status": "rejected", "active_skill_unchanged": True}

            # Approve -> create or add version & publish via P1 rules
            manifest = json.loads(c["proposed_manifest_json"])
            manifest["version"] = req.version
            md = c["proposed_markdown"]
            chash = _content_hash(manifest, md, [])
            sk_id = c["target_skill_id"]
            if not sk_id:
                ex = conn.execute("SELECT id FROM sk32_skills WHERE namespace = ? AND slug = ?", (f"proj-{c['project_id'][:8]}", c["proposed_slug"])).fetchone()
                if ex:
                    sk_id = ex["id"]
                else:
                    sk_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO sk32_skills (
                            id, namespace, slug, title, description, owner, lifecycle, active_version_id,
                            provenance_json, is_starter, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'active', NULL, ?, 0, ?, ?)
                        """,
                        (sk_id, f"proj-{c['project_id'][:8]}", c["proposed_slug"], c["proposed_title"], manifest.get("description", ""), actor, json.dumps({"candidate_id": candidate_id, "memory_ids": json.loads(c["memory_ids_json"])}), now, now),
                    )
            vid = str(uuid.uuid4())
            conn.execute(
                """
                INSERT INTO sk32_skill_versions (
                    id, skill_id, version, content_hash, manifest_json, markdown_body, attachments_json,
                    state, author, approved_by, approved_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, '[]', 'published', ?, ?, ?, ?)
                """,
                (vid, sk_id, req.version, chash, json.dumps(manifest, ensure_ascii=False), md, actor, actor, now, now),
            )
            conn.execute("UPDATE sk32_skills SET active_version_id = ?, updated_at = ? WHERE id = ?", (vid, now, sk_id))
            conn.execute(
                "UPDATE sk32_skill_candidates SET status = 'approved', reviewer = ?, review_note = ?, published_version_id = ?, updated_at = ? WHERE id = ?",
                (actor, req.note, vid, now, candidate_id),
            )
            return {"id": candidate_id, "status": "approved", "skill_id": sk_id, "published_version_id": vid}
