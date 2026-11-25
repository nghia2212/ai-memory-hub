"""V3.1 P4–P8 — Unified Tool Pack Registry for Shared, Game, Technical Architect, and Software Developer packs."""
from __future__ import annotations

from typing import Any

from .architect_pack import ARCHITECT_TOOLS
from .developer_pack import DEVELOPER_TOOLS
from .game_pack import GAME_TOOLS
from .shared_pack import SHARED_TOOLS

ALL_TOOL_PACKS: dict[str, dict[str, Any]] = {
    "shared": SHARED_TOOLS,
    "game": GAME_TOOLS,
    "architect": ARCHITECT_TOOLS,
    "developer": DEVELOPER_TOOLS,
}


def register_all_local_packs(gateway_service: Any) -> int:
    """Register all deterministic local tool pack handlers into V31McpGatewayService."""
    count = 0
    for pack_name, tools in ALL_TOOL_PACKS.items():
        server_name = f"pack-{pack_name}"
        for tool_name, entry in tools.items():
            gateway_service.register_local_tool(server_name, entry["meta"], entry["handler"])
            count += 1
    return count
