from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.prompt_message import PromptMessage


@dataclass(frozen=True)
class GetPromptResult:
    """Result envelope returned by ``prompts/get``.

    ``description`` is optional — the spec uses it to surface the prompt's
    purpose to the client UI alongside the rendered messages.
    """

    messages: list[PromptMessage] = field(default_factory=list)
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"messages": [m.to_dict() for m in self.messages]}
        if self.description is not None:
            out["description"] = self.description
        return out


__all__ = ["GetPromptResult"]
