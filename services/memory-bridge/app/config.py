"""Configuration for Memory Bridge service."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _split_tokens(raw: str) -> set[str]:
    return {t.strip() for t in raw.split(",") if t.strip()}


@dataclass(frozen=True)
class Settings:
    service_name: str = field(default_factory=lambda: os.environ.get("SERVICE_NAME", "memory-bridge"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8082")))
    host: str = field(default_factory=lambda: os.environ.get("HOST", "0.0.0.0"))
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("BRIDGE_DATA_DIR", "/data")))
    db_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("BRIDGE_DB_PATH", str(Path(os.environ.get("BRIDGE_DATA_DIR", "/data")) / "memory_bridge.db"))
        )
    )
    backup_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("BRIDGE_BACKUP_DIR", str(Path(os.environ.get("BRIDGE_DATA_DIR", "/data")) / "backups"))
        )
    )
    codebase_url: str | None = field(default_factory=lambda: os.environ.get("CODEBASE_URL") or None)
    codebase_token: str | None = field(default_factory=lambda: os.environ.get("CODEBASE_TOKEN") or None)
    read_tokens: set[str] = field(default_factory=lambda: _split_tokens(os.environ.get("BRIDGE_READ_TOKENS", "")))
    write_tokens: set[str] = field(default_factory=lambda: _split_tokens(os.environ.get("BRIDGE_WRITE_TOKENS", "")))
    admin_tokens: set[str] = field(default_factory=lambda: _split_tokens(os.environ.get("BRIDGE_ADMIN_TOKENS", "")))
    worker_tokens: set[str] = field(default_factory=lambda: _split_tokens(os.environ.get("BRIDGE_WORKER_TOKENS", "")))
    default_limit: int = 5
    max_limit: int = 10
    default_max_chars: int = 4000
    max_chars_cap: int = 8000
    max_query_chars: int = 512
    max_payload_bytes: int = 65536


def load_settings() -> Settings:
    return Settings()
