from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class ResourceTemplate:
    """A parameterised resource address (RFC 6570 URI Template).

    Returned by ``resources/templates/list``. Clients fill in template
    variables and call ``resources/read`` with the resulting URI.
    """

    uri_template: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    title: str | None = None
    annotations: dict[str, Any] | None = None
    # Base-protocol ``_meta`` bundle. Free-form dict at this wire boundary
    # because ``_meta`` is MCP's open extension namespace (see
    # :class:`~rest_framework_mcp.protocol.types.tool.Tool`).
    meta: dict[str, Any] | None = None

    icons: tuple[Icon, ...] = ()
    """Display icons for this entry. Emitted only when non-empty — the
    spec makes ``icons`` optional and an empty array carries no meaning."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"uriTemplate": self.uri_template, "name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["ResourceTemplate"]
