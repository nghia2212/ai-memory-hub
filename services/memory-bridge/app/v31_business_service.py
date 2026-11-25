"""V3.1 P1 — Business Workspace Manager & Asset Registry service."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .v31_schema import (
    AssetRegisterRequest,
    BusinessCreateRequest,
    BusinessDuplicateRequest,
    BusinessFromTemplateRequest,
    BusinessImportRequest,
    BusinessMcpBindRequest,
    BusinessToolPolicyRequest,
    BusinessUpdateRequest,
    CustomTemplateCreateRequest,
)

_SECRET_PATTERN = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z\-_]{30,}|-----BEGIN [A-Z ]+PRIVATE KEY-----)",
    re.IGNORECASE,
)

_SENSITIVE_KEY_RE = re.compile(r"(?:^|_)(?:password|secret|api_key|access_token|private_key|bearer)(?:$|_)", re.IGNORECASE)


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return s or f"biz-{uuid.uuid4().hex[:6]}"


def _reject_raw_secrets(obj: Any, path: str = "root") -> None:
    """Reject raw credentials in business configuration; only secret_refs are permitted."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if _SENSITIVE_KEY_RE.search(kl) and kl not in ("secret_refs", "secret_ref") and isinstance(v, str) and v.strip():
                raise BridgeError(
                    422,
                    "raw_secret_forbidden",
                    f"Raw credential field '{path}.{k}' is forbidden in business config. Use secret_refs instead.",
                )
            _reject_raw_secrets(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _reject_raw_secrets(item, f"{path}[{i}]")
    elif isinstance(obj, str):
        if _SECRET_PATTERN.search(obj):
            raise BridgeError(
                422,
                "raw_secret_forbidden",
                f"Detected raw secret pattern at '{path}'. Credentials must be stored via secret_refs.",
            )


BUILTIN_BUSINESS_TEMPLATES: list[dict[str, Any]] = [
    {
        "template_key": "game-development",
        "name": "Game Development",
        "description": "Phát triển game 2D/3D (Three.js, Phaser, TypeScript, Blender, GLB, Playwright QA, Cloudflare Pages) — mặc định phù hợp Đội Cún Siêu Trí Tuệ.",
        "icon": "gamepad",
        "agent_instructions": (
            "Ưu tiên truy xuất AI Memory & Codebase trước khi đọc file rộng. "
            "Kiểm tra asset GLB/3D và chạy QA trình duyệt ở các viewport mobile/desktop (844x390, 1280x720, 1366x768) trước khi báo hoàn thành. "
            "Không sửa save game thật của người dùng và không deploy production khi chưa được duyệt."
        ),
        "model_policy": {
            "default_model": "gemini-4-neon-medium",
            "fallback_models": ["gemini-4-hydrogen", "claude-sonnet"],
            "allow_paid_api": False,
        },
        "budget_policy": {"max_task_tokens": 120000, "max_daily_cost_usd": 0.0, "context_budget_chars": 12000},
        "memory_policy": {"auto_retrieve_verified": True, "require_review_for_writes": True, "scope": "project_worktree"},
        "workflow_policy": {"require_plan_approval": True, "require_qa_evidence": True, "allow_main_merge": False},
        "resource_policy": {"max_concurrent_mcp": 3, "max_ram_mb": 2048, "idle_shutdown_sec": 300},
        "recommended_capabilities": ["memory_read", "code_search", "git_read", "browser_qa", "glb_inspect", "blender_headless", "image_process", "audio_process", "ts_semantic"],
    },
    {
        "template_key": "technical-architect",
        "name": "Technical Architect",
        "description": "Kiến trúc hệ thống, Java/Banking, Oracle/PostgreSQL/SQL Server metadata, OpenAPI/Swagger, Diagram (Mermaid/PlantUML/C4/ERD), ADR & Observability.",
        "icon": "architecture",
        "agent_instructions": (
            "Luôn phân tích hợp đồng API (OpenAPI), lược đồ database ở chế độ read-only/metadata-only trên môi trường test. "
            "Tuyệt đối không kết nối DB production, không gửi dữ liệu khách hàng/ngân hàng hoặc sơ đồ hệ thống nội bộ lên dịch vụ cloud bên ngoài. "
            "Ghi nhận mọi quyết định kiến trúc thành ADR candidate trong AI Memory."
        ),
        "model_policy": {
            "default_model": "gemini-4-carbon",
            "fallback_models": ["gemini-4-neon-medium", "claude-opus"],
            "allow_paid_api": False,
        },
        "budget_policy": {"max_task_tokens": 160000, "max_daily_cost_usd": 0.0, "context_budget_chars": 16000},
        "memory_policy": {"auto_retrieve_verified": True, "require_review_for_writes": True, "scope": "project_wide"},
        "workflow_policy": {"require_plan_approval": True, "require_adr_candidate": True, "allow_prod_db": False},
        "resource_policy": {"max_concurrent_mcp": 3, "max_ram_mb": 1536, "idle_shutdown_sec": 300},
        "recommended_capabilities": ["memory_read", "code_search", "db_metadata", "openapi_inspect", "diagram_render", "doc_extract", "http_test", "infra_readonly"],
    },
    {
        "template_key": "software-developer",
        "name": "Software Developer",
        "description": "Phát triển phần mềm Java, Spring Boot, TypeScript: phân tích ngữ nghĩa, Maven/Gradle, JUnit, Security scan, Log analysis & Git worktree delivery.",
        "icon": "code",
        "agent_instructions": (
            "Phát triển trong worktree riêng biệt, tuân thủ owned_paths, chạy build & JUnit/pytest thực tế trước khi commit. "
            "Không force push, không merge thẳng vào main. Lọc secret khỏi log trước khi phân tích."
        ),
        "model_policy": {
            "default_model": "gemini-4-neon-medium",
            "fallback_models": ["gemini-4-hydrogen", "codex-cli"],
            "allow_paid_api": False,
        },
        "budget_policy": {"max_task_tokens": 120000, "max_daily_cost_usd": 0.0, "context_budget_chars": 12000},
        "memory_policy": {"auto_retrieve_verified": True, "require_review_for_writes": True, "scope": "project_worktree"},
        "workflow_policy": {"require_plan_approval": True, "require_tests_passed": True, "allow_main_merge": False},
        "resource_policy": {"max_concurrent_mcp": 3, "max_ram_mb": 2048, "idle_shutdown_sec": 300},
        "recommended_capabilities": ["memory_read", "code_search", "git_read", "java_semantic", "ts_semantic", "maven_build", "junit_test", "security_scan", "log_analyze"],
    },
]


class V31BusinessService:
    """Manages Business Workspaces, templates, versioning, project/MCP/tool bindings, and Asset Registry."""

    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._initialized = False

    def ensure_initial_seed(self) -> None:
        """Seed the 3 initial business templates ONCE; never re-create businesses deleted/archived by user."""
        if self._initialized:
            return
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            # Ensure built-in template catalog rows exist
            for tpl in BUILTIN_BUSINESS_TEMPLATES:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO v31_business_templates (
                        id, template_key, name, description, icon, is_builtin, payload_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        f"tpl-{tpl['template_key']}",
                        tpl["template_key"],
                        tpl["name"],
                        tpl["description"],
                        tpl["icon"],
                        json.dumps(tpl, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
            # Check one-time seed flag so user deletions/archives are permanently respected across restarts
            seeded = conn.execute(
                "SELECT value FROM v31_business_meta WHERE key = 'initial_businesses_seeded'"
            ).fetchone()
            if not seeded:
                for tpl in BUILTIN_BUSINESS_TEMPLATES:
                    exists = conn.execute(
                        "SELECT id FROM v31_business_profiles WHERE name = ? OR slug = ?",
                        (tpl["name"], tpl["template_key"]),
                    ).fetchone()
                    if exists:
                        continue
                    bid = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO v31_business_profiles (
                            id, name, slug, description, icon, enabled, archived, revision,
                            template_key, agent_instructions, model_policy_json, budget_policy_json,
                            memory_policy_json, workflow_policy_json, resource_policy_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            bid,
                            tpl["name"],
                            tpl["template_key"],
                            tpl["description"],
                            tpl["icon"],
                            tpl["template_key"],
                            tpl["agent_instructions"],
                            json.dumps(tpl["model_policy"], ensure_ascii=False),
                            json.dumps(tpl["budget_policy"], ensure_ascii=False),
                            json.dumps(tpl["memory_policy"], ensure_ascii=False),
                            json.dumps(tpl["workflow_policy"], ensure_ascii=False),
                            json.dumps(tpl["resource_policy"], ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                    # Bind builtin MCP servers if already present
                    builtin_srvs = conn.execute("SELECT id FROM v3_mcp_servers WHERE trust_tier = 'builtin'").fetchall()
                    for bs in builtin_srvs:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO v31_business_mcp_bindings (
                                business_id, server_id, enabled, required, priority, config_json, created_at, updated_at
                            ) VALUES (?, ?, 1, 0, 10, '{}', ?, ?)
                            """,
                            (bid, bs["id"], now, now),
                        )
                    self._save_version_snapshot(conn, bid, 1, now)
                conn.execute(
                    "INSERT INTO v31_business_meta (key, value, updated_at) VALUES ('initial_businesses_seeded', '1', ?)",
                    (now,),
                )
        self._initialized = True

    def _build_snapshot(self, conn: Any, business_id: str) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
        if not row:
            raise BridgeError(404, "business_not_found", "Business workspace not found.")
        projects = [
            r["project_id"]
            for r in conn.execute("SELECT project_id FROM v31_business_projects WHERE business_id = ? ORDER BY project_id", (business_id,)).fetchall()
        ]
        bindings = [
            {
                "server_id": r["server_id"],
                "enabled": bool(r["enabled"]),
                "required": bool(r["required"]),
                "priority": r["priority"],
                "config": json.loads(r["config_json"]),
            }
            for r in conn.execute(
                "SELECT * FROM v31_business_mcp_bindings WHERE business_id = ? ORDER BY priority, server_id",
                (business_id,),
            ).fetchall()
        ]
        tool_policies = [
            {"tool_id": r["tool_id"], "policy": r["policy"]}
            for r in conn.execute("SELECT tool_id, policy FROM v31_business_tools WHERE business_id = ? ORDER BY tool_id", (business_id,)).fetchall()
        ]
        return {
            "id": row["id"],
            "name": row["name"],
            "slug": row["slug"],
            "description": row["description"],
            "icon": row["icon"],
            "enabled": bool(row["enabled"]),
            "archived": bool(row["archived"]),
            "revision": row["revision"],
            "template_key": row["template_key"],
            "agent_instructions": row["agent_instructions"],
            "model_policy": json.loads(row["model_policy_json"] or "{}"),
            "budget_policy": json.loads(row["budget_policy_json"] or "{}"),
            "memory_policy": json.loads(row["memory_policy_json"] or "{}"),
            "workflow_policy": json.loads(row["workflow_policy_json"] or "{}"),
            "resource_policy": json.loads(row["resource_policy_json"] or "{}"),
            "project_ids": projects,
            "mcp_bindings": bindings,
            "tool_policies": tool_policies,
        }

    def _save_version_snapshot(self, conn: Any, business_id: str, revision: int, now: str) -> None:
        snap = self._build_snapshot(conn, business_id)
        conn.execute(
            """
            INSERT OR REPLACE INTO v31_business_versions (business_id, revision, snapshot_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (business_id, revision, json.dumps(snap, ensure_ascii=False), now),
        )

    def _bump_revision(self, conn: Any, business_id: str, now: str) -> int:
        row = conn.execute("SELECT revision FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
        if not row:
            raise BridgeError(404, "business_not_found", "Business workspace not found.")
        new_rev = int(row["revision"]) + 1
        conn.execute(
            "UPDATE v31_business_profiles SET revision = ?, updated_at = ? WHERE id = ?",
            (new_rev, now, business_id),
        )
        self._save_version_snapshot(conn, business_id, new_rev, now)
        return new_rev

    # --- CRUD ---
    def list_businesses(
        self,
        *,
        search: str = "",
        include_archived: bool = False,
        enabled_only: bool = False,
    ) -> dict[str, Any]:
        self.ensure_initial_seed()
        clauses = ["1=1"]
        args: list[Any] = []
        if not include_archived:
            clauses.append("b.archived = 0")
        if enabled_only:
            clauses.append("b.enabled = 1")
        if search.strip():
            clauses.append("(b.name LIKE ? OR b.description LIKE ? OR COALESCE(b.slug, '') LIKE ?)")
            pat = f"%{search.strip()}%"
            args.extend([pat, pat, pat])
        where = " AND ".join(clauses)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT b.* FROM v31_business_profiles b WHERE {where} ORDER BY b.archived ASC, b.updated_at DESC",
                args,
            ).fetchall()
            items: list[dict[str, Any]] = []
            for r in rows:
                bid = r["id"]
                proj_cnt = conn.execute("SELECT COUNT(*) FROM v31_business_projects WHERE business_id = ?", (bid,)).fetchone()[0]
                mcp_rows = conn.execute(
                    """
                    SELECT m.enabled AS bind_enabled, s.health_status, s.approval_state
                    FROM v31_business_mcp_bindings m
                    JOIN v3_mcp_servers s ON s.id = m.server_id
                    WHERE m.business_id = ?
                    """,
                    (bid,),
                ).fetchall()
                mcp_cfg = len(mcp_rows)
                mcp_online = sum(1 for x in mcp_rows if x["bind_enabled"] and x["health_status"] == "ready" and x["approval_state"] == "approved")
                mcp_offline = sum(1 for x in mcp_rows if x["bind_enabled"] and x["health_status"] in ("offline", "timeout", "auth_failed", "down"))
                model_pol = json.loads(r["model_policy_json"] or "{}")
                items.append(
                    {
                        "id": bid,
                        "name": r["name"],
                        "slug": r["slug"],
                        "description": r["description"],
                        "icon": r["icon"],
                        "enabled": bool(r["enabled"]),
                        "archived": bool(r["archived"]),
                        "revision": r["revision"],
                        "template_key": r["template_key"],
                        "default_model": model_pol.get("default_model") or "Chưa đặt",
                        "project_count": proj_cnt,
                        "mcp_configured_count": mcp_cfg,
                        "mcp_online_count": mcp_online,
                        "mcp_offline_count": mcp_offline,
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                )
            return {"items": items, "total": len(items)}

    def get_business_detail(self, business_id: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.read_conn() as conn:
            row = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            projects = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT p.id AS project_id, p.name, p.canonical_identity
                    FROM v31_business_projects bp
                    JOIN projects p ON p.id = bp.project_id
                    WHERE bp.business_id = ?
                    ORDER BY p.name
                    """,
                    (business_id,),
                ).fetchall()
            ]
            bindings_raw = conn.execute(
                """
                SELECT bm.*, s.name AS server_name, s.transport, s.health_status, s.approval_state, s.trust_tier,
                       COALESCE(ext.adapter_transport, s.transport) AS adapter_transport,
                       COALESCE(ext.enabled, 1) AS server_enabled
                FROM v31_business_mcp_bindings bm
                JOIN v3_mcp_servers s ON s.id = bm.server_id
                LEFT JOIN v31_mcp_server_ext ext ON ext.server_id = s.id
                WHERE bm.business_id = ?
                ORDER BY bm.priority ASC, s.name ASC
                """,
                (business_id,),
            ).fetchall()
            tool_policies_map = {
                r["tool_id"]: r["policy"]
                for r in conn.execute("SELECT tool_id, policy FROM v31_business_tools WHERE business_id = ?", (business_id,)).fetchall()
            }
            bindings = []
            for b in bindings_raw:
                srv_tools = conn.execute(
                    "SELECT id, tool_name, description, side_effect_level, requires_approval, capability_tags_json FROM v3_mcp_tools WHERE server_id = ? ORDER BY tool_name",
                    (b["server_id"],),
                ).fetchall()
                tools_out = []
                for t in srv_tools:
                    tools_out.append(
                        {
                            "tool_id": t["id"],
                            "tool_name": t["tool_name"],
                            "description": t["description"],
                            "side_effect_level": t["side_effect_level"],
                            "requires_approval": bool(t["requires_approval"]),
                            "capability_tags": json.loads(t["capability_tags_json"] or "[]"),
                            "policy": tool_policies_map.get(t["id"], "deny"),  # default-deny when unset
                        }
                    )
                bindings.append(
                    {
                        "server_id": b["server_id"],
                        "server_name": b["server_name"],
                        "transport": b["adapter_transport"],
                        "health_status": b["health_status"],
                        "approval_state": b["approval_state"],
                        "trust_tier": b["trust_tier"],
                        "server_enabled": bool(b["server_enabled"]),
                        "enabled": bool(b["enabled"]),
                        "required": bool(b["required"]),
                        "priority": b["priority"],
                        "config": json.loads(b["config_json"] or "{}"),
                        "tools": tools_out,
                    }
                )
            active_sessions = [
                dict(r)
                for r in conn.execute(
                    """
                    SELECT DISTINCT snap.session_id, snap.project_id, s.status, s.model_id, s.started_at
                    FROM v31_task_policy_snapshots snap
                    JOIN v2_sessions s ON s.id = snap.session_id
                    WHERE snap.business_id = ? AND s.status = 'running'
                    """,
                    (business_id,),
                ).fetchall()
            ]
            ver_count = conn.execute("SELECT COUNT(*) FROM v31_business_versions WHERE business_id = ?", (business_id,)).fetchone()[0]
            return {
                "id": row["id"],
                "name": row["name"],
                "slug": row["slug"],
                "description": row["description"],
                "icon": row["icon"],
                "enabled": bool(row["enabled"]),
                "archived": bool(row["archived"]),
                "revision": row["revision"],
                "template_key": row["template_key"],
                "agent_instructions": row["agent_instructions"],
                "model_policy": json.loads(row["model_policy_json"] or "{}"),
                "budget_policy": json.loads(row["budget_policy_json"] or "{}"),
                "memory_policy": json.loads(row["memory_policy_json"] or "{}"),
                "workflow_policy": json.loads(row["workflow_policy_json"] or "{}"),
                "resource_policy": json.loads(row["resource_policy_json"] or "{}"),
                "projects": projects,
                "mcp_bindings": bindings,
                "active_sessions": active_sessions,
                "version_count": ver_count,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def create_business(self, req: BusinessCreateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        _reject_raw_secrets(req.model_dump())
        now = utc_now_iso()
        bid = str(uuid.uuid4())
        slug = _slugify(req.slug or req.name)
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM v31_business_profiles WHERE name = ?", (req.name.strip(),)).fetchone():
                raise BridgeError(409, "business_exists", f"Business '{req.name}' already exists.")
            if conn.execute("SELECT 1 FROM v31_business_profiles WHERE slug = ?", (slug,)).fetchone():
                slug = f"{slug}-{bid[:6]}"
            conn.execute(
                """
                INSERT INTO v31_business_profiles (
                    id, name, slug, description, icon, enabled, archived, revision,
                    template_key, agent_instructions, model_policy_json, budget_policy_json,
                    memory_policy_json, workflow_policy_json, resource_policy_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bid,
                    req.name.strip(),
                    slug,
                    req.description.strip(),
                    req.icon.strip() or "briefcase",
                    1 if req.enabled else 0,
                    req.template_key,
                    req.agent_instructions,
                    json.dumps(req.model_policy, ensure_ascii=False),
                    json.dumps(req.budget_policy, ensure_ascii=False),
                    json.dumps(req.memory_policy, ensure_ascii=False),
                    json.dumps(req.workflow_policy, ensure_ascii=False),
                    json.dumps(req.resource_policy, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            for pid in req.project_ids:
                if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                    raise BridgeError(404, "project_not_found", f"Project '{pid}' not found.")
                conn.execute("INSERT OR IGNORE INTO v31_business_projects (business_id, project_id) VALUES (?, ?)", (bid, pid))
            self._save_version_snapshot(conn, bid, 1, now)
        return self.get_business_detail(bid)

    def update_business(self, business_id: str, req: BusinessUpdateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        _reject_raw_secrets(req.model_dump(exclude_none=True))
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            updates: dict[str, Any] = {}
            if req.name is not None:
                dup = conn.execute("SELECT id FROM v31_business_profiles WHERE name = ? AND id != ?", (req.name.strip(), business_id)).fetchone()
                if dup:
                    raise BridgeError(409, "business_exists", f"Business '{req.name}' already exists.")
                updates["name"] = req.name.strip()
            if req.slug is not None:
                updates["slug"] = _slugify(req.slug)
            if req.description is not None:
                updates["description"] = req.description.strip()
            if req.icon is not None:
                updates["icon"] = req.icon.strip() or "briefcase"
            if req.enabled is not None:
                updates["enabled"] = 1 if req.enabled else 0
            if req.agent_instructions is not None:
                updates["agent_instructions"] = req.agent_instructions
            if req.model_policy is not None:
                updates["model_policy_json"] = json.dumps(req.model_policy, ensure_ascii=False)
            if req.budget_policy is not None:
                updates["budget_policy_json"] = json.dumps(req.budget_policy, ensure_ascii=False)
            if req.memory_policy is not None:
                updates["memory_policy_json"] = json.dumps(req.memory_policy, ensure_ascii=False)
            if req.workflow_policy is not None:
                updates["workflow_policy_json"] = json.dumps(req.workflow_policy, ensure_ascii=False)
            if req.resource_policy is not None:
                updates["resource_policy_json"] = json.dumps(req.resource_policy, ensure_ascii=False)
            if updates:
                set_clause = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(
                    f"UPDATE v31_business_profiles SET {set_clause}, updated_at = ? WHERE id = ?",
                    [*updates.values(), now, business_id],
                )
                self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def archive_business(self, business_id: str, actor: str = "admin") -> dict[str, Any]:
        """Soft-archive business; NEVER deletes MCP servers, projects, memories, conversations, or running sessions."""
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            linked_projects = conn.execute("SELECT COUNT(*) FROM v31_business_projects WHERE business_id = ?", (business_id,)).fetchone()[0]
            mcp_bindings = conn.execute("SELECT COUNT(*) FROM v31_business_mcp_bindings WHERE business_id = ?", (business_id,)).fetchone()[0]
            active_sessions = conn.execute(
                """
                SELECT COUNT(DISTINCT snap.session_id)
                FROM v31_task_policy_snapshots snap
                JOIN v2_sessions s ON s.id = snap.session_id
                WHERE snap.business_id = ? AND s.status = 'running'
                """,
                (business_id,),
            ).fetchone()[0]
            conn.execute(
                "UPDATE v31_business_profiles SET archived = 1, enabled = 0, updated_at = ? WHERE id = ?",
                (now, business_id),
            )
            rev = self._bump_revision(conn, business_id, now)
        return {
            "id": business_id,
            "archived": True,
            "enabled": False,
            "revision": rev,
            "preserved": {
                "mcp_registry_untouched": True,
                "projects_untouched": True,
                "memories_untouched": True,
                "conversations_untouched": True,
                "traces_untouched": True,
            },
            "dependencies": {
                "linked_projects": linked_projects,
                "mcp_bindings": mcp_bindings,
                "active_running_sessions": active_sessions,
            },
        }

    def restore_business(self, business_id: str, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT id FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
            if not row:
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            conn.execute(
                "UPDATE v31_business_profiles SET archived = 0, enabled = 1, updated_at = ? WHERE id = ?",
                (now, business_id),
            )
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def duplicate_business(self, business_id: str, req: BusinessDuplicateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        new_id = str(uuid.uuid4())
        new_slug = _slugify(req.new_name)
        with self.db.write_tx() as conn:
            src = conn.execute("SELECT * FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone()
            if not src:
                raise BridgeError(404, "business_not_found", "Source business workspace not found.")
            if conn.execute("SELECT 1 FROM v31_business_profiles WHERE name = ?", (req.new_name.strip(),)).fetchone():
                raise BridgeError(409, "business_exists", f"Business '{req.new_name}' already exists.")
            if conn.execute("SELECT 1 FROM v31_business_profiles WHERE slug = ?", (new_slug,)).fetchone():
                new_slug = f"{new_slug}-{new_id[:6]}"
            conn.execute(
                """
                INSERT INTO v31_business_profiles (
                    id, name, slug, description, icon, enabled, archived, revision,
                    template_key, agent_instructions, model_policy_json, budget_policy_json,
                    memory_policy_json, workflow_policy_json, resource_policy_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, 0, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    new_id, req.new_name.strip(), new_slug, src["description"], src["icon"],
                    src["template_key"], src["agent_instructions"], src["model_policy_json"],
                    src["budget_policy_json"], src["memory_policy_json"], src["workflow_policy_json"],
                    src["resource_policy_json"], now, now,
                ),
            )
            for p in conn.execute("SELECT project_id FROM v31_business_projects WHERE business_id = ?", (business_id,)).fetchall():
                conn.execute("INSERT INTO v31_business_projects (business_id, project_id) VALUES (?, ?)", (new_id, p["project_id"]))
            for m in conn.execute("SELECT * FROM v31_business_mcp_bindings WHERE business_id = ?", (business_id,)).fetchall():
                conn.execute(
                    """
                    INSERT INTO v31_business_mcp_bindings (business_id, server_id, enabled, required, priority, config_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_id, m["server_id"], m["enabled"], m["required"], m["priority"], m["config_json"], now, now),
                )
            for t in conn.execute("SELECT tool_id, policy FROM v31_business_tools WHERE business_id = ?", (business_id,)).fetchall():
                conn.execute("INSERT INTO v31_business_tools (business_id, tool_id, policy) VALUES (?, ?, ?)", (new_id, t["tool_id"], t["policy"]))
            self._save_version_snapshot(conn, new_id, 1, now)
        return self.get_business_detail(new_id)

    def export_business(self, business_id: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.read_conn() as conn:
            snap = self._build_snapshot(conn, business_id)
            return {
                "schema": "ai-memory-hub-v31-business-export",
                "exported_at": utc_now_iso(),
                "business": redact_payload(snap),
            }

    def import_business(self, req: BusinessImportRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        _reject_raw_secrets(req.payload)
        raw = req.payload.get("business", req.payload)
        name = (req.new_name or str(raw.get("name") or "")).strip()
        if len(name) < 2:
            raise BridgeError(422, "invalid_import", "Imported business requires a valid name (>= 2 chars).")
        create_req = BusinessCreateRequest(
            name=name,
            description=str(raw.get("description") or ""),
            icon=str(raw.get("icon") or "briefcase"),
            enabled=bool(raw.get("enabled", True)),
            template_key=raw.get("template_key"),
            agent_instructions=str(raw.get("agent_instructions") or ""),
            model_policy=dict(raw.get("model_policy") or {}),
            budget_policy=dict(raw.get("budget_policy") or {}),
            memory_policy=dict(raw.get("memory_policy") or {}),
            workflow_policy=dict(raw.get("workflow_policy") or {}),
            resource_policy=dict(raw.get("resource_policy") or {}),
            project_ids=[],
        )
        created = self.create_business(create_req, actor=actor)
        bid = created["id"]
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            for pid in raw.get("project_ids") or []:
                if conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO v31_business_projects (business_id, project_id) VALUES (?, ?)", (bid, pid))
            for b in raw.get("mcp_bindings") or []:
                sid = b.get("server_id")
                if sid and conn.execute("SELECT 1 FROM v3_mcp_servers WHERE id = ?", (sid,)).fetchone():
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO v31_business_mcp_bindings (
                            business_id, server_id, enabled, required, priority, config_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (bid, sid, 1 if b.get("enabled", True) else 0, 1 if b.get("required", False) else 0, int(b.get("priority", 100)), json.dumps(b.get("config") or {}), now, now),
                    )
            for tp in raw.get("tool_policies") or []:
                tid = tp.get("tool_id")
                pol = tp.get("policy")
                if tid and pol in ("allow", "approval", "deny") and conn.execute("SELECT 1 FROM v3_mcp_tools WHERE id = ?", (tid,)).fetchone():
                    conn.execute("INSERT OR REPLACE INTO v31_business_tools (business_id, tool_id, policy) VALUES (?, ?, ?)", (bid, tid, pol))
            self._bump_revision(conn, bid, now)
        return self.get_business_detail(bid)

    def list_versions(self, business_id: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.read_conn() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            rows = conn.execute(
                "SELECT revision, snapshot_json, created_at FROM v31_business_versions WHERE business_id = ? ORDER BY revision DESC",
                (business_id,),
            ).fetchall()
            return {
                "business_id": business_id,
                "items": [{"revision": r["revision"], "created_at": r["created_at"], "snapshot": json.loads(r["snapshot_json"])} for r in rows],
            }

    def restore_version(self, business_id: str, target_revision: int, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            ver = conn.execute(
                "SELECT snapshot_json FROM v31_business_versions WHERE business_id = ? AND revision = ?",
                (business_id, target_revision),
            ).fetchone()
            if not ver:
                raise BridgeError(404, "version_not_found", f"Revision {target_revision} not found for business.")
            snap = json.loads(ver["snapshot_json"])
            conn.execute(
                """
                UPDATE v31_business_profiles
                SET name = ?, description = ?, icon = ?, enabled = ?, archived = ?,
                    agent_instructions = ?, model_policy_json = ?, budget_policy_json = ?,
                    memory_policy_json = ?, workflow_policy_json = ?, resource_policy_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    snap["name"], snap.get("description", ""), snap.get("icon", "briefcase"),
                    1 if snap.get("enabled", True) else 0, 1 if snap.get("archived", False) else 0,
                    snap.get("agent_instructions", ""),
                    json.dumps(snap.get("model_policy") or {}, ensure_ascii=False),
                    json.dumps(snap.get("budget_policy") or {}, ensure_ascii=False),
                    json.dumps(snap.get("memory_policy") or {}, ensure_ascii=False),
                    json.dumps(snap.get("workflow_policy") or {}, ensure_ascii=False),
                    json.dumps(snap.get("resource_policy") or {}, ensure_ascii=False),
                    now, business_id,
                ),
            )
            conn.execute("DELETE FROM v31_business_projects WHERE business_id = ?", (business_id,))
            for pid in snap.get("project_ids") or []:
                if conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                    conn.execute("INSERT OR IGNORE INTO v31_business_projects (business_id, project_id) VALUES (?, ?)", (business_id, pid))
            conn.execute("DELETE FROM v31_business_mcp_bindings WHERE business_id = ?", (business_id,))
            for b in snap.get("mcp_bindings") or []:
                if conn.execute("SELECT 1 FROM v3_mcp_servers WHERE id = ?", (b["server_id"],)).fetchone():
                    conn.execute(
                        """
                        INSERT INTO v31_business_mcp_bindings (business_id, server_id, enabled, required, priority, config_json, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (business_id, b["server_id"], 1 if b.get("enabled", True) else 0, 1 if b.get("required", False) else 0, int(b.get("priority", 100)), json.dumps(b.get("config") or {}), now, now),
                    )
            conn.execute("DELETE FROM v31_business_tools WHERE business_id = ?", (business_id,))
            for tp in snap.get("tool_policies") or []:
                if conn.execute("SELECT 1 FROM v3_mcp_tools WHERE id = ?", (tp["tool_id"],)).fetchone():
                    conn.execute("INSERT INTO v31_business_tools (business_id, tool_id, policy) VALUES (?, ?, ?)", (business_id, tp["tool_id"], tp["policy"]))
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    # --- Project, MCP & Tool bindings ---
    def attach_project(self, business_id: str, project_id: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            conn.execute("INSERT OR IGNORE INTO v31_business_projects (business_id, project_id) VALUES (?, ?)", (business_id, project_id))
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def detach_project(self, business_id: str, project_id: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            conn.execute("DELETE FROM v31_business_projects WHERE business_id = ? AND project_id = ?", (business_id, project_id))
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def bind_mcp(self, business_id: str, req: BusinessMcpBindRequest) -> dict[str, Any]:
        self.ensure_initial_seed()
        _reject_raw_secrets(req.config)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            if not conn.execute("SELECT 1 FROM v3_mcp_servers WHERE id = ?", (req.server_id,)).fetchone():
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            conn.execute(
                """
                INSERT INTO v31_business_mcp_bindings (
                    business_id, server_id, enabled, required, priority, config_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(business_id, server_id) DO UPDATE SET
                    enabled = excluded.enabled,
                    required = excluded.required,
                    priority = excluded.priority,
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (business_id, req.server_id, 1 if req.enabled else 0, 1 if req.required else 0, req.priority, json.dumps(req.config, ensure_ascii=False), now, now),
            )
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def unbind_mcp(self, business_id: str, server_id: str) -> dict[str, Any]:
        """Unbind MCP from ONE business only; never affects other businesses or global MCP registry."""
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            conn.execute("DELETE FROM v31_business_mcp_bindings WHERE business_id = ? AND server_id = ?", (business_id, server_id))
            # Also remove tool overrides for this business+server only
            conn.execute(
                "DELETE FROM v31_business_tools WHERE business_id = ? AND tool_id IN (SELECT id FROM v3_mcp_tools WHERE server_id = ?)",
                (business_id, server_id),
            )
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    def set_tool_policy(self, business_id: str, req: BusinessToolPolicyRequest) -> dict[str, Any]:
        self.ensure_initial_seed()
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            if not conn.execute("SELECT 1 FROM v3_mcp_tools WHERE id = ?", (req.tool_id,)).fetchone():
                raise BridgeError(404, "mcp_tool_not_found", "MCP tool not found.")
            conn.execute(
                """
                INSERT INTO v31_business_tools (business_id, tool_id, policy) VALUES (?, ?, ?)
                ON CONFLICT(business_id, tool_id) DO UPDATE SET policy = excluded.policy
                """,
                (business_id, req.tool_id, req.policy),
            )
            self._bump_revision(conn, business_id, now)
        return self.get_business_detail(business_id)

    # --- Templates ---
    def list_templates(self) -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.read_conn() as conn:
            rows = conn.execute("SELECT * FROM v31_business_templates ORDER BY is_builtin DESC, name ASC").fetchall()
            return {
                "items": [
                    {
                        "id": r["id"],
                        "template_key": r["template_key"],
                        "name": r["name"],
                        "description": r["description"],
                        "icon": r["icon"],
                        "is_builtin": bool(r["is_builtin"]),
                        "payload": json.loads(r["payload_json"]),
                    }
                    for r in rows
                ]
            }

    def create_from_template(self, req: BusinessFromTemplateRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.read_conn() as conn:
            row = conn.execute("SELECT * FROM v31_business_templates WHERE template_key = ?", (req.template_key,)).fetchone()
            if not row:
                raise BridgeError(404, "template_not_found", f"Template '{req.template_key}' not found.")
            tpl = json.loads(row["payload_json"])
        name = (req.name or f"{tpl['name']} ({uuid.uuid4().hex[:4]})").strip()
        return self.create_business(
            BusinessCreateRequest(
                name=name,
                description=tpl.get("description", ""),
                icon=tpl.get("icon", "briefcase"),
                template_key=req.template_key,
                agent_instructions=tpl.get("agent_instructions", ""),
                model_policy=tpl.get("model_policy", {}),
                budget_policy=tpl.get("budget_policy", {}),
                memory_policy=tpl.get("memory_policy", {}),
                workflow_policy=tpl.get("workflow_policy", {}),
                resource_policy=tpl.get("resource_policy", {}),
            ),
            actor=actor,
        )

    def create_custom_template(self, req: CustomTemplateCreateRequest) -> dict[str, Any]:
        self.ensure_initial_seed()
        _reject_raw_secrets(req.payload)
        now = utc_now_iso()
        tid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM v31_business_templates WHERE template_key = ?", (req.template_key,)).fetchone():
                raise BridgeError(409, "template_exists", f"Template '{req.template_key}' already exists.")
            conn.execute(
                """
                INSERT INTO v31_business_templates (id, template_key, name, description, icon, is_builtin, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (tid, req.template_key, req.name, req.description, req.icon, json.dumps(req.payload, ensure_ascii=False), now, now),
            )
        return {"id": tid, "template_key": req.template_key, "name": req.name, "is_builtin": False}

    def delete_custom_template(self, template_key: str) -> dict[str, Any]:
        self.ensure_initial_seed()
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT id, is_builtin FROM v31_business_templates WHERE template_key = ?", (template_key,)).fetchone()
            if not row:
                raise BridgeError(404, "template_not_found", "Template not found.")
            if row["is_builtin"]:
                raise BridgeError(403, "builtin_template_protected", "Cannot delete built-in template; delete or archive businesses created from it instead.")
            conn.execute("DELETE FROM v31_business_templates WHERE template_key = ?", (template_key,))
        return {"deleted": True, "template_key": template_key}

    # --- P6.5 Asset Registry ---
    def register_asset(self, req: AssetRegisterRequest) -> dict[str, Any]:
        now = utc_now_iso()
        aid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (str(req.project_id),)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            conn.execute(
                """
                INSERT INTO v31_asset_registry (
                    id, project_id, source, license, file_path, file_hash, format, version,
                    dependencies_json, thumbnail_path, metadata_json, approval_state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    aid, str(req.project_id), req.source, req.license, req.file_path, req.file_hash,
                    req.format.lower(), req.version, json.dumps(req.dependencies), req.thumbnail_path,
                    json.dumps(redact_payload(req.metadata), ensure_ascii=False), req.approval_state, now, now,
                ),
            )
        return {"id": aid, "project_id": str(req.project_id), "format": req.format.lower(), "approval_state": req.approval_state}

    def list_assets(self, project_id: str, fmt: str | None = None) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        if fmt:
            clauses.append("format = ?")
            args.append(fmt.lower())
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM v31_asset_registry WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 200",
                args,
            ).fetchall()
            return {
                "items": [
                    {
                        **dict(r),
                        "dependencies": json.loads(r["dependencies_json"]),
                        "metadata": json.loads(r["metadata_json"]),
                    }
                    for r in rows
                ]
            }
