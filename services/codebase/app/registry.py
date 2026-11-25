"""Allowlist registry for approved projects and worktrees."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID

import httpx
import yaml

from .config import Settings
from .models import WorktreeEntry

logger = logging.getLogger(__name__)


def cbm_project_name(project_id: UUID, worktree_id: UUID) -> str:
    """Deterministic, collision-free CBM project name per (project_id, worktree_id)."""
    return f"cbm_{project_id.hex}_{worktree_id.hex}"


class WorktreeRegistry:
    """Resolves approved (project_id, worktree_id) scopes from local allowlist or Bridge."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._entries: dict[tuple[UUID, UUID], WorktreeEntry] = {}

    def register(self, entry: WorktreeEntry) -> None:
        self._entries[(entry.project_id, entry.worktree_id)] = entry

    def load_from_file(self, path: Path) -> None:
        if not path.is_file():
            logger.warning("Registry file does not exist")
            return
        raw_text = path.read_text(encoding="utf-8")
        data = json.loads(raw_text) if path.suffix.lower() == ".json" else yaml.safe_load(raw_text)
        if not isinstance(data, dict):
            return
        for item in data.get("worktrees", []):
            entry = WorktreeEntry.model_validate(item)
            self.register(entry)

    def _normalize_bridge_item(self, project_id: UUID, item: dict) -> dict:
        normalized = dict(item)
        normalized.setdefault("project_id", str(project_id))
        if "worktree_id" not in normalized and "id" in normalized:
            normalized["worktree_id"] = normalized["id"]
        if not normalized.get("canonical_repo_id"):
            normalized["canonical_repo_id"] = str(
                normalized.get("canonical_identity") or normalized.get("project_id") or project_id
            )
        canonical_root = str(normalized.get("canonical_root") or "")
        if not normalized.get("host_root"):
            normalized["host_root"] = canonical_root or str(normalized.get("container_root") or "")
        if not normalized.get("container_root"):
            wt_id = str(normalized.get("worktree_id") or "")
            allowed = self._settings.cbm_allowed_root
            candidate = (allowed / wt_id) if (allowed and wt_id) else None
            if candidate is not None and candidate.is_dir():
                normalized["container_root"] = str(candidate)
            else:
                normalized["container_root"] = canonical_root
        return normalized

    async def refresh_from_bridge(self, project_id: UUID) -> None:
        if not self._settings.bridge_url:
            return
        headers = {}
        if self._settings.bridge_token:
            headers["Authorization"] = f"Bearer {self._settings.bridge_token}"
        url = f"{self._settings.bridge_url.rstrip('/')}/v1/projects/{project_id}/worktrees"
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    items = payload
                elif isinstance(payload, dict):
                    items = payload.get("items") or payload.get("worktrees") or []
                else:
                    items = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    entry = WorktreeEntry.model_validate(self._normalize_bridge_item(project_id, item))
                    self.register(entry)
        except Exception as exc:
            logger.warning("Bridge registry lookup failed: %s", type(exc).__name__)

    async def get(self, project_id: UUID, worktree_id: UUID) -> WorktreeEntry | None:
        key = (project_id, worktree_id)
        if key in self._entries:
            return self._entries[key]
        if self._settings.registry_file:
            self.load_from_file(self._settings.registry_file)
            if key in self._entries:
                return self._entries[key]
        if self._settings.bridge_url:
            await self.refresh_from_bridge(project_id)
            if key in self._entries:
                return self._entries[key]
        return None
