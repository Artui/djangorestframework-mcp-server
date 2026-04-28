from __future__ import annotations

from typing import Any

from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_message import JsonRpcMessage
from rest_framework_mcp.protocol.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.protocol.jsonrpc_constants import JSONRPC_VERSION


def parse_message(payload: dict[str, Any]) -> JsonRpcMessage:
    """Classify a parsed JSON object as request / notification / response.

    Raises ``ValueError`` if the payload has no recognizable shape — caller
    should translate that to a JSON-RPC ``-32600`` (Invalid Request) error.
    """
    if not isinstance(payload, dict):
        raise ValueError("JSON-RPC message must be a JSON object")
    if payload.get("jsonrpc") != JSONRPC_VERSION:
        raise ValueError("JSON-RPC version must be '2.0'")
    if "method" in payload:
        method: Any = payload["method"]
        if not isinstance(method, str):
            raise ValueError("'method' must be a string")
        params: Any = payload.get("params")
        if "id" in payload:
            return JsonRpcRequest(method=method, id=payload["id"], params=params)
        return JsonRpcNotification(method=method, params=params)
    if "result" in payload or "error" in payload:
        error_payload: Any = payload.get("error")
        error: JsonRpcError | None = None
        if error_payload is not None:
            if not isinstance(error_payload, dict):
                raise ValueError("'error' must be an object")
            error = JsonRpcError(
                code=error_payload.get("code", 0),
                message=error_payload.get("message", ""),
                data=error_payload.get("data"),
            )
        return JsonRpcResponse(
            id=payload.get("id"),
            result=payload.get("result"),
            error=error,
        )
    raise ValueError("JSON-RPC message must contain 'method' or 'result'/'error'")


__all__ = ["parse_message"]
