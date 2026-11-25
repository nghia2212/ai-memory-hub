"""V3.2 (SK32) P5 — Provider Skill Adapters with Managed Namespace, Atomic Update, Backup & Honest Capability Labels.

Rules:
- Never overwrite unrelated user skills or global configs silently.
- Write Hub-managed skills ONLY inside `<target_dir>/hub-managed/<slug>/SKILL.md` with `.hub-managed-manifest.json`.
- Backup existing managed files before updating; remove ONLY Hub-owned files.
- Never auto-install third-party packages, run shell commands, download from network, or call paid APIs.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

ADAPTER_MANIFESTS: list[dict[str, Any]] = [
    {
        "adapter": "claude_code",
        "cli": "claude",
        "native_skill_support": True,
        "delivery_mode": "managed_skill_dir_or_context",
        "supports_discovery": True,
        "supports_load_evidence": False,
        "notes": "Claude Code supports project/user SKILL.md directories; Hub syncs strictly into `hub-managed/` subdirectory with backup.",
    },
    {
        "adapter": "antigravity",
        "cli": "agy",
        "native_skill_support": True,
        "delivery_mode": "managed_skill_dir_or_context",
        "supports_discovery": True,
        "supports_load_evidence": False,
        "notes": "Antigravity reads SKILL.md folders; Hub manages only `hub-managed/` namespace and preserves unrelated user skills.",
    },
    {
        "adapter": "codex_cli",
        "cli": "codex",
        "native_skill_support": False,
        "delivery_mode": "bounded_context_adapter",
        "supports_discovery": False,
        "supports_load_evidence": False,
        "notes": "Codex CLI receives resolved skills via bounded progressive-disclosure prompt context; native skill auto-load is not assumed.",
    },
    {
        "adapter": "gemini_cli",
        "cli": "gemini",
        "native_skill_support": False,
        "delivery_mode": "bounded_context_adapter",
        "supports_discovery": False,
        "supports_load_evidence": False,
        "notes": "Gemini CLI uses bounded context injection unless native skill format is explicitly verified on installed version.",
    },
    {
        "adapter": "deepseek_api",
        "cli": None,
        "native_skill_support": False,
        "delivery_mode": "bounded_context_adapter",
        "supports_discovery": False,
        "supports_load_evidence": False,
        "notes": "Paid API disabled by default; uses bounded context injection only when user explicitly opts in.",
    },
    {
        "adapter": "qwen_local",
        "cli": None,
        "native_skill_support": False,
        "delivery_mode": "bounded_context_adapter",
        "supports_discovery": False,
        "supports_load_evidence": False,
        "notes": "Local model adapter uses bounded context injection; no native skill assumption.",
    },
]


def inspect_skill_adapters() -> list[dict[str, Any]]:
    out = []
    for m in ADAPTER_MANIFESTS:
        cli = m["cli"]
        found = shutil.which(cli) if cli else None
        out.append({**m, "installed": bool(found) if cli else False, "executable_path": found})
    return out


def sync_managed_skills(
    *,
    target_root: str | Path,
    resolved_skills: list[dict[str, Any]],
    preview_only: bool = True,
) -> dict[str, Any]:
    """Preview or atomically sync resolved skills into `<target_root>/hub-managed/` without touching unrelated files."""
    root = Path(target_root).resolve()
    managed_dir = root / "hub-managed"
    manifest_file = managed_dir / ".hub-managed-manifest.json"
    prev_owned: dict[str, str] = {}
    if manifest_file.is_file():
        try:
            prev_owned = json.loads(manifest_file.read_text(encoding="utf-8")).get("skills", {})
        except Exception:
            prev_owned = {}

    desired: dict[str, dict[str, Any]] = {}
    for sk in resolved_skills:
        slug = str(sk["slug"])
        desired[slug] = {
            "skill_id": sk["skill_id"],
            "version": sk["version"],
            "content_hash": sk["content_hash"],
            "markdown_body": sk["markdown_body"],
        }

    to_add_or_update = [s for s, info in desired.items() if prev_owned.get(s) != info["content_hash"]]
    to_remove = [s for s in prev_owned if s not in desired]

    if preview_only:
        return {
            "preview_only": True,
            "managed_dir": str(managed_dir),
            "add_or_update": to_add_or_update,
            "remove_hub_owned_only": to_remove,
            "preserves_unrelated_user_skills": True,
        }

    backup_dir: str | None = None
    if managed_dir.exists():
        bk = root / f".hub-managed-backup-{int(time.time())}"
        shutil.copytree(managed_dir, bk)
        backup_dir = str(bk)
    managed_dir.mkdir(parents=True, exist_ok=True)

    # Remove ONLY previously Hub-owned skill folders
    for slug in to_remove:
        old_dir = managed_dir / slug
        if old_dir.is_dir():
            shutil.rmtree(old_dir)

    # Atomic write per skill
    new_owned: dict[str, str] = {}
    for slug, info in desired.items():
        sdir = managed_dir / slug
        sdir.mkdir(parents=True, exist_ok=True)
        tmp_file = sdir / "SKILL.md.tmp"
        final_file = sdir / "SKILL.md"
        tmp_file.write_text(info["markdown_body"], encoding="utf-8")
        tmp_file.replace(final_file)
        new_owned[slug] = info["content_hash"]

    tmp_man = managed_dir / ".hub-managed-manifest.json.tmp"
    tmp_man.write_text(json.dumps({"owner": "ai-memory-hub-sk32", "skills": new_owned}, indent=2), encoding="utf-8")
    tmp_man.replace(manifest_file)

    return {
        "preview_only": False,
        "managed_dir": str(managed_dir),
        "backup_dir": backup_dir,
        "written": sorted(new_owned.keys()),
        "removed": to_remove,
        "delivery_status": "delivered",
        "load_status": "unknown",  # Never infer execution/load merely from file delivery!
    }
