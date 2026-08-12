from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument


@dataclass(frozen=True)
class Prompt:
    """An MCP prompt descriptor as returned by ``prompts/list``.

    A server-defined template the client invokes by name to get back a sequence
    of LLM messages. Arguments are filled in at ``prompts/get`` time and
    threaded into the rendering callable as kwargs.
    """

    name: str
    description: str | None = None
    title: str | None = None
    arguments: list[PromptArgument] = field(default_factory=list)
    annotations: dict[str, Any] | None = None
    meta: dict[str, Any] | None = None

    icons: tuple[Icon, ...] = ()
    """Display icons for this entry, emitted only when non-empty."""

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"name": self.name}
        if self.title is not None:
            out["title"] = self.title
        if self.description is not None:
            out["description"] = self.description
        if self.arguments:
            out["arguments"] = [arg.to_dict() for arg in self.arguments]
        if self.annotations is not None:
            out["annotations"] = self.annotations
        if self.meta:
            out["_meta"] = self.meta
        if self.icons:
            out["icons"] = [icon.to_dict() for icon in self.icons]
        return out


__all__ = ["Prompt"]
