from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock


@dataclass(frozen=True)
class PromptMessage:
    """One conversation turn returned by ``prompts/get``.

    The MCP spec accepts ``user`` or ``assistant`` for ``role``, and a message's
    ``content`` is one content block — ``text``, ``image``, ``audio`` or an
    embedded ``resource``. ``content`` stays a plain dict because that is
    exactly the wire shape; :meth:`block` is the typed way in, and
    :meth:`text` remains for the overwhelmingly common case.
    """

    role: str
    content: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"role": self.role, "content": self.content}

    @classmethod
    def text(cls, role: str, text: str) -> PromptMessage:
        """Convenience constructor for the common case of a text turn."""
        return cls(role=role, content={"type": "text", "text": text})

    @classmethod
    def block(cls, role: str, block: ToolContentBlock) -> PromptMessage:
        """Build a turn from any content block.

        The spec uses one content vocabulary across tool results and prompt
        messages, so this reuses :class:`ToolContentBlock` and its typed
        constructors rather than growing a parallel set. ``resource_link`` is
        the one member prompts do not accept — a prompt message embeds
        content, it does not point at it.
        """
        return cls(role=role, content=block.to_dict())


__all__ = ["PromptMessage"]
