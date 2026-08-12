from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class Tool:
    """An MCP tool descriptor as returned by ``tools/list``.

    ``input_schema`` and ``output_schema`` are JSON Schema documents.
    ``annotations`` carries the MCP ToolAnnotations hint bundle
    (``readOnlyHint``, ``destructiveHint``), a closed spec-defined set.

    ``meta`` is the base-protocol ``_meta`` bundle, emitted under ``"_meta"``.
    It stays a free-form dict at this wire boundary rather than a closed
    dataclass because ``_meta`` is MCP's open extension namespace — any
    extension may add its own key. The same holds for every ``meta`` field on
    the other wire types here.
    """

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None
    title: str | None = None

    icons: tuple[Icon, ...] = ()
    """Display icons for this entry, emitted only when non-empty."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "inputSchema": self.input_schema}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.output_schema is not None:
            out["outputSchema"] = self.output_schema
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["Tool"]
