from __future__ import annotations

from dataclasses import dataclass

from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry


@dataclass(frozen=True)
class MCPCallContext:
    """Bundle of state every JSON-RPC handler needs.

    Constructed by the transport layer per HTTP request and threaded through
    ``dispatch`` to the chosen handler. Frozen so handlers cannot mutate
    shared state — any per-request bookkeeping should happen on locals.
    """

    http_request: HttpRequest
    token: TokenInfo
    tools: ToolRegistry
    resources: ResourceRegistry
    prompts: PromptRegistry
    protocol_version: str
    session_id: str | None = None


__all__ = ["MCPCallContext"]
