from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.constants import (
    CLIENT_CAPABILITIES_META_KEY,
    CLIENT_INFO_META_KEY,
    PROTOCOL_VERSION_META_KEY,
)
from rest_framework_mcp.protocol.types.implementation import Implementation


@dataclass(frozen=True)
class RequestMetadata:
    """The per-request ``_meta`` a modern client sends on every request.

    What replaced the ``initialize`` handshake: version, identity and
    capabilities restated on each request rather than agreed once and
    remembered, so nothing is negotiated and nothing has to be stored.

    ``from_params`` doubles as the **era test**, returning ``None`` when
    the request carries no modern protocol version — a legacy request being
    exactly one that carries none. Deliberately not a test on the
    ``MCP-Protocol-Version`` header (legacy clients have sent one since
    ``2025-06-18``) nor on the method (most exist in both eras).
    """

    protocol_version: str
    client_capabilities: dict[str, Any] = field(default_factory=dict)
    client_info: Implementation | None = None

    @classmethod
    def from_params(cls, params: Any) -> RequestMetadata | None:
        """Read modern metadata off a request's ``params``, or ``None`` if legacy.

        Tolerant on the way in: a malformed ``_meta`` reads as legacy rather
        than erroring, because a *missing* modern marker and a *broken* one are
        indistinguishable from here, and answering a legacy client with a
        modern header-validation error is the more confusing failure. A modern
        request missing a required field is rejected downstream, where the
        error can name it.
        """
        if not isinstance(params, dict):
            return None
        meta: Any = params.get("_meta")
        if not isinstance(meta, dict):
            return None
        version: Any = meta.get(PROTOCOL_VERSION_META_KEY)
        if not isinstance(version, str) or not version:
            return None

        capabilities: Any = meta.get(CLIENT_CAPABILITIES_META_KEY)
        info: Any = meta.get(CLIENT_INFO_META_KEY)
        return cls(
            protocol_version=version,
            client_capabilities=capabilities if isinstance(capabilities, dict) else {},
            client_info=_implementation(info),
        )


def _implementation(raw: Any) -> Implementation | None:
    """Project a ``clientInfo`` mapping, ignoring anything unusable.

    Self-reported and unverified, as the spec says of the server's own, so a
    half-filled one is projected as far as it goes. Nothing branches on it.
    """
    if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
        return None
    version: Any = raw.get("version")
    title: Any = raw.get("title")
    return Implementation(
        name=raw["name"],
        version=version if isinstance(version, str) else "",
        title=title if isinstance(title, str) else None,
    )


__all__ = ["RequestMetadata"]
