from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ServerCapabilities:
    """Capability bundle the server advertises in the ``initialize`` response.

    For v1 we ship ``tools`` and ``resources``; ``prompts`` is reserved.
    """

    tools: dict[str, Any] | None = field(default_factory=dict)
    resources: dict[str, Any] | None = field(default_factory=dict)
    prompts: dict[str, Any] | None = None
    logging: dict[str, Any] | None = None
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
        if self.logging is not None:
            out["logging"] = self.logging
        if self.completions is not None:
            out["completions"] = self.completions
        if self.experimental is not None:
            out["experimental"] = self.experimental
        return out


__all__ = ["ServerCapabilities"]
