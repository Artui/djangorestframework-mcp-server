from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument


@dataclass(frozen=True)
class PromptBinding:
    """All wiring for a single MCP prompt.

    A prompt is a server-defined message template the client invokes by name.
    The ``render`` callable receives the client-supplied arguments as kwargs
    and returns a list of :class:`PromptMessage` instances, a list of strings
    (each becoming a user text message), a single string, or a coroutine
    yielding any of those. The handler normalises whichever shape arrives into
    the spec's ``messages`` list at dispatch time.

    ``annotations`` and ``meta`` are emitted verbatim on this prompt's
    ``prompts/list`` entry, under ``annotations`` and ``_meta`` respectively.
    """

    name: str
    description: str | None
    render: Callable[..., Any]
    arguments: tuple[PromptArgument, ...] = ()
    permissions: tuple[Any, ...] = ()
    rate_limits: tuple[Any, ...] = ()
    annotations: dict[str, Any] = field(default_factory=dict)
    # Free-form for the reason given on ``ToolBinding.meta``.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name to completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or a
    queryset — which the handler slices to the spec's cap rather than
    draining."""

    icons: tuple[Icon, ...] = ()
    """Display icons, emitted in this prompt's listing entry. Purely
    presentational; nothing in dispatch reads them."""

    always_listed: bool = False
    """Keep this prompt in ``prompts/list`` even when
    ``FILTER_LISTINGS_BY_PERMISSIONS`` would hide it — same semantics as
    :attr:`ToolBinding.always_listed`."""


__all__ = ["PromptBinding"]
