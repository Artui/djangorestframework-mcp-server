from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.context import MCPCallContext


def handle_ping(params: dict[str, Any] | None, context: MCPCallContext) -> dict[str, Any]:
    """Respond to the MCP ``ping`` keepalive.

    The MCP spec says the response is an empty object — no fields are
    inspected by clients, only the round-trip success.
    """
    del params, context  # unused
    return {}


__all__ = ["handle_ping"]
