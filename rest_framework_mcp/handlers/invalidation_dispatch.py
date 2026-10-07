from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from asgiref.sync import sync_to_async

from rest_framework_mcp.constants import ResultType
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

    **``thread_sensitive=True`` is the load-bearing part.** Django connections
    are thread-local, and under ASGI the ORM work ran on a ``sync_to_async``
    worker while this coroutine resumes on the loop thread. Announcing from the
    loop would read a *different* connection, see no open transaction, and
    publish immediately — announcing a write that has not committed and may roll
    back. The thread-sensitive executor is the one the dispatch used, so
    ``on_commit`` attaches to the transaction that holds the write.
    """
    await sync_to_async(announce_invalidations, thread_sensitive=True)(
        binding, result, arguments, context
    )


def _uris(binding: Any, result: Any, arguments: Mapping[str, Any]) -> tuple[str, ...]:
    """The URIs to announce, or nothing at all.

    **Only a result that completed announces.** A failed tool announces
    nothing, and the check is on ``isError`` rather than on the result being
    present: a ``ServiceError`` produces a well-formed result, so "did it come
    back" is not the question. Nor does a result asking the client for input,
    which ran nothing and carries no ``isError`` to fail on; it once announced
    a change no call had made
    (``test_only_a_completed_result_announces[input-required-*]``). Completed
    is read as the envelope reads it: a ``resultType`` that is absent, which a
    tool result's is until the envelope stamps it, or ``complete``.

    The chain is one branch arc, so each condition is held by a test:
    ``templates`` by ``test_a_binding_that_declares_nothing_publishes_nothing``,
    the ``dict`` by ``test_a_non_dict_result_announces_nothing``, ``isError`` by
    ``test_a_failed_tool_announces_nothing`` and ``resultType`` by
    ``test_only_a_completed_result_announces``.

    The ``getattr`` default keeps a hand-built binding without the field
    working.
    """
    templates: tuple[str, ...] = getattr(binding, "invalidates", ())
    if (
        not templates
        or not isinstance(result, dict)
        or result.get("isError")
        or result.get("resultType", ResultType.COMPLETE.value) != ResultType.COMPLETE.value
    ):
        return ()
    return render_invalidations(templates, payload=result, arguments=arguments)


__all__ = ["announce_invalidations", "announce_invalidations_async"]
