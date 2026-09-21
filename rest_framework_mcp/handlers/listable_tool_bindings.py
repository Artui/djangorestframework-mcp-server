"""The tool bindings a caller's listing starts from, before availability is asked.

Shared by ``tools/list`` and ``MCPServer.unavailable_tools`` so the two begin from
the same set. Were they to diverge, an in-process consumer asking which tools are
unavailable could be told about one this caller may not see -- naming it, and its
reason, to a principal the listing hides it from.
"""

from __future__ import annotations

from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.tool_registry import ToolBindingLike


def listable_tool_bindings(context: MCPCallContext) -> list[ToolBindingLike]:
    """Every registered tool, less those ``FILTER_LISTINGS_BY_PERMISSIONS`` hides.

    Registry order is kept, so pagination over the result behaves as it does over
    the registry. With the flag off, which is the default, nothing is filtered.
    """
    bindings: list[ToolBindingLike] = list(context.tools.all())
    if context.config.filter_listings_by_permissions:
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]
    return bindings


__all__ = ["listable_tool_bindings"]
