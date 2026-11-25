"""V3 Memory Bridge service: Dynamic MCP Registry/Routing (V3.0) and Conversation History + FTS5 (V3.2-V3.4)."""
from __future__ import annotations

import json
import re
import uuid
from typing import Any

from .db import DatabaseManager, utc_now_iso
from .errors import BridgeError
from .security import redact_payload
from .v3_schema import (
    ConversationIngestRequest,
    ConversationPromoteRequest,
    McpApprovalRequest,
    McpRouteRequest,
    McpServerRegisterRequest,
)

_FORBIDDEN_CMD_RE = re.compile(r"(?:[;&|`$]|\b(?:rm\s+-rf|powershell\s+-enc|cmd\s+/c|sh\s+-c)\b)", re.IGNORECASE)


def _fts_query(raw: str) -> str:
    tokens = re.findall(r"[\w\u00C0-\u1EF9]+", raw, flags=re.UNICODE)
    if not tokens:
        return ""
    return " AND ".join(f'"{t}"' for t in tokens[:12])


class V3BridgeService:
    def __init__(self, db: DatabaseManager) -> None:
        self.db = db
        self._builtins_seeded = False

    def _ensure_seeded(self) -> None:
        if not self._builtins_seeded:
            self.seed_builtin_mcp_servers()
            self._builtins_seeded = True

    def _audit_mcp(
        self,
        conn: Any,
        *,
        action: str,
        status: str,
        actor: str,
        project_id: str | None = None,
        worktree_id: str | None = None,
        server_id: str | None = None,
        tool_name: str | None = None,
        latency_ms: int | None = None,
        error_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        conn.execute(
            """
            INSERT INTO v3_mcp_audit (
                id, project_id, worktree_id, session_id, trace_id, server_id, tool_name,
                action, status, latency_ms, error_code, actor, details_json, created_at
            ) VALUES (?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                project_id,
                worktree_id,
                server_id,
                tool_name,
                action,
                status,
                latency_ms,
                error_code,
                actor,
                json.dumps(redact_payload(details or {}), ensure_ascii=False),
                utc_now_iso(),
            ),
        )

    # --- V3.0 Dynamic MCP Registry & Scoped Routing ---
    def seed_builtin_mcp_servers(self) -> None:
        now = utc_now_iso()
        builtins = [
            (
                "builtin-ai-memory-bridge",
                "ai-memory-bridge",
                "streamable_http",
                "http://127.0.0.1:8082/mcp",
                "v3.0",
                [
                    ("get_project_context", "Get bounded verified project/worktree context", ["memory_read", "context"], "read_only", 0),
                    ("search_memory", "Search verified memories via FTS5 unicode61", ["memory_read", "search"], "read_only", 0),
                    ("get_handoff", "Retrieve scoped task handoff", ["memory_read", "handoff"], "read_only", 0),
                    ("save_task_result", "Save candidate task handoff result", ["memory_write", "handoff"], "write_scoped", 0),
                    ("save_decision", "Save candidate technical decision", ["memory_write", "decision"], "write_scoped", 0),
                    ("search_code", "Proxy code symbol search via Codebase Adapter", ["code_search", "symbols"], "read_only", 0),
                ],
            ),
            (
                "builtin-ai-memory-codebase",
                "ai-memory-codebase",
                "streamable_http",
                "http://127.0.0.1:8081/mcp",
                "v0.12.1",
                [
                    ("search_code", "Search indexed code symbols & line ranges in approved worktree", ["code_search", "symbols", "ast"], "read_only", 0),
                ],
            ),
        ]
        with self.db.write_tx() as conn:
            for sid, name, transport, endpoint, ver, tools in builtins:
                conn.execute(
                    """
                    INSERT INTO v3_mcp_servers (
                        id, name, transport, endpoint_or_cmd, version, trust_tier, approval_state,
                        allowed_project_ids_json, health_status, last_checked_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'builtin', 'approved', '[]', 'unknown', NULL, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        version = excluded.version,
                        updated_at = excluded.updated_at
                    """,
                    (sid, name, transport, endpoint, ver, now, now),
                )
                row = conn.execute("SELECT id FROM v3_mcp_servers WHERE name = ?", (name,)).fetchone()
                actual_sid = row["id"]
                for tname, desc, tags, side_eff, req_app in tools:
                    conn.execute(
                        """
                        INSERT INTO v3_mcp_tools (
                            id, server_id, tool_name, description, input_schema_hash,
                            capability_tags_json, side_effect_level, requires_approval, discovered_at
                        ) VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)
                        ON CONFLICT(server_id, tool_name) DO UPDATE SET
                            description = excluded.description,
                            capability_tags_json = excluded.capability_tags_json,
                            side_effect_level = excluded.side_effect_level,
                            discovered_at = excluded.discovered_at
                        """,
                        (str(uuid.uuid4()), actual_sid, tname, desc, json.dumps(tags), side_eff, req_app, now),
                    )

    def list_mcp_servers(self) -> dict[str, Any]:
        self._ensure_seeded()
        with self.db.read_conn() as conn:
            servers = conn.execute("SELECT * FROM v3_mcp_servers ORDER BY trust_tier, name").fetchall()
            tools = conn.execute("SELECT * FROM v3_mcp_tools ORDER BY tool_name").fetchall()
            by_srv: dict[str, list[dict[str, Any]]] = {}
            for t in tools:
                by_srv.setdefault(t["server_id"], []).append(
                    {
                        "id": t["id"],
                        "tool_name": t["tool_name"],
                        "description": t["description"],
                        "capability_tags": json.loads(t["capability_tags_json"]),
                        "side_effect_level": t["side_effect_level"],
                        "requires_approval": bool(t["requires_approval"]),
                        "discovered_at": t["discovered_at"],
                    }
                )
            return {
                "items": [
                    {
                        "id": s["id"],
                        "name": s["name"],
                        "transport": s["transport"],
                        "endpoint_or_cmd": s["endpoint_or_cmd"],
                        "version": s["version"],
                        "trust_tier": s["trust_tier"],
                        "approval_state": s["approval_state"],
                        "allowed_project_ids": json.loads(s["allowed_project_ids_json"]),
                        "health_status": s["health_status"],
                        "last_latency_ms": s["last_latency_ms"],
                        "last_checked_at": s["last_checked_at"],
                        "tools": by_srv.get(s["id"], []),
                    }
                    for s in servers
                ]
            }

    def register_mcp_server(self, req: McpServerRegisterRequest, actor: str = "admin") -> dict[str, Any]:
        if _FORBIDDEN_CMD_RE.search(req.endpoint_or_cmd):
            raise BridgeError(422, "unsafe_mcp_endpoint", "Arbitrary shell syntax is forbidden in Dynamic MCP endpoint/command.")
        if req.transport == "streamable_http" and not req.endpoint_or_cmd.startswith(("http://127.0.0.1:", "http://localhost:", "https://")):
            raise BridgeError(422, "unsafe_mcp_endpoint", "HTTP MCP server must use loopback or explicit HTTPS.")

        now = utc_now_iso()
        sid = str(uuid.uuid4())
        with self.db.write_tx() as conn:
            if conn.execute("SELECT 1 FROM v3_mcp_servers WHERE name = ?", (req.name,)).fetchone():
                raise BridgeError(409, "mcp_server_exists", f"MCP server '{req.name}' already exists.")
            # New servers ALWAYS start as pending_approval; never auto-activate untrusted servers.
            conn.execute(
                """
                INSERT INTO v3_mcp_servers (
                    id, name, transport, endpoint_or_cmd, version, trust_tier, approval_state,
                    allowed_project_ids_json, health_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'approved_local', 'pending_approval', ?, 'unknown', ?, ?)
                """,
                (sid, req.name, req.transport, req.endpoint_or_cmd, req.version, json.dumps(req.allowed_project_ids), now, now),
            )
            for t in req.tools:
                conn.execute(
                    """
                    INSERT INTO v3_mcp_tools (
                        id, server_id, tool_name, description, input_schema_hash,
                        capability_tags_json, side_effect_level, requires_approval, discovered_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        sid,
                        t.tool_name,
                        t.description,
                        t.input_schema_hash,
                        json.dumps(t.capability_tags),
                        t.side_effect_level,
                        1 if (t.requires_approval or t.side_effect_level == "restricted") else 0,
                        now,
                    ),
                )
            self._audit_mcp(conn, action="register", status="pending_approval", actor=actor, server_id=sid, details={"name": req.name, "tool_count": len(req.tools)})
        return {"id": sid, "name": req.name, "approval_state": "pending_approval"}

    def approve_mcp_server(self, server_id: str, req: McpApprovalRequest) -> dict[str, Any]:
        now = utc_now_iso()
        new_state = "approved" if req.decision == "approve" else "revoked"
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT * FROM v3_mcp_servers WHERE id = ?", (server_id,)).fetchone()
            if not row:
                raise BridgeError(404, "mcp_server_not_found", "MCP server not found.")
            allowed_json = json.dumps(req.allowed_project_ids) if req.allowed_project_ids is not None else row["allowed_project_ids_json"]
            conn.execute(
                "UPDATE v3_mcp_servers SET approval_state = ?, allowed_project_ids_json = ?, updated_at = ? WHERE id = ?",
                (new_state, allowed_json, now, server_id),
            )
            self._audit_mcp(conn, action=req.decision, status=new_state, actor=req.actor, server_id=server_id)
        return {"id": server_id, "approval_state": new_state}

    def route_mcp_capabilities(self, req: McpRouteRequest, actor: str = "planner") -> dict[str, Any]:
        self._ensure_seeded()
        pid = str(req.project_id)
        wid = str(req.worktree_id) if req.worktree_id else None
        wanted = {c.strip().lower() for c in req.required_capabilities if c.strip()}
        matches: list[dict[str, Any]] = []
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM projects WHERE id = ?", (pid,)).fetchone():
                raise BridgeError(404, "project_not_found", "Project not found")
            servers = conn.execute(
                "SELECT * FROM v3_mcp_servers WHERE approval_state = 'approved' AND health_status != 'down'"
            ).fetchall()
            for s in servers:
                allowed = json.loads(s["allowed_project_ids_json"])
                if s["trust_tier"] != "builtin" and allowed and pid not in allowed:
                    continue
                tools = conn.execute("SELECT * FROM v3_mcp_tools WHERE server_id = ?", (s["id"],)).fetchall()
                for t in tools:
                    if not req.allow_write and t["side_effect_level"] != "read_only":
                        continue
                    tags = {x.lower() for x in json.loads(t["capability_tags_json"])}
                    overlap = sorted(wanted & tags)
                    if overlap:
                        matches.append(
                            {
                                "server_id": s["id"],
                                "server_name": s["name"],
                                "transport": s["transport"],
                                "tool_name": t["tool_name"],
                                "matched_capabilities": overlap,
                                "side_effect_level": t["side_effect_level"],
                                "requires_approval": bool(t["requires_approval"]),
                            }
                        )
            matches.sort(key=lambda x: (-len(x["matched_capabilities"]), x["server_name"], x["tool_name"]))
            self._audit_mcp(
                conn,
                action="route",
                status="matched" if matches else "no_match",
                actor=actor,
                project_id=pid,
                worktree_id=wid,
                details={"required": sorted(wanted), "matched_count": len(matches)},
            )
        return {"project_id": pid, "required_capabilities": sorted(wanted), "matches": matches[:20]}

    def list_mcp_audit(self, limit: int = 50) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            rows = conn.execute("SELECT * FROM v3_mcp_audit ORDER BY created_at DESC LIMIT ?", (min(max(1, limit), 200),)).fetchall()
            return {"items": [{**dict(r), "details_json": json.loads(r["details_json"])} for r in rows]}

    # --- V3.2 & V3.3 Conversation Storage, FTS5 Search & Recovery ---
    def ingest_conversation(self, req: ConversationIngestRequest) -> dict[str, Any]:
        pid, wid = str(req.project_id), str(req.worktree_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            if not conn.execute("SELECT 1 FROM worktrees WHERE id = ? AND project_id = ?", (wid, pid)).fetchone():
                raise BridgeError(404, "worktree_not_found", "Worktree not found for project")

            # Verify explicit links only; never fabricate cross-project links.
            sid = str(req.session_id) if req.session_id else None
            if sid and not conn.execute("SELECT 1 FROM v2_sessions WHERE id = ? AND project_id = ?", (sid, pid)).fetchone():
                raise BridgeError(422, "invalid_session_link", "Session ID not found in project scope")
            plan_id = str(req.plan_id) if req.plan_id else None
            if plan_id and not conn.execute("SELECT 1 FROM v2_plans WHERE id = ? AND project_id = ?", (plan_id, pid)).fetchone():
                raise BridgeError(422, "invalid_plan_link", "Plan ID not found in project scope")
            task_id = str(req.task_id) if req.task_id else None
            if task_id and not conn.execute("SELECT 1 FROM v2_tasks WHERE id = ? AND project_id = ?", (task_id, pid)).fetchone():
                raise BridgeError(422, "invalid_task_link", "Task ID not found in project scope")

            existing = conn.execute(
                "SELECT id FROM v3_conversations WHERE project_id = ? AND worktree_id = ? AND client_type = ? AND external_conversation_id = ?",
                (pid, wid, req.client_type, req.external_conversation_id),
            ).fetchone()
            if existing:
                conv_id = existing["id"]
                conn.execute(
                    """
                    UPDATE v3_conversations
                    SET title = COALESCE(?, title),
                        session_id = COALESCE(?, session_id),
                        plan_id = COALESCE(?, plan_id),
                        task_id = COALESCE(?, task_id),
                        provider = COALESCE(?, provider),
                        model_id = COALESCE(?, model_id),
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (req.title, sid, plan_id, task_id, req.provider, req.model_id, now, conv_id),
                )
            else:
                conv_id = str(uuid.uuid4())
                started_at = min(m.created_at for m in req.messages)
                conn.execute(
                    """
                    INSERT INTO v3_conversations (
                        id, project_id, worktree_id, client_type, external_conversation_id,
                        session_id, plan_id, task_id, provider, model_id, title,
                        started_at, updated_at, message_count, provenance_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
                    """,
                    (
                        conv_id, pid, wid, req.client_type, req.external_conversation_id,
                        sid, plan_id, task_id, req.provider, req.model_id, req.title,
                        started_at, now, json.dumps(redact_payload(req.provenance), ensure_ascii=False),
                    ),
                )

            inserted = 0
            deduped = 0
            for m in req.messages:
                if conn.execute(
                    "SELECT 1 FROM v3_conversation_messages WHERE conversation_id = ? AND content_hash = ?",
                    (conv_id, m.content_hash),
                ).fetchone():
                    deduped += 1
                    continue
                mid = str(uuid.uuid4())
                conn.execute(
                    """
                    INSERT INTO v3_conversation_messages (
                        id, conversation_id, project_id, worktree_id, seq_no, role,
                        visible_text, model_id, input_tokens, output_tokens, content_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (mid, conv_id, pid, wid, m.seq_no, m.role, m.visible_text, m.model_id, m.input_tokens, m.output_tokens, m.content_hash, m.created_at),
                )
                conn.execute(
                    "INSERT INTO v3_conversation_messages_fts (message_id, conversation_id, project_id, worktree_id, role, visible_text) VALUES (?, ?, ?, ?, ?, ?)",
                    (mid, conv_id, pid, wid, m.role, m.visible_text),
                )
                inserted += 1

            total_msgs = conn.execute("SELECT COUNT(*) FROM v3_conversation_messages WHERE conversation_id = ?", (conv_id,)).fetchone()[0]
            conn.execute("UPDATE v3_conversations SET message_count = ?, updated_at = ? WHERE id = ?", (total_msgs, now, conv_id))

        return {"conversation_id": conv_id, "inserted": inserted, "deduplicated": deduped, "message_count": total_msgs}

    def list_conversations(
        self,
        *,
        project_id: str,
        worktree_id: str | None = None,
        client_type: str | None = None,
        model_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        clauses = ["project_id = ?"]
        args: list[Any] = [project_id]
        if worktree_id:
            clauses.append("worktree_id = ?")
            args.append(worktree_id)
        if client_type:
            clauses.append("client_type = ?")
            args.append(client_type)
        if model_id:
            clauses.append("model_id = ?")
            args.append(model_id)
        where = " AND ".join(clauses)
        with self.db.read_conn() as conn:
            total = conn.execute(f"SELECT COUNT(*) FROM v3_conversations WHERE {where}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM v3_conversations WHERE {where} ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
            items = [{**dict(r), "provenance": json.loads(r["provenance_json"])} for r in rows]
            for it in items:
                it.pop("provenance_json", None)
            return {"items": items, "total": total, "next_offset": offset + limit if offset + limit < total else None}

    def get_conversation_detail(self, conv_id: str, project_id: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        with self.db.read_conn() as conn:
            conv = conn.execute("SELECT * FROM v3_conversations WHERE id = ? AND project_id = ?", (conv_id, project_id)).fetchone()
            if not conv:
                raise BridgeError(404, "conversation_not_found", "Conversation not found in project scope")
            msgs = conn.execute(
                "SELECT * FROM v3_conversation_messages WHERE conversation_id = ? ORDER BY seq_no ASC, created_at ASC LIMIT ? OFFSET ?",
                (conv_id, limit, offset),
            ).fetchall()
            cdict = dict(conv)
            cdict["provenance"] = json.loads(cdict.pop("provenance_json"))
            cdict["links"] = {
                "session_id": conv["session_id"],
                "plan_id": conv["plan_id"],
                "task_id": conv["task_id"],
                "unlinked": not any((conv["session_id"], conv["plan_id"], conv["task_id"])),
            }
            return {
                "conversation": cdict,
                "messages": [dict(m) for m in msgs],
                "total_messages": conv["message_count"],
                "next_offset": offset + limit if offset + limit < conv["message_count"] else None,
            }

    def search_conversations(
        self,
        *,
        project_id: str,
        query: str,
        worktree_id: str | None = None,
        client_type: str | None = None,
        model_id: str | None = None,
        limit: int = 10,
        max_chars: int = 4000,
    ) -> dict[str, Any]:
        fts = _fts_query(query)
        if not fts:
            return {"items": [], "truncated": False}
        clauses = ["f.project_id = ?", "v3_conversation_messages_fts MATCH ?"]
        args: list[Any] = [project_id, fts]
        if worktree_id:
            clauses.append("f.worktree_id = ?")
            args.append(worktree_id)
        if client_type:
            clauses.append("c.client_type = ?")
            args.append(client_type)
        if model_id:
            clauses.append("COALESCE(m.model_id, c.model_id) = ?")
            args.append(model_id)
        where = " AND ".join(clauses)
        with self.db.read_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT m.id AS message_id, m.conversation_id, m.seq_no, m.role, m.visible_text, m.created_at,
                       COALESCE(m.model_id, c.model_id) AS model_id, c.client_type, c.title, c.session_id, c.task_id
                FROM v3_conversation_messages_fts f
                JOIN v3_conversation_messages m ON m.id = f.message_id
                JOIN v3_conversations c ON c.id = m.conversation_id
                WHERE {where}
                ORDER BY bm25(v3_conversation_messages_fts), m.created_at DESC
                LIMIT ?
                """,
                [*args, min(max(1, limit), 30)],
            ).fetchall()
        items: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for r in rows:
            excerpt = str(r["visible_text"])[:450]
            if used + len(excerpt) > max_chars:
                truncated = True
                break
            used += len(excerpt)
            items.append(
                {
                    "message_id": r["message_id"],
                    "conversation_id": r["conversation_id"],
                    "title": r["title"],
                    "client_type": r["client_type"],
                    "model_id": r["model_id"],
                    "seq_no": r["seq_no"],
                    "role": r["role"],
                    "excerpt": excerpt,
                    "created_at": r["created_at"],
                    "session_id": r["session_id"],
                    "task_id": r["task_id"],
                }
            )
        return {"items": items, "truncated": truncated}

    def promote_excerpt_to_memory(self, conv_id: str, req: ConversationPromoteRequest) -> dict[str, Any]:
        pid = str(req.project_id)
        mid = str(req.message_id)
        now = utc_now_iso()
        with self.db.write_tx() as conn:
            row = conn.execute(
                "SELECT m.*, c.client_type FROM v3_conversation_messages m JOIN v3_conversations c ON c.id = m.conversation_id WHERE m.id = ? AND m.conversation_id = ? AND m.project_id = ?",
                (mid, conv_id, pid),
            ).fetchone()
            if not row:
                raise BridgeError(404, "message_not_found", "Conversation message not found in scope")
            new_mem_id = str(uuid.uuid4())
            evidence_ref = f"conversation:{conv_id}#seq:{row['seq_no']}"
            body = str(row["visible_text"])[:3500]
            conn.execute(
                """
                INSERT INTO memories (
                    id, project_id, worktree_id, type, title, body, tags_json,
                    source_agent, source_commit, evidence_ref, state, supersedes, created_at, updated_at
                ) VALUES (?, ?, ?, 'experience', ?, ?, ?, ?, NULL, ?, 'candidate', NULL, ?, ?)
                """,
                (new_mem_id, pid, row["worktree_id"], req.title, body, json.dumps(["conversation_excerpt", row["client_type"]]), req.actor, evidence_ref, now, now),
            )
            conn.execute(
                "INSERT INTO memories_fts (memory_id, project_id, worktree_id, title, body, tags) VALUES (?, ?, ?, ?, ?, ?)",
                (new_mem_id, pid, row["worktree_id"], req.title, body, f"conversation_excerpt {row['client_type']}"),
            )
        return {"memory_id": new_mem_id, "state": "candidate", "evidence_ref": evidence_ref}

    def delete_conversation(self, conv_id: str, project_id: str) -> dict[str, Any]:
        with self.db.write_tx() as conn:
            row = conn.execute("SELECT id FROM v3_conversations WHERE id = ? AND project_id = ?", (conv_id, project_id)).fetchone()
            if not row:
                raise BridgeError(404, "conversation_not_found", "Conversation not found")
            conn.execute("DELETE FROM v3_conversation_messages_fts WHERE conversation_id = ?", (conv_id,))
            conn.execute("DELETE FROM v3_conversations WHERE id = ?", (conv_id,))
        return {"deleted": True, "conversation_id": conv_id}
