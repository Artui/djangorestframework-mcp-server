from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import SERVER_INFO_META_KEY
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.protocol.types.server_capabilities import ServerCapabilities


@dataclass(frozen=True)
class DiscoverResult:
    """The server's response to a ``server/discover`` request.

    ``server/discover`` is the ``2026-07-28`` revision's replacement for the
    ``initialize`` handshake: same three answers — which protocol versions,
    which capabilities, who is this — but as an ordinary request rather than a
    stateful negotiation, so it can be cached, repeated, or skipped entirely.

    Two shape differences from :class:`InitializeResult` are worth noticing,
    because they are not cosmetic:

    - ``supportedVersions`` is a **list**, not a negotiated single version.
      Nothing is agreed here; the client picks one and puts it on subsequent
      requests.
    - ``serverInfo`` moves into ``_meta`` under a reserved key. The spec is
      pointed about why: it is self-reported, unverified, and clients
      **SHOULD NOT** change behaviour or make security decisions from it — so
      it lives in the metadata namespace rather than looking like protocol
      state.
    """

    supported_versions: tuple[str, ...]
    capabilities: ServerCapabilities
    server_info: Implementation
    instructions: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "supportedVersions": list(self.supported_versions),
            "capabilities": self.capabilities.to_dict(),
            "_meta": {SERVER_INFO_META_KEY: self.server_info.to_dict()},
        }
        if self.instructions is not None:
            out["instructions"] = self.instructions
        return out


__all__ = ["DiscoverResult"]
