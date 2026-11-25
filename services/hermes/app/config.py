"""Configuration loader for Hermes worker service."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_csv(value: str | None) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class HermesConfig:
    service_name: str = "hermes"
    port: int = 8083
    enabled: bool = False
    provider: str = "mock"  # mock | deepseek | hermes_gateway
    bridge_url: str = "http://memory-bridge:8082"
    bridge_worker_token: str = ""
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    hermes_gateway_url: str = ""
    approved_projects: frozenset[str] = field(default_factory=frozenset)
    poll_interval_seconds: float = 30.0
    request_timeout_seconds: float = 15.0
    max_retries: int = 2
    max_events_per_run: int = 10
    max_input_chars: int = 2000
    max_output_tokens: int = 400
    max_total_tokens_per_run: int = 8000
    state_dir: str = ""

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "HermesConfig":
        src = env if env is not None else os.environ
        return cls(
            service_name=src.get("SERVICE_NAME", "hermes"),
            port=int(src.get("PORT", "8083")),
            enabled=_parse_bool(src.get("HERMES_ENABLED"), default=False),
            provider=src.get("HERMES_PROVIDER", "mock").strip().lower() or "mock",
            bridge_url=src.get("BRIDGE_URL", "http://memory-bridge:8082").rstrip("/"),
            bridge_worker_token=src.get("BRIDGE_WORKER_TOKEN", ""),
            deepseek_api_key=src.get("DEEPSEEK_API_KEY", ""),
            deepseek_base_url=src.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/"),
            deepseek_model=src.get("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash",
            hermes_gateway_url=src.get("HERMES_GATEWAY_URL", "").rstrip("/"),
            approved_projects=_parse_csv(src.get("HERMES_APPROVED_PROJECTS")),
            poll_interval_seconds=max(1.0, float(src.get("HERMES_POLL_INTERVAL_SECONDS", "30"))),
            request_timeout_seconds=max(1.0, float(src.get("HERMES_REQUEST_TIMEOUT_SECONDS", "15"))),
            max_retries=max(0, min(5, int(src.get("HERMES_MAX_RETRIES", "2")))),
            max_events_per_run=max(1, min(50, int(src.get("HERMES_MAX_EVENTS_PER_RUN", "10")))),
            max_input_chars=max(200, min(4000, int(src.get("HERMES_MAX_INPUT_CHARS", "2000")))),
            max_output_tokens=max(64, min(1024, int(src.get("HERMES_MAX_OUTPUT_TOKENS", "400")))),
            max_total_tokens_per_run=max(
                500, min(50000, int(src.get("HERMES_MAX_TOTAL_TOKENS_PER_RUN", "8000")))
            ),
            state_dir=src.get("HERMES_STATE_DIR", "").strip(),
        )

    def validate_for_run(self) -> tuple[bool, str]:
        """Return (is_ready, reason) without leaking secrets."""
        if not self.enabled:
            return True, "disabled"
        if self.provider == "mock":
            return True, "ready_mock"
        if self.provider == "deepseek":
            if not self.deepseek_api_key or self.deepseek_api_key.startswith("replace_me"):
                return False, "missing_deepseek_api_key"
            if not self.deepseek_base_url.startswith("https://"):
                return False, "invalid_deepseek_base_url"
            return True, "ready_deepseek"
        if self.provider == "hermes_gateway":
            if not self.hermes_gateway_url:
                return False, "missing_hermes_gateway_url"
            return True, "ready_hermes_gateway"
        return False, f"unsupported_provider:{self.provider}"
