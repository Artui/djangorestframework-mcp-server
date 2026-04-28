from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.prompt_argument import PromptArgument


@dataclass(frozen=True)
class PromptBinding:
    """All wiring for a single MCP prompt.

    A prompt is a server-defined message-template the client invokes by
    name. The ``render`` callable receives the client-supplied arguments as
    kwargs and returns either:

    - a list of :class:`PromptMessage` instances (full control), or
    - a list of strings (each becomes a user text message), or
    - a single string (becomes one user text message), or
    - a coroutine yielding any of the above.

    The handler normalises whatever shape the callable returns into the spec's
    ``messages`` list at dispatch time.
    """

    name: str
    description: str | None
    render: Callable[..., Any]
    arguments: tuple[PromptArgument, ...] = ()
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    title: str | None = None


__all__ = ["PromptBinding"]
