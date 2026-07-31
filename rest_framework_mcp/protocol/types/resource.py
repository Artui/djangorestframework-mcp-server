from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class Resource:
    """A concrete MCP resource as returned by ``resources/list``.

    ``uri`` is the canonical address (e.g. ``"invoices://1"``); ``mime_type``
    advertises how the contents will be encoded by ``resources/read``.
    """

    uri: str
    name: str
    description: str | None = None
    mime_type: str | None = None
    size: int | None = None
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
        out: dict[str, Any] = {"uri": self.uri, "name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.mime_type is not None:
            out["mimeType"] = self.mime_type
        if self.size is not None:
            out["size"] = self.size
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["Resource"]
