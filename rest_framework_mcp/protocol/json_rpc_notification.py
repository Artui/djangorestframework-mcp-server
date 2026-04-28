from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.jsonrpc_constants import JSONRPC_VERSION


@dataclass(frozen=True)
class JsonRpcNotification:
    """A JSON-RPC 2.0 notification: no ``id``, no response expected."""

    method: str
    params: dict[str, Any] | list[Any] | None = None
    jsonrpc: str = JSONRPC_VERSION

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": self.jsonrpc, "method": self.method}
        if self.params is not None:
            out["params"] = self.params
        return out


__all__ = ["JsonRpcNotification"]
