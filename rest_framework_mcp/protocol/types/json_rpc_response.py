from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import JSONRPC_VERSION, JsonRpcId
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


@dataclass(frozen=True)
class JsonRpcResponse:
    """A JSON-RPC 2.0 response: exactly one of ``result`` or ``error`` is set."""

    id: JsonRpcId
    result: Any = None
    error: JsonRpcError | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self.result
        return out


__all__ = ["JsonRpcResponse"]
