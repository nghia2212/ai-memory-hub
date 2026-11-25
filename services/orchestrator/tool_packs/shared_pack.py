"""V3.1 P4 — Shared Tool Pack: Git, Safe Filesystem, GitHub (env-gated), Official Documentation Index."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

_SENSITIVE_FILENAMES = frozenset({".env", ".env.local", ".env.production", "id_rsa", "id_ed25519", "credentials.json", ".npmrc", ".pypirc"})
_SENSITIVE_DIRS = frozenset({".ssh", ".aws", ".gnupg"})

_OFFICIAL_DOCS: dict[str, dict[str, str]] = {
    "java": {"title": "Oracle / OpenJDK Java Documentation", "url": "https://docs.oracle.com/en/java/javase/21/docs/api/"},
    "spring-boot": {"title": "Spring Boot Reference Documentation", "url": "https://docs.spring.io/spring-boot/index.html"},
    "hibernate": {"title": "Hibernate ORM Documentation", "url": "https://hibernate.org/orm/documentation/"},
    "oracle": {"title": "Oracle Database Documentation", "url": "https://docs.oracle.com/en/database/oracle/oracle-database/"},
    "typescript": {"title": "TypeScript Handbook & Compiler API", "url": "https://www.typescriptlang.org/docs/"},
    "threejs": {"title": "Three.js Documentation", "url": "https://threejs.org/docs/"},
    "phaser": {"title": "Phaser 3 API Documentation", "url": "https://newdocs.phaser.io/docs/3.80.0"},
    "blender": {"title": "Blender Python API (bpy)", "url": "https://docs.blender.org/api/current/"},
    "cloudflare": {"title": "Cloudflare Pages & Wrangler Docs", "url": "https://developers.cloudflare.com/pages/"},
    "docker": {"title": "Docker Compose & Engine Docs", "url": "https://docs.docker.com/"},
    "mcp": {"title": "Model Context Protocol Specification", "url": "https://modelcontextprotocol.io/specification"},
}


def _safe_resolve(approved_root: str, rel_path: str = "") -> Path:
    root = Path(approved_root).resolve()
    if not root.is_dir():
        raise PermissionError(f"Approved root does not exist: {approved_root}")
    if not rel_path:
        return root
    if rel_path.startswith(("/", "\\")) or ":" in rel_path.split("/")[0]:
        raise PermissionError(f"Absolute path forbidden: {rel_path}")
    norm = rel_path.replace("\\", "/")
    if ".." in Path(norm).parts:
        raise PermissionError(f"Path traversal forbidden: {rel_path}")
    target = root / norm
    if target.is_symlink():
        res = target.resolve()
        res.relative_to(root)
    res = target.resolve()
    res.relative_to(root)
    if res.name.lower() in _SENSITIVE_FILENAMES or any(p.lower() in _SENSITIVE_DIRS for p in res.parts):
        raise PermissionError(f"Access to sensitive file/directory is denied: {rel_path}")
    return res


def git_inspect(args: dict[str, Any]) -> dict[str, Any]:
    repo = Path(str(args["repo_path"])).resolve()
    action = str(args.get("action", "status"))
    cmd_map = {
        "status": ["git", "status", "--short", "--branch"],
        "log": ["git", "log", f"-n{min(int(args.get('limit', 10)), 50)}", "--oneline"],
        "branch": ["git", "branch", "-vv"],
        "worktree": ["git", "worktree", "list"],
        "diff_stat": ["git", "diff", "--stat"],
    }
    if action not in cmd_map:
        raise ValueError(f"Unsupported read-only git action: {action}")
    proc = subprocess.run(cmd_map[action], cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20, shell=False)
    return {"action": action, "exit_code": proc.returncode, "output": (proc.stdout or "")[:8000], "stderr": (proc.stderr or "")[:1000]}


def fs_read_bounded(args: dict[str, Any]) -> dict[str, Any]:
    target = _safe_resolve(str(args["approved_root"]), str(args["rel_path"]))
    if not target.is_file():
        raise FileNotFoundError(f"File not found: {args['rel_path']}")
    start_line = max(1, int(args.get("start_line", 1)))
    end_line = min(start_line + 250, int(args.get("end_line", start_line + 100)))
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    selected = lines[start_line - 1 : end_line]
    return {
        "path": str(args["rel_path"]),
        "total_lines": len(lines),
        "start_line": start_line,
        "end_line": min(end_line, len(lines)),
        "content": "\n".join(selected)[:12000],
    }


def github_status_check(args: dict[str, Any]) -> dict[str, Any]:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if not token:
        return {"status": "not_configured", "message": "GITHUB_TOKEN is not configured in approved environment."}
    return {"status": "configured", "repo": args.get("repo", ""), "auth_mode": "env_secret_ref"}


def doc_lookup(args: dict[str, Any]) -> dict[str, Any]:
    topic = str(args.get("topic", "")).strip().lower()
    matches = {k: v for k, v in _OFFICIAL_DOCS.items() if not topic or topic in k or topic in v["title"].lower()}
    return {"topic": topic, "official_sources": matches, "paid_api_used": False}


SHARED_TOOLS: dict[str, Any] = {
    "git_inspect": {
        "meta": {
            "name": "git_inspect",
            "description": "Read-only Git inspection (status, log, branch, worktree, diff_stat).",
            "capability_tags": ["git_read", "vcs"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["repo_path"], "properties": {"repo_path": {"type": "string"}, "action": {"type": "string"}, "limit": {"type": "integer"}}},
        },
        "handler": git_inspect,
    },
    "fs_read_bounded": {
        "meta": {
            "name": "fs_read_bounded",
            "description": "Read bounded line range from file inside approved root; blocks traversal, symlinks, and .env/SSH secrets.",
            "capability_tags": ["fs_read", "code_read"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "required": ["approved_root", "rel_path"], "properties": {"approved_root": {"type": "string"}, "rel_path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}}},
        },
        "handler": fs_read_bounded,
    },
    "github_status_check": {
        "meta": {
            "name": "github_status_check",
            "description": "Check GitHub adapter credential readiness without exposing token.",
            "capability_tags": ["github_read"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {"repo": {"type": "string"}}},
        },
        "handler": github_status_check,
    },
    "doc_lookup": {
        "meta": {
            "name": "doc_lookup",
            "description": "Official documentation reference lookup for Java, Spring Boot, Oracle, TypeScript, Three.js, Phaser, Blender, Docker, MCP.",
            "capability_tags": ["docs_lookup"],
            "side_effect_level": "read_only",
            "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}},
        },
        "handler": doc_lookup,
    },
}
