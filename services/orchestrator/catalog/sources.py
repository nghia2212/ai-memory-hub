"""Official-source catalog adapters and host CLI capability mapper."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from urllib.parse import urlparse

import httpx

from services.orchestrator.contracts import HostCapabilityReport, ModelRecord

ALLOWED_SOURCE_HOSTS: frozenset[str] = frozenset(
    {
        "platform.claude.com",
        "docs.anthropic.com",
        "developers.openai.com",
        "platform.openai.com",
        "ai.google.dev",
    }
)

MAX_SOURCE_BYTES = 1_048_576  # 1 MiB safety cap


class SourceFetchError(RuntimeError):
    """Raised when an official source cannot be fetched or fails validation."""


FetcherFn = Callable[[str, float, int], bytes]


def default_http_fetcher(url: str, timeout: float, max_bytes: int) -> bytes:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
        raise SourceFetchError(f"Source URL not in allowlist: {parsed.hostname}")
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
        if len(content) > max_bytes:
            raise SourceFetchError("Source payload exceeds max_bytes limit")
        return content


class OfficialCatalogAdapter(Protocol):
    provider: str
    source_url: str

    def fetch_models(self, now_iso: str | None = None) -> list[ModelRecord]: ...


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_json_models(
    raw: bytes,
    *,
    provider: str,
    source_url: str,
    verified_at: str,
    default_auth_mode: str = "api_key",
) -> list[ModelRecord]:
    """Parse structured official catalog JSON as untrusted data (no eval/exec)."""
    if len(raw) > MAX_SOURCE_BYTES:
        raise SourceFetchError("Payload exceeds MAX_SOURCE_BYTES")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceFetchError("Invalid JSON catalog payload") from exc

    items = payload.get("models", []) if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise SourceFetchError("Catalog payload must be a list or {models: []}")

    records: list[ModelRecord] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id", "")).strip()
        if not model_id:
            continue
        raw_status = str(item.get("status", "candidate")).strip()
        status = raw_status if raw_status in ("candidate", "verified", "deprecated", "unavailable") else "candidate"
        auth_mode = str(item.get("auth_mode", default_auth_mode)).strip()
        if auth_mode not in ("api_key", "subscription_cli", "oauth", "unknown"):
            auth_mode = "unknown"
        records.append(
            ModelRecord(
                provider=provider,
                model_id=model_id,
                cli_family=item.get("cli_family"),
                cli_version=item.get("cli_version"),
                auth_mode=auth_mode,  # type: ignore[arg-type]
                status=status,  # type: ignore[arg-type]
                context_limit=item.get("context_limit"),
                output_limit=item.get("output_limit"),
                capabilities=item.get("capabilities") if isinstance(item.get("capabilities"), dict) else {},
                price_input_per_1m=item.get("price_input_per_1m"),
                price_output_per_1m=item.get("price_output_per_1m"),
                price_cache_read_per_1m=item.get("price_cache_read_per_1m"),
                price_cache_write_per_1m=item.get("price_cache_write_per_1m"),
                currency=item.get("currency", "USD"),
                effective_date=item.get("effective_date"),
                source_url=source_url,
                verified_at=verified_at,
                expires_at=item.get("expires_at"),
            )
        )
    return records


@dataclass
class BaseOfficialAdapter:
    provider: str
    source_url: str
    fetcher: FetcherFn = default_http_fetcher
    timeout_seconds: float = 10.0

    def fetch_models(self, now_iso: str | None = None) -> list[ModelRecord]:
        parsed = urlparse(self.source_url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_SOURCE_HOSTS:
            raise SourceFetchError(f"Unapproved catalog host: {parsed.hostname}")
        raw = self.fetcher(self.source_url, self.timeout_seconds, MAX_SOURCE_BYTES)
        verified_at = now_iso or _utc_now_iso()
        return _parse_json_models(
            raw,
            provider=self.provider,
            source_url=self.source_url,
            verified_at=verified_at,
        )


class AnthropicCatalogAdapter(BaseOfficialAdapter):
    def __init__(
        self,
        source_url: str = "https://platform.claude.com/docs/en/about-claude/models/overview.json",
        fetcher: FetcherFn = default_http_fetcher,
    ) -> None:
        super().__init__(provider="anthropic", source_url=source_url, fetcher=fetcher)


class OpenAICatalogAdapter(BaseOfficialAdapter):
    def __init__(
        self,
        source_url: str = "https://developers.openai.com/api/docs/models.json",
        fetcher: FetcherFn = default_http_fetcher,
    ) -> None:
        super().__init__(provider="openai", source_url=source_url, fetcher=fetcher)


class GoogleGeminiCatalogAdapter(BaseOfficialAdapter):
    def __init__(
        self,
        source_url: str = "https://ai.google.dev/gemini-api/docs/models.json",
        fetcher: FetcherFn = default_http_fetcher,
    ) -> None:
        super().__init__(provider="google", source_url=source_url, fetcher=fetcher)


def _infer_provider_from_cli_model(cli_name: str, model_id: str) -> str:
    low = model_id.lower()
    if low.startswith("claude-"):
        return "anthropic"
    if low.startswith("gemini-"):
        return "google"
    if low.startswith(("gpt-", "o1-", "o3-", "o4-")):
        return "openai"
    if cli_name == "claude":
        return "anthropic"
    if cli_name == "codex":
        return "openai"
    if cli_name == "gemini":
        return "google"
    return cli_name


class HostDiscoveryCatalogMapper:
    """Maps verified HostCapabilityReport (e.g. `agy models`) into ModelRecord entries."""

    def build_records(self, report: HostCapabilityReport) -> list[ModelRecord]:
        records: list[ModelRecord] = []
        for cli in report.clis:
            if not cli.installed or not cli.noninteractive_supported:
                continue
            for model_id in cli.verified_model_ids:
                provider = _infer_provider_from_cli_model(cli.cli_name, model_id)
                records.append(
                    ModelRecord(
                        provider=provider,
                        model_id=model_id,
                        cli_family=cli.cli_name,
                        cli_version=cli.version,
                        auth_mode=cli.auth_mode,
                        status="verified",
                        capabilities={
                            "structured_json": cli.structured_json_supported,
                            "supported_telemetry_fields": list(cli.supported_telemetry_fields),
                        },
                        # Subscription CLI usage is not priced as API usage; leave API prices null.
                        price_input_per_1m=None,
                        price_output_per_1m=None,
                        price_cache_read_per_1m=None,
                        price_cache_write_per_1m=None,
                        currency=None,
                        source_url=f"host-cli://{cli.cli_name}",
                        verified_at=cli.verified_at,
                    )
                )
        return records
