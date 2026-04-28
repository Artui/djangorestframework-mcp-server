from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class ToolResult:
    """The result of a successful ``tools/call``.

    ``structured_content`` is always JSON-shaped (clients parse it directly);
    ``content`` is the human-readable / token-efficient projection that the
    encoder produces (JSON or TOON, etc.).

    ``is_error`` is ``True`` when the tool itself reported failure — it is
    distinct from a JSON-RPC protocol error: the JSON-RPC envelope is still
    a successful response, the failure detail lives inside the result.
    """

    content: list[ToolContentBlock] = field(default_factory=list)
    structured_content: Any = None
    is_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"content": [block.to_dict() for block in self.content]}
        if self.structured_content is not None:
            out["structuredContent"] = self.structured_content
        if self.is_error:
            out["isError"] = True
        return out


__all__ = ["ToolResult"]
