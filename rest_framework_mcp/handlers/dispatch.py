from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_initialize import handle_initialize
from rest_framework_mcp.handlers.handle_ping import handle_ping
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_list import handle_prompts_list
from rest_framework_mcp.handlers.handle_resources_list import handle_resources_list
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_templates_list import (
    handle_resources_templates_list,
)
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.protocol.initialize_result import InitializeResult
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode

# Method dispatch table. The values return either:
#   - a result payload (anything JSON-encodable),
#   - an :class:`InitializeResult` (which the dispatcher serialises), or
#   - a :class:`JsonRpcError`.
_HandlerFn = Callable[[dict[str, Any] | None, MCPCallContext], Any]

_HANDLERS: dict[str, _HandlerFn] = {
    "initialize": handle_initialize,
    "ping": handle_ping,
    "tools/list": handle_tools_list,
    "tools/call": handle_tools_call,
    "resources/list": handle_resources_list,
    "resources/templates/list": handle_resources_templates_list,
    "resources/read": handle_resources_read,
    "prompts/list": handle_prompts_list,
    "prompts/get": handle_prompts_get,
}


def dispatch(
    method: str,
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> Any | JsonRpcError:
    """Route a JSON-RPC method to its handler.

    Returns the handler's raw return value, except that
    :class:`InitializeResult` is converted to its dict form so the transport
    can serialise uniformly.
    """
    handler: _HandlerFn | None = _HANDLERS.get(method)
    if handler is None:
        return JsonRpcError(JsonRpcErrorCode.METHOD_NOT_FOUND, f"Unknown method: {method!r}")
    result: Any = handler(params, context)
    if isinstance(result, InitializeResult):
        return result.to_dict()
    return result


__all__ = ["dispatch"]
