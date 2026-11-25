"""V3.2.1 (MCP321) — MCP Candidate Catalog Service & External Plan/Task Sync Registry (Monitor-Only)."""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .v3_schema import McpApprovalRequest
from .v31_mcp_gateway import V31McpGatewayService
from .v31_schema import McpGatewayServerCreateRequest
from .v321_schema import (
    CatalogRegisterRequest,
    ExternalPlanIngestRequest,
    ProjectMcpOverrideRequest,
    SessionToMonitoringPlanRequest,
    TraceAssociationRequest,
)

# Verified candidate MCP catalog entries (separate from registered/approved/connected servers!)
BUILTIN_CATALOG_ENTRIES: list[dict[str, Any]] = [
    # --- Built-in Local Deterministic Packs (ready when registered via Hub Gateway) ---
    {
        "catalog_key": "pack-shared",
        "name": "pack-shared (Git / Safe FS / Docs)",
        "category": "builtin_pack",
        "description": "Built-in local deterministic Shared Pack: read-only Git, bounded Safe Filesystem (blocks .env/..), official docs index.",
        "source_repo": "services/orchestrator/tool_packs/shared_pack.py",
        "version": "3.2.1",
        "license": "MIT",
        "install_method": "built-in (0 external deps)",
        "transport": "local_tool",
        "default_endpoint_or_cmd": "local://pack-shared",
        "default_args": [],
        "capabilities": ["git_read", "fs_read", "docs_lookup", "github_read"],
        "auth_requirements": [],
        "network_destinations": [],
        "risk_level": "low",
        "resource_estimate": "<20MB RAM",
        "recommended_businesses": ["Game Development", "Technical Architect", "Software Developer"],
    },
    {
        "catalog_key": "pack-game",
        "name": "pack-game (GLB / Blender / Image / Audio)",
        "category": "game",
        "description": "Built-in local deterministic Game Pack: binary GLB 2.0 inspector, Blender headless detector, Image/Audio validator.",
        "source_repo": "services/orchestrator/tool_packs/game_pack.py",
        "version": "3.2.1",
        "license": "MIT",
        "install_method": "built-in + optional Blender/FFmpeg on host",
        "transport": "local_tool",
        "default_endpoint_or_cmd": "local://pack-game",
        "default_args": [],
        "capabilities": ["glb_inspect", "blender_headless", "image_process", "audio_process", "cloudflare_preview", "android_qa"],
        "auth_requirements": [],
        "network_destinations": [],
        "risk_level": "low",
        "resource_estimate": "<40MB RAM",
        "recommended_businesses": ["Game Development"],
    },
    {
        "catalog_key": "pack-architect",
        "name": "pack-architect (DB-Meta / OpenAPI / HTTP / Diagram / Doc)",
        "category": "architect",
        "description": "Built-in local deterministic Architect Pack: read-only DB metadata (SQLite live; Oracle/PG/MSSQL test-DSN gated), OpenAPI 3.x breaking diff, local Mermaid/PlantUML validator, allowlisted HTTP tester.",
        "source_repo": "services/orchestrator/tool_packs/architect_pack.py",
        "version": "3.2.1",
        "license": "MIT",
        "install_method": "built-in (0 external deps)",
        "transport": "local_tool",
        "default_endpoint_or_cmd": "local://pack-architect",
        "default_args": [],
        "capabilities": ["db_metadata", "openapi_inspect", "http_test", "diagram_render", "doc_extract", "infra_readonly"],
        "auth_requirements": ["Optional TEST_ORACLE_DSN / TEST_POSTGRES_DSN (read-only test only)"],
        "network_destinations": ["allowlisted_only"],
        "risk_level": "low",
        "resource_estimate": "<30MB RAM",
        "recommended_businesses": ["Technical Architect"],
    },
    {
        "catalog_key": "pack-developer",
        "name": "pack-developer (Java / TS / Maven / JUnit / Security / Log)",
        "category": "developer",
        "description": "Built-in local deterministic Developer Pack: Java structural/Spring analyzer, TypeScript analyzer, Maven pom.xml & JUnit XML parser, offline secret scanner, log analyzer.",
        "source_repo": "services/orchestrator/tool_packs/developer_pack.py",
        "version": "3.2.1",
        "license": "MIT",
        "install_method": "built-in + optional Java/Maven/Node on host",
        "transport": "local_tool",
        "default_endpoint_or_cmd": "local://pack-developer",
        "default_args": [],
        "capabilities": ["java_semantic", "ts_semantic", "maven_build", "junit_test", "security_scan", "log_analyze"],
        "auth_requirements": [],
        "network_destinations": [],
        "risk_level": "low",
        "resource_estimate": "<50MB RAM",
        "recommended_businesses": ["Software Developer", "Game Development"],
    },
    # --- External Priority Candidates (require explicit install/config/credentials — NEVER auto-installed) ---
    {
        "catalog_key": "github-mcp",
        "name": "GitHub Official MCP Server",
        "category": "common",
        "description": "Official GitHub MCP server for repos, issues, PRs, reviews and workflows (read-only/scoped token by default).",
        "source_repo": "https://github.com/github/github-mcp-server",
        "version": "official-release",
        "license": "MIT",
        "install_method": "docker or binary stdio (requires user approval & GITHUB_PERSONAL_ACCESS_TOKEN)",
        "transport": "stdio",
        "default_endpoint_or_cmd": "github-mcp-server",
        "default_args": ["stdio"],
        "capabilities": ["github_read", "pr_review", "issue_track"],
        "auth_requirements": ["GITHUB_PERSONAL_ACCESS_TOKEN"],
        "network_destinations": ["api.github.com"],
        "risk_level": "medium",
        "resource_estimate": "~60MB RAM",
        "recommended_businesses": ["Game Development", "Technical Architect", "Software Developer"],
    },
    {
        "catalog_key": "context7-mcp",
        "name": "Context7 Documentation MCP",
        "category": "common",
        "description": "Up-to-date versioned library documentation lookup via Context7 MCP (free tier / rate-limited).",
        "source_repo": "https://github.com/upstash/context7",
        "version": "@upstash/context7-mcp",
        "license": "MIT",
        "install_method": "npx -y @upstash/context7-mcp (user approval required)",
        "transport": "stdio",
        "default_endpoint_or_cmd": "npx",
        "default_args": ["-y", "@upstash/context7-mcp"],
        "capabilities": ["docs_lookup", "library_docs"],
        "auth_requirements": ["Optional CONTEXT7_API_KEY"],
        "network_destinations": ["context7.com"],
        "risk_level": "low",
        "resource_estimate": "~80MB RAM",
        "recommended_businesses": ["Game Development", "Technical Architect", "Software Developer"],
    },
    {
        "catalog_key": "filesystem-mcp",
        "name": "MCP Reference Filesystem Server",
        "category": "common",
        "description": "Official Anthropic/MCP reference Filesystem server restricted strictly to approved worktree roots.",
        "source_repo": "https://github.com/modelcontextprotocol/servers",
        "version": "@modelcontextprotocol/server-filesystem",
        "license": "MIT",
        "install_method": "npx -y @modelcontextprotocol/server-filesystem <approved_root>",
        "transport": "stdio",
        "default_endpoint_or_cmd": "npx",
        "default_args": ["-y", "@modelcontextprotocol/server-filesystem"],
        "capabilities": ["fs_read", "fs_write_scoped"],
        "auth_requirements": [],
        "network_destinations": [],
        "risk_level": "medium",
        "resource_estimate": "~70MB RAM",
        "recommended_businesses": ["Game Development", "Software Developer"],
    },
    {
        "catalog_key": "playwright-mcp",
        "name": "Microsoft Playwright MCP",
        "category": "game",
        "description": "Official Microsoft Playwright MCP for isolated browser QA, screenshots, DOM & console inspection (never attaches personal Chrome).",
        "source_repo": "https://github.com/microsoft/playwright-mcp",
        "version": "@playwright/mcp",
        "license": "Apache-2.0",
        "install_method": "npx -y @playwright/mcp --headless",
        "transport": "stdio",
        "default_endpoint_or_cmd": "npx",
        "default_args": ["-y", "@playwright/mcp", "--headless"],
        "capabilities": ["browser_qa", "screenshot", "dom_inspect"],
        "auth_requirements": [],
        "network_destinations": ["localhost / approved preview URLs"],
        "risk_level": "medium",
        "resource_estimate": "~350MB RAM (on-demand)",
        "recommended_businesses": ["Game Development", "Software Developer"],
    },
    {
        "catalog_key": "chrome-devtools-mcp",
        "name": "Chrome DevTools MCP",
        "category": "game",
        "description": "Isolated Chromium DevTools MCP for console, network, WebGL & performance profiling.",
        "source_repo": "https://github.com/ChromeDevTools/chrome-devtools-mcp",
        "version": "chrome-devtools-mcp",
        "license": "Apache-2.0",
        "install_method": "npx -y chrome-devtools-mcp@latest (isolated profile only)",
        "transport": "stdio",
        "default_endpoint_or_cmd": "npx",
        "default_args": ["-y", "chrome-devtools-mcp@latest"],
        "capabilities": ["devtools_debug", "perf_profile", "webgl_diag"],
        "auth_requirements": [],
        "network_destinations": ["localhost"],
        "risk_level": "medium",
        "resource_estimate": "~350MB RAM (on-demand)",
        "recommended_businesses": ["Game Development"],
    },
    {
        "catalog_key": "blender-mcp",
        "name": "Blender Host Bridge MCP (Optional)",
        "category": "game",
        "description": "Optional Blender addon/bridge for interactive scene inspection; prefer pack-game headless Python for deterministic checks.",
        "source_repo": "https://github.com/ahujasid/blender-mcp",
        "version": "community-verified",
        "license": "MIT",
        "install_method": "Blender addon + uvx blender-mcp (user-controlled Blender instance)",
        "transport": "stdio",
        "default_endpoint_or_cmd": "uvx",
        "default_args": ["blender-mcp"],
        "capabilities": ["blender_interactive", "3d_asset"],
        "auth_requirements": [],
        "network_destinations": ["127.0.0.1"],
        "risk_level": "medium",
        "resource_estimate": "~100MB RAM (+ Blender)",
        "recommended_businesses": ["Game Development"],
    },
]


def _rev_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()[:24]


class V321Mcp321Service:
    """Manages MCP Candidate Catalog (separate from registered servers) & External Plan Sync (monitor-only)."""

    def __init__(self, db: DatabaseManager, gateway: V31McpGatewayService, *, biz_service: Any = None) -> None:
        self.db = db
        self.gateway = gateway
        self.biz_service = biz_service
        self._seeded = False

    def ensure_catalog_seeded(self) -> None:
        if self._seeded:
            return
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            for entry in BUILTIN_CATALOG_ENTRIES:
                conn.execute(
                    """
                    INSERT INTO mcp321_catalog_entries (
                        id, catalog_key, name, category, description, source_repo, version, license,
                        install_method, transport, default_endpoint_or_cmd, default_args_json,
                        capabilities_json, auth_requirements_json, network_destinations_json,
                        risk_level, resource_estimate, recommended_businesses_json, verified_source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(catalog_key) DO UPDATE SET
                        description = excluded.description,
                        version = excluded.version,
                        capabilities_json = excluded.capabilities_json,
                        updated_at = excluded.updated_at
                    """,
                    (
                        f"cat-{entry['catalog_key']}",
                        entry["catalog_key"],
                        entry["name"],
                        entry["category"],
                        entry["description"],
                        entry["source_repo"],
                        entry["version"],
                        entry["license"],
                        entry["install_method"],
                        entry["transport"],
                        entry["default_endpoint_or_cmd"],
                        json.dumps(entry["default_args"]),
                        json.dumps(entry["capabilities"]),
                        json.dumps(entry["auth_requirements"]),
                        json.dumps(entry["network_destinations"]),
                        entry["risk_level"],
                        entry["resource_estimate"],
                        json.dumps(entry["recommended_businesses"]),
                        now,
                        now,
                    ),
                )
        self._seeded = True

    def list_catalog(self, category: str | None = None) -> dict[str, Any]:
        self.ensure_catalog_seeded()
        clauses = ["1=1"]
        args: list[Any] = []
        if category:
            clauses.append("category = ?")
            args.append(category)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM mcp321_catalog_entries WHERE {' AND '.join(clauses)} ORDER BY category, name",
                args,
            ).fetchall()
            reg_map = {
                r["name"]: {"id": r["id"], "approval_state": r["approval_state"], "health_status": r["health_status"]}
                for r in conn.execute("SELECT id, name, approval_state, health_status FROM v3_mcp_servers").fetchall()
            }
            items = []
            for r in rows:
                reg = reg_map.get(r["catalog_key"]) or reg_map.get(r["name"])
                lifecycle_state = "candidate_not_installed"
                if reg:
                    lifecycle_state = reg["health_status"] if reg["approval_state"] == "approved" else reg["approval_state"]
                items.append(
                    {
                        "id": r["id"],
                        "catalog_key": r["catalog_key"],
                        "name": r["name"],
                        "category": r["category"],
                        "description": r["description"],
                        "source_repo": r["source_repo"],
                        "version": r["version"],
                        "license": r["license"],
                        "install_method": r["install_method"],
                        "transport": r["transport"],
                        "default_endpoint_or_cmd": r["default_endpoint_or_cmd"],
                        "default_args": json.loads(r["default_args_json"]),
                        "capabilities": json.loads(r["capabilities_json"]),
                        "auth_requirements": json.loads(r["auth_requirements_json"]),
                        "network_destinations": json.loads(r["network_destinations_json"]),
                        "risk_level": r["risk_level"],
                        "resource_estimate": r["resource_estimate"],
                        "recommended_businesses": json.loads(r["recommended_businesses_json"]),
                        "registered": reg is not None,
                        "registered_server_id": reg["id"] if reg else None,
                        "lifecycle_state": lifecycle_state,
                    }
                )
            return {"items": items, "total": len(items)}

    def register_from_catalog(self, req: CatalogRegisterRequest, actor: str = "admin") -> dict[str, Any]:
        self.ensure_catalog_seeded()
        with self.db.read_conn() as conn:
            cat = conn.execute("SELECT * FROM mcp321_catalog_entries WHERE catalog_key = ?", (req.catalog_key,)).fetchone()
            if not cat:
                raise BridgeError(404, "catalog_entry_not_found", f"Catalog entry '{req.catalog_key}' not found.")

        srv_name = (req.server_name or cat["catalog_key"]).strip()
        transport = cat["transport"]
        ep = req.endpoint if req.endpoint is not None else (cat["default_endpoint_or_cmd"] if transport in ("streamable_http", "rest_api", "local_tool") else "")
        exe = req.executable if req.executable is not None else (cat["default_endpoint_or_cmd"] if transport in ("stdio", "native_cli") else "")
        args = req.args if req.args is not None else json.loads(cat["default_args_json"])

        preview = {
            "preview_only": req.preview_only,
            "catalog_key": cat["catalog_key"],
            "server_name": srv_name,
            "transport": transport,
            "endpoint": ep,
            "executable": exe,
            "args": args,
            "source_repo": cat["source_repo"],
            "license": cat["license"],
            "auth_requirements": json.loads(cat["auth_requirements_json"]),
            "risk_level": cat["risk_level"],
        }
        if req.preview_only:
            return preview

        created = self.gateway.create_server(
            McpGatewayServerCreateRequest(
                name=srv_name,
                description=cat["description"],
                adapter_transport=transport,
                endpoint=ep,
                executable=exe,
                args=args,
                version=cat["version"],
                capability_tags=json.loads(cat["capabilities_json"]),
                allowed_project_ids=req.allowed_project_ids,
            ),
            actor=actor,
        )
        sid = created["id"]
        discovery = None
        health = None
        if req.approve_immediately:
            self.gateway.v3_service.approve_mcp_server(sid, McpApprovalRequest(decision="approve", actor=actor))
            health = self.gateway.check_server_health(sid, actor=actor)
            if transport == "local_tool" and health["health_status"] == "ready":
                discovery = self.gateway.discover_server_tools(sid, actor=actor)
        binding_sync = None
        if self.biz_service is not None:
            binding_sync = self.biz_service.sync_default_business_bindings()
        return {"registered": True, "server": created, "health": health, "discovery": discovery, "binding_sync": binding_sync}

    # --- P8: External Plan & Task Synchronization (Monitor-Only) ---
    def ingest_external_plan(self, req: ExternalPlanIngestRequest, actor: str = "collector") -> dict[str, Any]:
        if not req.consent_confirmed:
            raise BridgeError(403, "consent_required", "Explicit opt-in consent is required to ingest external plans.")
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        now = utc_now_iso()
        redacted_obj = str(redact_payload({"objective": req.objective})["objective"])
        redacted_prov = redact_payload(req.provenance)
        rev_hash = _rev_hash({"title": req.title, "objective": redacted_obj, "status": req.normalized_status, "tasks": [t.model_dump() for t in req.tasks]})

        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            if wid and not conn.execute("SELECT 1 FROM worktrees WHERE id = ? AND project_id = ?", (wid, pid)).fetchone():
                raise BridgeError(404, "worktree_not_found", "Worktree not found in project.")

            existing = conn.execute(
                "SELECT * FROM mcp321_external_plans WHERE project_id = ? AND source_client = ? AND external_plan_id = ?",
                (pid, req.source_client, req.external_plan_id),
            ).fetchone()

            if existing:
                plan_id = existing["id"]
                if existing["revision_hash"] == rev_hash and not existing["deleted_tombstone"]:
                    return {"id": plan_id, "deduplicated": True, "revision_no": existing["revision_no"], "revision_hash": rev_hash, "is_monitor_only": True}
                rev_no = int(existing["revision_no"]) + 1
                conn.execute(
                    """
                    UPDATE mcp321_external_plans
                    SET title = ?, objective = ?, source_status = ?, normalized_status = ?,
                        revision_no = ?, revision_hash = ?, linked_session_id = COALESCE(?, linked_session_id),
                        linked_root_trace_id = COALESCE(?, linked_root_trace_id),
                        provenance_json = ?, deleted_tombstone = 0, observed_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        req.title, redacted_obj, req.source_status, req.normalized_status,
                        rev_no, rev_hash,
                        str(req.linked_session_id) if req.linked_session_id else None,
                        str(req.linked_root_trace_id) if req.linked_root_trace_id else None,
                        json.dumps(redacted_prov, ensure_ascii=False), now, now, plan_id,
                    ),
                )
            else:
                plan_id = str(uuid.uuid4())
                rev_no = 1
                conn.execute(
                    """
                    INSERT INTO mcp321_external_plans (
                        id, project_id, worktree_id, source_client, source_instance, external_plan_id,
                        title, objective, source_status, normalized_status, revision_no, revision_hash,
                        is_monitor_only, linked_session_id, linked_root_trace_id, provenance_json,
                        deleted_tombstone, observed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, 1, ?, ?, ?, 0, ?, ?, ?)
                    """,
                    (
                        plan_id, pid, wid, req.source_client, req.source_instance, req.external_plan_id,
                        req.title, redacted_obj, req.source_status, req.normalized_status, rev_hash,
                        str(req.linked_session_id) if req.linked_session_id else None,
                        str(req.linked_root_trace_id) if req.linked_root_trace_id else None,
                        json.dumps(redacted_prov, ensure_ascii=False), now, now, now,
                    ),
                )

            # Upsert external tasks
            for t in req.tasks:
                thash = _rev_hash(t.model_dump())
                conn.execute(
                    """
                    INSERT INTO mcp321_external_tasks (
                        id, external_plan_id, project_id, external_task_key, title, parent_task_id,
                        source_status, normalized_status, revision_hash, observed_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(external_plan_id, external_task_key) DO UPDATE SET
                        title = excluded.title,
                        parent_task_id = excluded.parent_task_id,
                        source_status = excluded.source_status,
                        normalized_status = excluded.normalized_status,
                        revision_hash = excluded.revision_hash,
                        observed_at = excluded.observed_at,
                        updated_at = excluded.updated_at
                    """,
                    (str(uuid.uuid4()), plan_id, pid, t.external_task_key, t.title, t.parent_task_id, t.source_status, t.normalized_status, thash, now, now, now),
                )

            # Save immutable revision snapshot
            conn.execute(
                """
                INSERT INTO mcp321_external_plan_revisions (id, external_plan_id, revision_no, revision_hash, snapshot_json, observed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (str(uuid.uuid4()), plan_id, rev_no, rev_hash, json.dumps({"title": req.title, "objective": redacted_obj, "status": req.normalized_status, "tasks": [t.model_dump() for t in req.tasks]}, ensure_ascii=False), now),
            )
            return {"id": plan_id, "deduplicated": False, "revision_no": rev_no, "revision_hash": rev_hash, "is_monitor_only": True}

    def create_monitoring_plan_from_session(self, req: SessionToMonitoringPlanRequest, actor: str = "admin") -> dict[str, Any]:
        """Explicit user-confirmed session-to-monitoring-plan creation (honest provenance, never fabricates a native plan)."""
        pid = str(req.project_id)
        sid = str(req.session_id)
        with self.db.read_conn() as conn:
            sess = conn.execute("SELECT * FROM v2_sessions WHERE id = ? AND project_id = ?", (sid, pid)).fetchone()
            if not sess:
                raise BridgeError(404, "session_not_found", "Session not found in project scope.")
        return self.ingest_external_plan(
            ExternalPlanIngestRequest(
                project_id=req.project_id,
                worktree_id=uuid.UUID(sess["worktree_id"]) if sess["worktree_id"] else None,
                source_client=req.source_client,
                source_instance="explicit-session-monitoring",
                external_plan_id=f"session:{sid}",
                title=req.title,
                objective=req.objective,
                source_status=sess["status"],
                normalized_status="active" if sess["status"] == "running" else ("completed" if sess["status"] == "completed" else "unknown"),
                linked_session_id=req.session_id,
                linked_root_trace_id=req.root_trace_id,
                provenance={"mode": "explicit_session_to_monitoring_plan", "created_by": actor, "native_plan_fabricated": False},
                consent_confirmed=req.consent_confirmed,
            ),
            actor=actor,
        )

    def list_external_plans(self, project_id: str, source_client: str | None = None, include_deleted: bool = False) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        if not include_deleted:
            clauses.append("deleted_tombstone = 0")
        if source_client:
            clauses.append("source_client = ?")
            args.append(source_client)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM mcp321_external_plans WHERE {' AND '.join(clauses)} ORDER BY updated_at DESC LIMIT 100",
                args,
            ).fetchall()
            items = []
            for r in rows:
                tcnt = conn.execute("SELECT COUNT(*) FROM mcp321_external_tasks WHERE external_plan_id = ?", (r["id"],)).fetchone()[0]
                acnt = conn.execute("SELECT COUNT(*) FROM mcp321_trace_associations WHERE external_plan_id = ?", (r["id"],)).fetchone()[0]
                d = dict(r)
                d["provenance"] = json.loads(d.pop("provenance_json"))
                d["is_monitor_only"] = bool(d["is_monitor_only"])
                d["deleted_tombstone"] = bool(d["deleted_tombstone"])
                d["task_count"] = tcnt
                d["trace_association_count"] = acnt
                items.append(d)
            return {"items": items, "total": len(items)}

    def delete_external_plan(self, plan_id: str, project_id: str) -> dict[str, Any]:
        """Consent withdrawal / tombstone delete of external monitor-only plan (preserves immutable traces!)."""
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            cur = conn.execute(
                "UPDATE mcp321_external_plans SET deleted_tombstone = 1, updated_at = ? WHERE id = ? AND project_id = ?",
                (now, plan_id, project_id),
            )
            if cur.rowcount == 0:
                raise BridgeError(404, "external_plan_not_found", "External plan not found in project scope.")
        return {"id": plan_id, "deleted_tombstone": True, "traces_preserved": True}

    def associate_trace(self, req: TraceAssociationRequest, actor: str = "admin") -> dict[str, Any]:
        pid = str(req.project_id)
        now = utc_now_iso()
        aid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM mcp321_external_plans WHERE id = ? AND project_id = ? AND deleted_tombstone = 0", (req.external_plan_id, pid)).fetchone():
                raise BridgeError(404, "external_plan_not_found", "External plan not found in project scope.")
            if req.trace_id and not conn.execute("SELECT 1 FROM v22_execution_traces WHERE trace_id = ? AND project_id = ?", (str(req.trace_id), pid)).fetchone():
                raise BridgeError(404, "trace_not_found", "Trace not found in project scope.")
            if req.session_id and not conn.execute("SELECT 1 FROM v2_sessions WHERE id = ? AND project_id = ?", (str(req.session_id), pid)).fetchone():
                raise BridgeError(404, "session_not_found", "Session not found in project scope.")
            conn.execute(
                """
                INSERT INTO mcp321_trace_associations (id, project_id, external_plan_id, external_task_id, trace_id, session_id, actor, evidence_note, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (aid, pid, req.external_plan_id, req.external_task_id, str(req.trace_id) if req.trace_id else None, str(req.session_id) if req.session_id else None, actor, req.evidence_note, now),
            )
        return {"id": aid, "external_plan_id": req.external_plan_id, "linked": True}

    # --- Project-Level MCP Overrides (inherit business MCPs, restrict/disable, never escalate) ---
    def list_project_mcp_overrides(self, project_id: str, business_id: str) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            if not conn.execute("SELECT 1 FROM v31_business_profiles WHERE id = ?", (business_id,)).fetchone():
                raise BridgeError(404, "business_not_found", "Business workspace not found.")
            linked = conn.execute(
                "SELECT 1 FROM v31_business_projects WHERE business_id = ? AND project_id = ?",
                (business_id, project_id),
            ).fetchone()
            bindings = conn.execute(
                """
                SELECT bm.server_id, bm.enabled AS business_enabled, bm.priority AS business_priority,
                       COALESCE(bm.activation_mode, 'on_demand') AS activation_mode,
                       s.name AS server_name, s.approval_state, s.health_status,
                       COALESCE(ext.adapter_transport, s.transport) AS transport
                FROM v31_business_mcp_bindings bm
                JOIN v3_mcp_servers s ON s.id = bm.server_id
                LEFT JOIN v31_mcp_server_ext ext ON ext.server_id = s.id
                WHERE bm.business_id = ?
                ORDER BY bm.priority ASC, s.name ASC
                """,
                (business_id,),
            ).fetchall()
            ovr_map = {
                r["server_id"]: {"mode": r["mode"], "priority": r["priority"], "updated_at": r["updated_at"]}
                for r in conn.execute(
                    "SELECT server_id, mode, priority, updated_at FROM mcp321_project_mcp_overrides WHERE project_id = ? AND business_id = ?",
                    (project_id, business_id),
                ).fetchall()
            }
            items = []
            for b in bindings:
                ovr = ovr_map.get(b["server_id"])
                mode = ovr["mode"] if ovr else "inherit"
                eff_enabled = bool(b["business_enabled"]) and (mode != "disabled")
                items.append(
                    {
                        "server_id": b["server_id"],
                        "server_name": b["server_name"],
                        "server_kind": "local_tool_pack" if b["transport"] == "local_tool" else "mcp_server",
                        "transport": b["transport"],
                        "approval_state": b["approval_state"],
                        "health_status": b["health_status"],
                        "activation_mode": b["activation_mode"],
                        "business_enabled": bool(b["business_enabled"]),
                        "override_mode": mode,
                        "effective_enabled": eff_enabled,
                        "effective_priority": ovr["priority"] if (ovr and ovr["priority"] is not None) else b["business_priority"],
                        "updated_at": ovr["updated_at"] if ovr else None,
                    }
                )
            return {"project_id": project_id, "business_id": business_id, "project_linked_to_business": linked is not None, "items": items}

    def set_project_mcp_override(self, project_id: str, req: ProjectMcpOverrideRequest) -> dict[str, Any]:
        """Project may inherit or disable a business-bound MCP; NEVER escalate unbound/disabled business MCPs."""
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (project_id,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found.")
            bind = conn.execute(
                "SELECT bm.enabled, s.approval_state FROM v31_business_mcp_bindings bm JOIN v3_mcp_servers s ON s.id = bm.server_id WHERE bm.business_id = ? AND bm.server_id = ?",
                (req.business_id, req.server_id),
            ).fetchone()
            if not bind:
                raise BridgeError(403, "project_override_cannot_escalate", "Project override cannot bind or escalate a server not bound to the business workspace.")
            if req.mode == "enabled" and (not bind["enabled"] or bind["approval_state"] != "approved"):
                raise BridgeError(403, "project_override_cannot_escalate", "Project override cannot enable an unapproved or business-disabled MCP server.")
            if req.mode == "inherit" and req.priority is None:
                conn.execute(
                    "DELETE FROM mcp321_project_mcp_overrides WHERE project_id = ? AND business_id = ? AND server_id = ?",
                    (project_id, req.business_id, req.server_id),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO mcp321_project_mcp_overrides (project_id, business_id, server_id, mode, priority, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(project_id, business_id, server_id) DO UPDATE SET
                        mode = excluded.mode,
                        priority = excluded.priority,
                        updated_at = excluded.updated_at
                    """,
                    (project_id, req.business_id, req.server_id, req.mode, req.priority, now),
                )
        return self.list_project_mcp_overrides(project_id, req.business_id)

    # --- P9: Client Runtime Adapter Inventory (Read-Only Capability Manifest) ---
    def get_runtime_adapters(self) -> dict[str, Any]:
        return {
            "managed_namespace": "ai-memory-hub-managed",
            "write_policy": "never_overwrite_user_config_without_backup_and_explicit_apply",
            "adapters": [
                {
                    "client_id": "antigravity",
                    "display_name": "Antigravity IDE / agy CLI",
                    "billing_mode": "subscription_cli",
                    "direct_dispatch_supported": True,
                    "manual_handoff_supported": True,
                    "modes_supported": ["chat", "task", "plan_dag"],
                    "session_resume_supported": True,
                    "mcp_activation_mode": "managed_preview_and_apply",
                    "skill_delivery_mode": "mcp_pull_and_context_pack",
                    "external_plan_sync": "opt_in_read_only_markdown_collector",
                    "token_telemetry_fields": ["input_tokens", "output_tokens", "cache_read_tokens"],
                    "token_telemetry_unsupported": ["cache_write_tokens", "reasoning_tokens", "cost_usd"],
                    "rollback_supported": True,
                },
                {
                    "client_id": "claude",
                    "display_name": "Claude Code CLI",
                    "billing_mode": "subscription_cli",
                    "direct_dispatch_supported": True,
                    "manual_handoff_supported": True,
                    "modes_supported": ["chat", "task", "plan_dag"],
                    "session_resume_supported": False,
                    "mcp_activation_mode": "managed_json_namespace",
                    "skill_delivery_mode": "managed_claude_skills_dir",
                    "external_plan_sync": "session_monitor_only",
                    "token_telemetry_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"],
                    "token_telemetry_unsupported": ["subscription_billing_cost_usd"],
                    "rollback_supported": True,
                },
                {
                    "client_id": "codex",
                    "display_name": "Codex CLI",
                    "billing_mode": "subscription_cli",
                    "direct_dispatch_supported": True,
                    "manual_handoff_supported": True,
                    "modes_supported": ["chat", "task", "plan_dag"],
                    "session_resume_supported": False,
                    "mcp_activation_mode": "managed_toml_block",
                    "skill_delivery_mode": "managed_agents_skill_pack",
                    "external_plan_sync": "session_monitor_only",
                    "token_telemetry_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"],
                    "token_telemetry_unsupported": ["subscription_billing_cost_usd"],
                    "rollback_supported": True,
                },
                {
                    "client_id": "gemini",
                    "display_name": "Gemini CLI",
                    "billing_mode": "subscription_cli",
                    "direct_dispatch_supported": False,
                    "manual_handoff_supported": True,
                    "modes_supported": ["plan_dag"],
                    "session_resume_supported": False,
                    "mcp_activation_mode": "managed_settings_json",
                    "skill_delivery_mode": "managed_gemini_skills_dir",
                    "external_plan_sync": "opt_in_read_only_collector",
                    "token_telemetry_fields": [],
                    "token_telemetry_unsupported": ["cli_not_installed_or_unavailable"],
                    "rollback_supported": True,
                },
                {
                    "client_id": "deepseek_api",
                    "display_name": "DeepSeek API (Optional Paid — Gated)",
                    "mcp_activation_mode": "hub_gateway_only",
                    "skill_delivery_mode": "context_pack_injection",
                    "external_plan_sync": "hub_native",
                    "token_telemetry_fields": ["input_tokens", "output_tokens", "cache_read_tokens", "reasoning_tokens"],
                    "token_telemetry_unsupported": [],
                    "rollback_supported": True,
                },
                {
                    "client_id": "qwen_local",
                    "display_name": "Qwen Local / Ollama (0 Cost)",
                    "mcp_activation_mode": "hub_gateway_only",
                    "skill_delivery_mode": "context_pack_injection",
                    "external_plan_sync": "hub_native",
                    "token_telemetry_fields": ["input_tokens", "output_tokens"],
                    "token_telemetry_unsupported": ["cache_read_tokens", "cache_write_tokens"],
                    "rollback_supported": True,
                },
            ],
        }
