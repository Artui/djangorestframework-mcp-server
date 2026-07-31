from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ServerCapabilities:
    """Capability bundle the server advertises in the ``initialize`` response.

    Every field defaults to ``None`` and is omitted from the payload when
    unset, because a capability is a promise: a client that sees ``resources``
    will call ``resources/list``, and one that sees ``completions`` will send
    ``completion/complete``. :func:`handle_initialize` populates only what this
    server can actually answer — ``tools`` and ``resources`` used to default to
    ``{}`` here, which advertised both on a server that had neither.

    ⛔ There is no ``logging`` field. One existed, unpopulated, until the
    ``2026-07-28`` revision **deprecated** the logging utility outright — after
    which an empty slot could only serve as an invitation to fill it in.
    ``experimental`` stays: it is the spec's own escape hatch and carries no
    such trap.
    """

    tools: dict[str, Any] | None = None
    resources: dict[str, Any] | None = None
    prompts: dict[str, Any] | None = None
    completions: dict[str, Any] | None = None
    experimental: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self.tools is not None:
            out["tools"] = self.tools
        if self.resources is not None:
            out["resources"] = self.resources
        if self.prompts is not None:
            out["prompts"] = self.prompts
        if self.completions is not None:
            out["completions"] = self.completions
        if self.experimental is not None:
            out["experimental"] = self.experimental
        return out


__all__ = ["ServerCapabilities"]
