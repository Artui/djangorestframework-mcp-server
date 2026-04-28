from __future__ import annotations

from enum import IntEnum


class JsonRpcErrorCode(IntEnum):
    """JSON-RPC 2.0 standard error codes plus MCP-specific reservations.

    The standard codes (-32700 through -32600 and -32603) are defined by
    JSON-RPC; MCP reserves the -32000 through -32099 range for server-defined
    errors. We map common MCP failure modes onto stable codes here so handlers
    don't drift.
    """

    PARSE_ERROR = -32700
    INVALID_REQUEST = -32600
    METHOD_NOT_FOUND = -32601
    INVALID_PARAMS = -32602
    INTERNAL_ERROR = -32603
    # Server-defined (MCP):
    SERVER_ERROR = -32000
    UNAUTHORIZED = -32001
    FORBIDDEN = -32002
    RESOURCE_NOT_FOUND = -32003
    TOOL_NOT_FOUND = -32004
    RATE_LIMITED = -32005


__all__ = ["JsonRpcErrorCode"]
