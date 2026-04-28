from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PromptMessage:
    """One conversation turn returned by ``prompts/get``.

    The MCP spec accepts ``user`` or ``assistant`` for ``role`` and supports
    several content types (``text``, ``image``, ``resource``); v1 ships text
    only — the type is set on ``content`` so future content types slot in
    without changing this dataclass's shape.
    """

    role: str
    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def text(cls, role: str, text: str) -> PromptMessage:
        """Convenience constructor for the common case of a text turn."""
        return cls(role=role, content={"type": "text", "text": text})


__all__ = ["PromptMessage"]
