from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_services import UNSET

from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class ToolResult:
    """The result of a successful ``tools/call``.

    Attributes:
        content: The human-readable / token-efficient projection the encoder
            produces (JSON, TOON, or a media block).
        structured_content: The same answer JSON-shaped, for clients to parse
            directly. ``UNSET`` — the default — means the result carries no
            structured channel at all and the key is left off the wire. A
            payload of ``None`` is a *value*: it is emitted as
            ``"structuredContent": null``, so a client branching on the key's
            presence to decide whether the tool offers structured output is not
            told "no" by a tool whose answer simply is null.
        is_error: ``True`` when the tool itself reported failure. Distinct from
            a JSON-RPC protocol error — the envelope is still a successful
            response and the failure detail lives inside the result.
        meta: The base-protocol ``_meta`` bundle on the *result envelope*, so
            per-call, unlike the static ``_meta`` a ``tools/list`` entry
            carries.
    """

    content: list[ToolContentBlock] = field(default_factory=list)
    structured_content: Any = UNSET
    is_error: bool = False
    meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"content": [block.to_dict() for block in self.content]}
        if self.structured_content is not UNSET:
            out["structuredContent"] = self.structured_content
        if self.is_error:
            out["isError"] = True
        if self.meta:
            out["_meta"] = self.meta
        return out


__all__ = ["ToolResult"]
