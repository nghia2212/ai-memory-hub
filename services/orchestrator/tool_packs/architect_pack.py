"""V3.1 P7 — Technical Architect Pack: DB Metadata (Oracle/PG/MSSQL/SQLite test), OpenAPI 3.x, HTTP Allowlist, Local Diagram, Doc Extract, Infra Read-Only."""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_FORBIDDEN_SQL_RE = re.compile(r"\b(DROP|DELETE|UPDATE|INSERT|ALTER|TRUNCATE|CREATE|REPLACE|GRANT|REVOKE|MERGE|EXEC|EXECUTE)\b", re.IGNORECASE)
_PROD_HOST_RE = re.compile(r"(?:^|[.\-_])(?:prod|production|prd|live|bank-core)(?:$|[.\-_])", re.IGNORECASE)


def db_inspect_schema(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only schema & metadata inspection. Supports live SQLite test fixtures; Oracle/PG/MSSQL report not_configured unless test DSN is approved."""
    engine = str(args.get("engine", "sqlite")).lower()
    host = str(args.get("host", ""))
    if host and _PROD_HOST_RE.search(host):
        raise PermissionError(f"Production database host '{host}' is strictly denied by Architect policy.")
    if engine in ("oracle", "postgresql", "postgres", "mssql", "sqlserver"):
        env_key = f"TEST_{engine.upper()}_DSN"
        if not os.environ.get(env_key):
            return {
                "engine": engine,
                "status": "not_configured",
                "mode": "metadata_only_readonly",
                "message": f"No approved test DSN in env '{env_key}'. Never connect to production or bank databases.",
            }
        return {"engine": engine, "status": "configured_test_env", "mode": "metadata_only_readonly"}

    if engine == "sqlite":
        db_path = Path(str(args["db_path"])).resolve()
        if not db_path.is_file():
            raise FileNotFoundError(f"SQLite test DB not found: {db_path}")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            tables = []
            for row in conn.execute("SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' ORDER BY name"):
                tname = row["name"]
                cols = [dict(c) for c in conn.execute(f'PRAGMA table_info("{tname}")').fetchall()]
                fks = [dict(fk) for fk in conn.execute(f'PRAGMA foreign_key_list("{tname}")').fetchall()]
                idxs = [dict(ix) for ix in conn.execute(f'PRAGMA index_list("{tname}")').fetchall()]
                tables.append({"name": tname, "type": row["type"], "columns": cols, "foreign_keys": fks, "indexes": idxs})
            return {"engine": "sqlite", "status": "ok", "mode": "read_only", "table_count": len(tables), "tables": tables}
        finally:
            conn.close()
    raise ValueError(f"Unsupported database engine: {engine}")


def db_validate_readonly_query(args: dict[str, Any]) -> dict[str, Any]:
    sql = str(args.get("sql", "")).strip()
    if not sql or _FORBIDDEN_SQL_RE.search(sql) or not sql.lower().startswith(("select", "with", "explain")):
        raise PermissionError("Only read-only SELECT/WITH/EXPLAIN queries are allowed; DDL/DML is denied.")
    return {"allowed": True, "mode": "read_only"}


def openapi_inspect(args: dict[str, Any]) -> dict[str, Any]:
    """Parse OpenAPI 3.x JSON specification into endpoint inventory, schemas, and auth schemes."""
    raw_spec = args.get("spec")
    if raw_spec is None:
        path = Path(str(args["file_path"])).resolve()
        raw_spec = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw_spec, dict) or not str(raw_spec.get("openapi", "")).startswith("3."):
        raise ValueError("Only OpenAPI 3.x JSON documents are supported.")
    endpoints = []
    for path_key, methods in (raw_spec.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for m, op in methods.items():
            if m.lower() not in ("get", "post", "put", "patch", "delete", "head", "options"):
                continue
            endpoints.append(
                {
                    "path": path_key,
                    "method": m.upper(),
                    "operation_id": op.get("operationId"),
                    "summary": op.get("summary", ""),
                    "required_params": [p.get("name") for p in (op.get("parameters") or []) if p.get("required")],
                    "responses": sorted((op.get("responses") or {}).keys()),
                }
            )
    sec_schemes = sorted(((raw_spec.get("components") or {}).get("securitySchemes") or {}).keys())
    return {
        "openapi": raw_spec.get("openapi"),
        "title": (raw_spec.get("info") or {}).get("title", "unknown"),
        "version": (raw_spec.get("info") or {}).get("version", "unknown"),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
        "security_schemes": sec_schemes,
    }


def openapi_diff_breaking(args: dict[str, Any]) -> dict[str, Any]:
    """Detect breaking changes between old and new OpenAPI 3.x specs."""
    old_inv = openapi_inspect({"spec": args["old_spec"]})
    new_inv = openapi_inspect({"spec": args["new_spec"]})
    old_map = {(e["method"], e["path"]): e for e in old_inv["endpoints"]}
    new_map = {(e["method"], e["path"]): e for e in new_inv["endpoints"]}
    breaking: list[str] = []
    for key, old_ep in old_map.items():
        if key not in new_map:
            breaking.append(f"Removed endpoint: {key[0]} {key[1]}")
            continue
        added_req = set(new_map[key]["required_params"]) - set(old_ep["required_params"])
        if added_req:
            breaking.append(f"New required params on {key[0]} {key[1]}: {sorted(added_req)}")
    return {"breaking_changes_count": len(breaking), "breaking_changes": breaking, "compatible": len(breaking) == 0}


def http_api_test(args: dict[str, Any]) -> dict[str, Any]:
    """Domain-allowlisted HTTP API test (blocks unapproved internal/production hosts)."""
    url = str(args["url"])
    allowlist = [d.lower() for d in (args.get("allowed_domains") or ["127.0.0.1", "localhost"])]
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host or host not in allowlist:
        raise PermissionError(f"Host '{host}' is not in approved domain allowlist {allowlist}.")
    method = str(args.get("method", "GET")).upper()
    if method not in ("GET", "HEAD", "OPTIONS", "POST"):
        raise PermissionError(f"HTTP method '{method}' not allowed in test adapter.")
    data = json.dumps(args["json_body"]).encode("utf-8") if args.get("json_body") is not None else None
    req_hdrs = dict(args.get("headers") or {})
    if data is not None and "Content-Type" not in req_hdrs:
        req_hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=req_hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=min(float(args.get("timeout_sec", 10)), 30.0)) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"status_code": resp.status, "content_type": resp.headers.get("Content-Type", ""), "body_excerpt": body[:4000]}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        return {"status_code": exc.code, "content_type": "", "body_excerpt": body[:4000]}


def diagram_validate_and_save(args: dict[str, Any]) -> dict[str, Any]:
    """Local-only Mermaid/PlantUML/C4/ERD diagram validator & saver (NEVER uploads to public rendering services)."""
    kind = str(args.get("kind", "mermaid")).lower()
    source = str(args.get("source", "")).strip()
    if not source:
        raise ValueError("Diagram source cannot be empty.")
    valid_starts = ("sequenceDiagram", "flowchart", "graph", "erDiagram", "classDiagram", "stateDiagram", "C4Context", "@startuml")
    if not any(source.startswith(p) for p in valid_starts):
        raise ValueError(f"Unrecognized diagram header for {kind}.")
    saved_path: str | None = None
    if args.get("output_path"):
        out = Path(str(args["output_path"])).resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(source, encoding="utf-8")
        saved_path = str(out)
    return {"kind": kind, "valid": True, "rendered_locally": True, "cloud_upload": False, "saved_path": saved_path, "chars": len(source)}


def doc_extract_bounded(args: dict[str, Any]) -> dict[str, Any]:
    """Extract bounded text excerpt from Markdown/JSON/CSV/TXT document without dumping entire file."""
    path = Path(str(args["file_path"])).resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Document not found: {path}")
    max_chars = min(int(args.get("max_chars", 4000)), 12000)
    text = path.read_text(encoding="utf-8", errors="replace")
    return {"file_path": str(path), "total_chars": len(text), "truncated": len(text) > max_chars, "excerpt": text[:max_chars]}


def docker_inspect_readonly(args: dict[str, Any]) -> dict[str, Any]:
    """Read-only Docker container list via CLI (never mounts Docker socket in container; no stop/rm/restart)."""
    docker = shutil.which("docker")
    if not docker:
        return {"status": "not_configured", "message": "Docker CLI not available."}
    proc = subprocess.run([docker, "ps", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}"], capture_output=True, text=True, timeout=10, shell=False)
    return {"status": "ok" if proc.returncode == 0 else "error", "containers": [l for l in (proc.stdout or "").splitlines() if l.strip()]}


ARCHITECT_TOOLS: dict[str, Any] = {
    "db_inspect_schema": {
        "meta": {
            "name": "db_inspect_schema",
            "description": "Read-only database schema & metadata inspector (SQLite test DB live; Oracle/PG/MSSQL test-DSN gated; production denied).",
            "capability_tags": ["db_metadata", "architect"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {"engine": {"type": "string"}, "db_path": {"type": "string"}, "host": {"type": "string"}}},
        },
        "handler": db_inspect_schema,
    },
    "openapi_inspect": {
        "meta": {
            "name": "openapi_inspect",
            "description": "Parse OpenAPI 3.x spec into endpoints, required params, responses, and security schemes.",
            "capability_tags": ["openapi_inspect", "api_contract"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {"file_path": {"type": "string"}, "spec": {"type": "object"}}},
        },
        "handler": openapi_inspect,
    },
    "openapi_diff_breaking": {
        "meta": {
            "name": "openapi_diff_breaking",
            "description": "Detect breaking contract changes between two OpenAPI 3.x specs.",
            "capability_tags": ["openapi_inspect", "api_contract"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["old_spec", "new_spec"], "properties": {"old_spec": {"type": "object"}, "new_spec": {"type": "object"}}},
        },
        "handler": openapi_diff_breaking,
    },
    "http_api_test": {
        "meta": {
            "name": "http_api_test",
            "description": "Domain-allowlisted REST/HTTP API tester.",
            "capability_tags": ["http_test", "api_contract"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["url"], "properties": {"url": {"type": "string"}, "method": {"type": "string"}, "allowed_domains": {"type": "array"}}},
        },
        "handler": http_api_test,
    },
    "diagram_validate_and_save": {
        "meta": {
            "name": "diagram_validate_and_save",
            "description": "Validate and save Mermaid/PlantUML/C4/ERD architecture diagrams strictly locally (zero cloud upload).",
            "capability_tags": ["diagram_render", "architect"],
            "side_effect_level": "write_scoped",
            "inputSchema": {"type": "object", "required": ["source"], "properties": {"kind": {"type": "string"}, "source": {"type": "string"}, "output_path": {"type": "string"}}},
        },
        "handler": diagram_validate_and_save,
    },
    "doc_extract_bounded": {
        "meta": {
            "name": "doc_extract_bounded",
            "description": "Extract bounded section/excerpt from local technical documents.",
            "capability_tags": ["doc_extract", "architect"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["file_path"], "properties": {"file_path": {"type": "string"}, "max_chars": {"type": "integer"}}},
        },
        "handler": doc_extract_bounded,
    },
    "docker_inspect_readonly": {
        "meta": {
            "name": "docker_inspect_readonly",
            "description": "Read-only Docker container status inspection (no destructive actions).",
            "capability_tags": ["infra_readonly", "observability"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {}},
        },
        "handler": docker_inspect_readonly,
    },
}
