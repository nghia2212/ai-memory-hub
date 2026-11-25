"""Configuration loaded from environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _parse_tokens(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {t.strip() for t in raw.split(",") if t.strip()}


@dataclass(frozen=True)
class Settings:
    port: int = 8081
    service_name: str = "codebase"
    data_dir: Path = Path("/data")
    cbm_bin: str = "/usr/local/bin/codebase-memory-mcp"
    cbm_cache_dir: Path = Path("/data/cbm-cache")
    cbm_allowed_root: Path = Path("/repos")
    registry_file: Path | None = None
    bridge_url: str | None = None
    bridge_token: str | None = None
    read_tokens: set[str] = field(default_factory=set)
    admin_tokens: set[str] = field(default_factory=set)
    allow_unauth_dev: bool = False
    query_timeout_sec: float = 25.0
    index_timeout_sec: float = 120.0

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        cbm_cache = Path(os.environ.get("CBM_CACHE_DIR", str(data_dir / "cbm-cache")))
        reg_raw = os.environ.get("REGISTRY_FILE", "").strip()
        bridge_raw = os.environ.get("BRIDGE_URL", "").strip()
        return cls(
            port=int(os.environ.get("PORT", "8081")),
            service_name=os.environ.get("SERVICE_NAME", "codebase"),
            data_dir=data_dir,
            cbm_bin=os.environ.get("CBM_BIN", "/usr/local/bin/codebase-memory-mcp"),
            cbm_cache_dir=cbm_cache,
            cbm_allowed_root=Path(os.environ.get("CBM_ALLOWED_ROOT", "/repos")),
            registry_file=Path(reg_raw) if reg_raw else None,
            bridge_url=bridge_raw or None,
            bridge_token=os.environ.get("BRIDGE_TOKEN", "").strip() or None,
            read_tokens=_parse_tokens(os.environ.get("CODEBASE_READ_TOKENS")),
            admin_tokens=_parse_tokens(os.environ.get("CODEBASE_ADMIN_TOKENS")),
            allow_unauth_dev=os.environ.get("ALLOW_UNAUTH_DEV", "").lower() in ("1", "true", "yes"),
            query_timeout_sec=float(os.environ.get("QUERY_TIMEOUT_SEC", "25.0")),
            index_timeout_sec=float(os.environ.get("INDEX_TIMEOUT_SEC", "120.0")),
        )
