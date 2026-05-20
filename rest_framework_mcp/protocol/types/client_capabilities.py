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
        return out


__all__ = ["ClientCapabilities"]
