from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClientCapabilities:
    """Capability bundle the client advertises in ``initialize``.

    All fields are open-ended dicts because the MCP spec leaves room for
    future capability sub-keys. We round-trip them verbatim.
    """

    roots: dict[str, Any] | None = None
    sampling: dict[str, Any] | None = None
    elicitation: dict[str, Any] | None = None
    experimental: dict[str, Any] | None = None
    extensions: dict[str, Any] | None = None
    """Protocol extensions the client implements, keyed by extension id — how a
    client says it supports MCP Apps (``io.modelcontextprotocol/ui``).

    **Advertisement is one-directional, client → server**: the spec defines no
    matching server capability, so nothing is sent back. Parsed here for
    introspection only. Nothing gates on it — the spec merely *SHOULD*-s a
    check, and remembering it per session would mean widening the pluggable
    ``SessionStore`` Protocol, which is a breaking change not worth making for
    metadata that unsupporting clients are required to ignore anyway."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.roots is not None:
            out["roots"] = self.roots
        if self.sampling is not None:
            out["sampling"] = self.sampling
        if self.elicitation is not None:
            out["elicitation"] = self.elicitation
        if self.experimental is not None:
            out["experimental"] = self.experimental
        if self.extensions is not None:
            out["extensions"] = self.extensions
        return out


__all__ = ["ClientCapabilities"]
