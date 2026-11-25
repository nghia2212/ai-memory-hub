"""Model catalog discovery, verification, and lifecycle management."""
from .registry import CatalogService, CatalogSnapshot, CatalogStaleError, RetirementAlert
from .sources import (
    ALLOWED_SOURCE_HOSTS,
    AnthropicCatalogAdapter,
    GoogleGeminiCatalogAdapter,
    HostDiscoveryCatalogMapper,
    OfficialCatalogAdapter,
    OpenAICatalogAdapter,
)

__all__ = [
    "ALLOWED_SOURCE_HOSTS",
    "AnthropicCatalogAdapter",
    "CatalogService",
    "CatalogSnapshot",
    "CatalogStaleError",
    "GoogleGeminiCatalogAdapter",
    "HostDiscoveryCatalogMapper",
    "OfficialCatalogAdapter",
    "OpenAICatalogAdapter",
    "RetirementAlert",
]
