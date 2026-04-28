from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.jsonrpc_constants import JSONRPC_VERSION, JsonRpcId


@dataclass(frozen=True)
class JsonRpcRequest:
    """A JSON-RPC 2.0 request: has ``id`` and expects a response."""

    method: str
    id: JsonRpcId
    params: dict[str, Any] | list[Any] | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method, "id": self.id}
        if self.params is not None:
            out["params"] = self.params
        return out


__all__ = ["JsonRpcRequest"]
