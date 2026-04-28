from __future__ import annotations

from rest_framework_mcp.protocol.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.json_rpc_response import JsonRpcResponse

JsonRpcMessage = JsonRpcRequest | JsonRpcNotification | JsonRpcResponse

__all__ = ["JsonRpcMessage"]
