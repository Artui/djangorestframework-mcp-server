from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class Implementation:
    """Identifies an MCP client or server: name + version, with an optional title.

    Mirrors the spec's ``Implementation extends BaseMetadata, Icons``.

    Attributes:
        name: *"Intended for programmatic or logical use"* — the stable
            identifier. What distinguishes two servers to a client, and what
            server-scoped state keys off. Not interchangeable with
            :attr:`title`; the split is the spec's own.
        version: The implementation's version string.
        title: *"Intended for UI and end-user contexts"* — the human-readable
            label. Clients fall back to :attr:`name` when absent.
        description: UI copy a client shows next to the server's name in a
            connection list. **Not** the ``initialize`` ``instructions``
            string, which tells the *model* how to use this server and is
            consumed as context. ``MCPServer(description=...)`` sets
            ``instructions``; this comes from the ``SERVER_INFO`` setting.
        website_url: A link a client can offer alongside the name.
        icons: Display icons, emitted only when non-empty.
    """

    name: str
    version: str
    title: str | None = None
    description: str | None = None
    website_url: str | None = None
    icons: tuple[Icon, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name, "version": self.version}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.website_url is not None:
            out["websiteUrl"] = self.website_url
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["Implementation"]
