from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon


@dataclass(frozen=True)
class Implementation:
    """Identifies an MCP client or server: name + version, with an optional title.

    Mirrors the spec's ``Implementation extends BaseMetadata, Icons``. The two
    labels are **not** interchangeable, and the split is the spec's own:

    - :attr:`name` is *"intended for programmatic or logical use"* — the stable
      identifier. This is what distinguishes two servers to a client, and what
      server-scoped state keys off.
    - :attr:`title` is *"intended for UI and end-user contexts"* — the
      human-readable label. Optional; clients fall back to ``name`` when absent.

    ⚠ :attr:`description` is **not** the ``instructions`` string the server
    sends in its ``initialize`` result, though both are prose about the server.
    ``instructions`` tells the *model* how to use this server and is consumed
    as context; ``description`` is UI copy a client shows next to the server's
    name in a connection list. ``MCPServer(description=...)`` sets the former —
    the latter comes from the ``SERVER_INFO`` setting, which is the one place
    the two cannot be confused for each other.
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
