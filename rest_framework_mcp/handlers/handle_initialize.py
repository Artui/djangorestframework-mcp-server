from __future__ import annotations

from typing import Any

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.protocol.implementation import Implementation
from rest_framework_mcp.protocol.initialize_params import InitializeParams
from rest_framework_mcp.protocol.initialize_result import InitializeResult
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.server_capabilities import ServerCapabilities
from rest_framework_mcp.version import __version__ as package_version


def handle_initialize(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> InitializeResult | JsonRpcError:
    """Handle the MCP ``initialize`` request.

    Negotiates protocol version: if the client's requested version is one we
    support, echo it back; otherwise return our latest. Mismatched / unparsable
    params produce ``-32602 Invalid Params`` so the client retries cleanly.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            code=JsonRpcErrorCode.INVALID_PARAMS,
            message="initialize params must be an object",
        )

    parsed: InitializeParams = InitializeParams.from_payload(params)
    supported: list[str] = list(get_setting("PROTOCOL_VERSIONS"))
    chosen: str = parsed.protocol_version if parsed.protocol_version in supported else supported[0]

    server_info_settings: dict[str, Any] = get_setting("SERVER_INFO")
    server_info = Implementation(
        name=server_info_settings.get("name", "djangorestframework-mcp-server"),
        version=server_info_settings.get("version", package_version),
    )
    # Advertise ``prompts`` only when the server has at least one registered.
    # Empty capability advertisement would tell clients to call ``prompts/list``
    # that returns nothing — harmless but noisy.
    capabilities = ServerCapabilities(
        tools={},
        resources={},
        prompts={} if len(context.prompts) > 0 else None,
    )
    return InitializeResult(
        protocol_version=chosen,
        capabilities=capabilities,
        server_info=server_info,
    )


__all__ = ["handle_initialize"]
