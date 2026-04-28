from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.prompt_argument import PromptArgument


@dataclass(frozen=True)
class Prompt:
    """An MCP prompt descriptor as returned by ``prompts/list``.

    A prompt is a server-defined template the client invokes by name to get
    back a sequence of LLM messages. Arguments are filled in at
    ``prompts/get`` time and threaded into the rendering callable as kwargs.
    """

    name: str
    description: str | None = None
    title: str | None = None
    arguments: list[PromptArgument] = field(default_factory=list)
    annotations: dict[str, Any] | None = None

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
        return out


__all__ = ["Prompt"]
