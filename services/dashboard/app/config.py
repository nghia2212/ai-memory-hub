"""Configuration for Dashboard service."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DashboardSettings:
    service_name: str = field(default_factory=lambda: os.environ.get("SERVICE_NAME", "dashboard"))
    port: int = field(default_factory=lambda: int(os.environ.get("PORT", "8080")))
    bridge_url: str = field(default_factory=lambda: os.environ.get("BRIDGE_URL", "http://127.0.0.1:8082"))
    codebase_url: str = field(default_factory=lambda: os.environ.get("CODEBASE_URL", "http://127.0.0.1:8081"))
    hermes_url: str = field(default_factory=lambda: os.environ.get("HERMES_URL", "http://127.0.0.1:8083"))
    hermes_enabled: bool = field(
        default_factory=lambda: os.environ.get("HERMES_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    )
    hermes_provider: str = field(default_factory=lambda: os.environ.get("HERMES_PROVIDER", "deepseek").strip().lower() or "deepseek")
    deepseek_api_key_set: bool = field(
        default_factory=lambda: os.environ.get("DEEPSEEK_API_KEY_SET", "").strip().lower() in {"1", "true", "yes", "on"}
    )
    bridge_read_token: str = field(default_factory=lambda: os.environ.get("BRIDGE_READ_TOKEN", ""))
    bridge_admin_token: str = field(default_factory=lambda: os.environ.get("BRIDGE_ADMIN_TOKEN", ""))
    bridge_runner_token: str = field(default_factory=lambda: os.environ.get("BRIDGE_RUNNER_TOKEN", ""))
    host_brain_url: str = field(default_factory=lambda: os.environ.get("HOST_BRAIN_URL", "http://host.docker.internal:8084"))
    dashboard_password: str = field(default_factory=lambda: os.environ.get("DASHBOARD_PASSWORD", ""))
    session_secret: str = field(
        default_factory=lambda: os.environ.get("DASHBOARD_SESSION_SECRET") or secrets.token_hex(32)
    )
    allowed_origins: set[str] = field(
        default_factory=lambda: {
            o.strip()
            for o in os.environ.get("DASHBOARD_ALLOWED_ORIGINS", "http://127.0.0.1:8080,http://localhost:8080").split(",")
            if o.strip()
        }
    )


def load_dashboard_settings() -> DashboardSettings:
    return DashboardSettings()
