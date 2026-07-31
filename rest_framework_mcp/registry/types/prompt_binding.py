from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.protocol.types.prompt_argument import PromptArgument


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
    # See ``ToolBinding.meta`` — free-form ``_meta`` bundle for this
    # prompt's ``prompts/list`` entry.
    meta: dict[str, Any] = field(default_factory=dict)
    title: str | None = None
    completions: dict[str, Callable[..., Any]] = field(default_factory=dict)
    """Argument name → completer callable, powering ``completion/complete``.

    A completer is dispatched through ``resolve_callable_kwargs`` against a
    pool of ``value`` (the text typed so far), ``arguments`` (siblings the
    client has already resolved, also spread by name), ``request`` and
    ``user``. It returns an iterable of suggestions — a list, a generator or
    a queryset — and the handler slices it to the spec's cap rather than
    draining it."""

    icons: tuple[Icon, ...] = ()
    """Display icons for this entry, emitted in its listing. Purely
    presentational — a client renders them; nothing in dispatch reads them."""

    always_listed: bool = False
    """Opt this prompt back into ``prompts/list`` when
    ``FILTER_LISTINGS_BY_PERMISSIONS`` would otherwise hide it — same semantics
    as :attr:`ToolBinding.always_listed`."""


__all__ = ["PromptBinding"]
