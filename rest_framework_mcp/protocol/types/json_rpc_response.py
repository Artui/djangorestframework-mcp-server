from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import JSONRPC_VERSION, JsonRpcId, ResultType
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


@dataclass(frozen=True)
class JsonRpcResponse:
    """A JSON-RPC 2.0 response: exactly one of ``result`` or ``error`` is set."""

    id: JsonRpcId
    result: Any = None
    error: JsonRpcError | None = None
    jsonrpc: str = JSONRPC_VERSION
    result_type: ResultType = ResultType.COMPLETE
    """Stamped into the result object as ``resultType``.

    Every result carries it from ``2026-07-28`` onward, so it is applied here —
    the one place a handler's return value becomes wire JSON — rather than in
    each of the nine handlers, where the tenth would eventually be forgotten.
    A handler that has already set the field wins, which is what leaves room
    for a non-``complete`` result to be built where it is produced.
    """

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.error is not None:
            out["error"] = self.error.to_dict()
        else:
            out["result"] = self._typed_result()
        return out

    def _typed_result(self) -> Any:
        """The result object with ``resultType`` stamped in.

        Non-dict results pass through untouched. Nothing here produces one —
        every MCP result is an object — but a JSON-RPC result may legally be
        any JSON value, and silently corrupting one would be a worse bug than
        omitting a field a legacy client ignores anyway.
        """
        if not isinstance(self.result, dict) or "resultType" in self.result:
            return self.result
        return {"resultType": self.result_type.value, **self.result}


__all__ = ["JsonRpcResponse"]
