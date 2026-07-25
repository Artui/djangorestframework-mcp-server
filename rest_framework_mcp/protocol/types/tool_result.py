from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class ToolResult:
    """The result of a successful ``tools/call``.

    ``structured_content`` is always JSON-shaped (clients parse it directly);
    ``content`` is the human-readable / token-efficient projection that the
    encoder produces (JSON or TOON, etc.).

    ``is_error`` is ``True`` when the tool itself reported failure — it is
    distinct from a JSON-RPC protocol error: the JSON-RPC envelope is still
    a successful response, the failure detail lives inside the result.

    ``meta`` is the base-protocol ``_meta`` bundle on the *result envelope*
    — per-call, unlike the static ``_meta`` a ``tools/list`` entry carries.
    """

    content: list[ToolContentBlock] = field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False
    # Base-protocol ``_meta`` bundle. Free-form dict at this wire boundary
    # because ``_meta`` is MCP's open extension namespace (see
    # :class:`~rest_framework_mcp.protocol.types.tool.Tool`).
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"content": [block.to_dict() for block in self.content]}
        if self.structured_content is not None:
            out["structuredContent"] = self.structured_content
        if self.is_error:
            out["isError"] = True
        if self.meta:
            out["_meta"] = self.meta
        return out


__all__ = ["ToolResult"]
