from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptArgument:
    """One declared input to an MCP prompt.

    Mirrors the spec's ``PromptArgument`` shape: a name plus optional
    description, with a flag indicating whether the client must supply it.
    Used in ``prompts/list`` to advertise the prompt's interface.
    """

    name: str
    description: str | None = None
    required: bool = False

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.description is not None:
            out["description"] = self.description
        if self.required:
            out["required"] = True
        return out


__all__ = ["PromptArgument"]
