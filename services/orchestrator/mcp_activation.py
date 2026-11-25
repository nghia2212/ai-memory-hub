"""V3.1 P3.5 — Agent MCP Activation & Client Inspection Adapter.

Rules:
- NEVER silently mutate user's global client config (~/.claude.json, ~/.codex/config.toml, ~/.gemini/antigravity/mcp_config.json).
- Generate session-scoped config artifacts or Gateway proxy references.
- Report exact client detection, hot-reload capability, and enforcement coverage limitations honestly.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any


CLIENT_SPECS: list[dict[str, Any]] = [
    {
        "client": "antigravity",
        "cli": "agy",
        "transport_support": ["streamable_http", "stdio"],
        "hot_reload": False,
        "session_config_mode": "gateway_or_mcp_config_snippet",
        "enforcement_coverage": "partial_external_client",
        "notes": "Antigravity IDE/agy reads MCP config at session/window load; use Gateway URL or reload window to apply new MCP servers. Never silently overwrite global mcp_config.json.",
    },
    {
        "client": "claude_code",
        "cli": "claude",
        "transport_support": ["streamable_http", "stdio"],
        "hot_reload": False,
        "session_config_mode": "cli_mcp_config_flag",
        "enforcement_coverage": "enforced_via_session_flag_and_gateway",
        "notes": "Claude Code supports per-session `--mcp-config <temp_file.json>` at launch without mutating global ~/.claude.json.",
    },
    {
        "client": "codex_cli",
        "cli": "codex",
        "transport_support": ["streamable_http", "stdio"],
        "hot_reload": False,
        "session_config_mode": "config_override_or_gateway",
        "enforcement_coverage": "partial_external_client",
        "notes": "Codex CLI reads MCP settings at launch; requires restart/new session to pick up added servers.",
    },
    {
        "client": "gemini_cli",
        "cli": "gemini",
        "transport_support": ["streamable_http", "stdio"],
        "hot_reload": False,
        "session_config_mode": "project_settings_or_gateway",
        "enforcement_coverage": "partial_external_client",
        "notes": "Gemini CLI supports project-level .gemini/settings.json or Gateway endpoint; requires new session if changed.",
    },
    {
        "client": "hermes",
        "cli": "hermes",
        "transport_support": ["streamable_http"],
        "hot_reload": True,
        "session_config_mode": "hub_gateway_native",
        "enforcement_coverage": "full_hub_gateway",
        "notes": "Hermes optional worker (disabled by default) routes all tool calls through Memory Bridge / Gateway.",
    },
]


def _detect_version(exe: str) -> str | None:
    try:
        proc = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=5, shell=False, check=False)
        out = (proc.stdout or proc.stderr or "").strip().splitlines()
        return out[0][:120] if out else None
    except Exception:
        return None


def inspect_agent_clients() -> list[dict[str, Any]]:
    """Probe actual locally installed AI agent clients on this host."""
    results: list[dict[str, Any]] = []
    for spec in CLIENT_SPECS:
        found = shutil.which(spec["cli"])
        results.append(
            {
                **spec,
                "installed": bool(found),
                "executable_path": found,
                "version": _detect_version(found) if found else None,
            }
        )
    return results


def generate_session_mcp_config(
    *,
    client: str,
    selected_servers: list[dict[str, Any]],
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Generate a session-scoped MCP configuration payload without touching global user configs."""
    mcp_servers: dict[str, Any] = {}
    for s in selected_servers:
        name = s["name"]
        transport = s.get("transport", "streamable_http")
        if transport == "streamable_http":
            mcp_servers[name] = {
                "type": "http",
                "url": s["endpoint_or_cmd"],
            }
        elif transport == "stdio":
            mcp_servers[name] = {
                "command": s.get("executable") or s["endpoint_or_cmd"],
                "args": s.get("args") or [],
            }
    config_doc = {"mcpServers": mcp_servers}
    written_to: str | None = None
    if output_path is not None:
        p = Path(output_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(config_doc, indent=2, ensure_ascii=False), encoding="utf-8")
        written_to = str(p)
    spec = next((c for c in CLIENT_SPECS if c["client"] == client), None)
    return {
        "client": client,
        "activated_live": False,
        "requires_session_launch_or_reload": not (spec["hot_reload"] if spec else False),
        "enforcement_coverage": spec["enforcement_coverage"] if spec else "external_unmanaged",
        "written_path": written_to,
        "config": config_doc,
    }
