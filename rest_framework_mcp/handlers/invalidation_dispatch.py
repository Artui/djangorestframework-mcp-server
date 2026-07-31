from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async

from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.subscriptions.publish_invalidations import publish_invalidations
from rest_framework_mcp.subscriptions.render_invalidations import render_invalidations


def announce_invalidations(
    binding: Any,
    result: Any,
    arguments: Mapping[str, Any],
    context: MCPCallContext,
) -> None:
    """Publish a binding's ``invalidates=`` URIs for a call that changed something.

    Called after dispatch on both transports — the async one bridges to it, it
    does not reimplement it.
    """
    uris = _uris(binding, result, arguments)
    publish_invalidations(context.subscriptions, uris)


async def announce_invalidations_async(
    binding: Any,
    result: Any,
    arguments: Mapping[str, Any],
    context: MCPCallContext,
) -> None:
    """The async transport's route to the same function.

    ⚠ **``thread_sensitive=True`` is the load-bearing part.** Django connections
    are thread-local, and under ASGI the ORM work ran on a ``sync_to_async``
    worker while this coroutine resumes on the event loop thread. Announcing
    from the loop would read a *different* connection, see no open transaction,
    and publish immediately — announcing a write that has not committed and may
    roll back, which is the one failure this whole mechanism exists to avoid.
    The thread-sensitive executor is the same one the dispatch used, so
    ``on_commit`` attaches to the transaction that actually holds the write.
    """
    await sync_to_async(announce_invalidations, thread_sensitive=True)(
        binding, result, arguments, context
    )


def _uris(binding: Any, result: Any, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """The URIs to announce, or nothing at all.

    ⚠ **A failed tool announces nothing**, and the check is on ``isError``
    rather than on the result being present. A ``ServiceError`` produces a
    perfectly well-formed result — that is the package's whole error contract —
    so "did it come back" is not the question. Nothing changed, so nothing is
    published.

    A binding that declares no templates costs one attribute read; the
    ``getattr`` default keeps a hand-built binding without the field working.
    """
    templates: tuple[str, ...] = getattr(binding, "invalidates", ())
    if not templates or not isinstance(result, dict) or result.get("isError"):
        return ()
    return render_invalidations(templates, payload=result, arguments=arguments)


__all__ = ["announce_invalidations", "announce_invalidations_async"]
