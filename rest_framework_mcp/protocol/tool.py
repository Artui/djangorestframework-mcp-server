from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Tool:
    """An MCP tool descriptor as returned by ``tools/list``.

    ``input_schema`` and ``output_schema`` are JSON Schema documents.
    ``annotations`` carries the MCP ToolAnnotations hint bundle (e.g.
    ``readOnlyHint``, ``destructiveHint``).
    """

    name: str
    description: str | None = None
    input_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object"})
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    title: str | None = None

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
        return out


__all__ = ["Tool"]
