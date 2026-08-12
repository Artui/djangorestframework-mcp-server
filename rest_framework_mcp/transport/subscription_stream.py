from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from django.http import StreamingHttpResponse

from rest_framework_mcp.constants import (
    JSONRPC_VERSION,
    SUBSCRIPTION_ID_META_KEY,
    SUBSCRIPTIONS_ACKNOWLEDGED_METHOD,
)
from rest_framework_mcp.protocol.types.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from rest_framework_mcp.transport.sse_response import format_event, keepalive_interval_seconds


def build_subscription_stream(
    *,
    broker: SubscriptionBroker,
    topics: frozenset[str],
    granted: SubscriptionFilter,
    request_id: Any,
    max_seconds: float | None,
    keepalive: float | None = None,
) -> StreamingHttpResponse:
    """Answer ``subscriptions/listen`` with a stream that stays open.

    **A different shape from the progress stream**, despite sharing the SSE
    framing: that one wraps a dispatch and ends with its result, while this has
    none. It opens and waits indefinitely for events other requests produce, so
    it ends when the client leaves and parks one ASGI task per subscriber for
    that whole time — inherent to the wire format the spec chose.

    Two ordering rules, both normative:

    1. ``notifications/subscriptions/acknowledged`` **MUST** be the first
       message carrying this subscription's id, and no notification may precede
       it. Emitted before the queue is read at all, so nothing can overtake it.
    2. Every frame carries the subscription id in ``_meta`` — the id of the
       ``listen`` request that opened the stream — which is what lets one
       client run several subscriptions and tell their notifications apart.

    The closing ``SubscriptionsListenResult`` is sent on a graceful teardown,
    which is always the server's decision: nothing was granted (a subscription
    with no topics can only deliver silence, so it is acknowledged honestly and
    closed), or ``max_seconds`` elapsed. An abrupt client disconnect carries no
    response — the spec's own rule, and the only possibility, since there is
    nobody left to read it.
    """
    return _sse(
        _stream(
            broker=broker,
            topics=topics,
            granted=granted,
            request_id=request_id,
            max_seconds=max_seconds,
            keepalive=keepalive if keepalive is not None else keepalive_interval_seconds(),
        )
    )


def _sse(body: AsyncIterator[bytes]) -> StreamingHttpResponse:
    response = StreamingHttpResponse(body, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Without this nginx buffers the response and flushes on close, which for a
    # stream that never ends means the client receives nothing, ever.
    response["X-Accel-Buffering"] = "no"
    return response


async def _stream(
    *,
    broker: SubscriptionBroker,
    topics: frozenset[str],
    granted: SubscriptionFilter,
    request_id: Any,
    max_seconds: float | None,
    keepalive: float,
) -> AsyncIterator[bytes]:
    if not topics:
        # Nothing was granted, so nothing can ever arrive. Acknowledging and
        # closing says exactly that in one round trip, instead of parking a
        # worker on an infinite keepalive stream that delivers silence.
        yield format_event(_acknowledgement(granted, request_id))
        yield format_event(subscription_closed_response(request_id))
        return

    queue: asyncio.Queue[Any] = await broker.subscribe(topics)
    deadline: float | None = (
        None if max_seconds is None else asyncio.get_running_loop().time() + max_seconds
    )
    try:
        yield format_event(_acknowledgement(granted, request_id))
        while True:
            if deadline is not None and asyncio.get_running_loop().time() >= deadline:
                # The permission check happened once, when the subscription
                # opened. Ending it forces the client to re-subscribe, which
                # re-runs that check — so a revoked principal stops receiving
                # change signals within one window rather than indefinitely.
                yield format_event(subscription_closed_response(request_id))
                return
            try:
                payload: Any = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct
                # A comment frame: proxies drop idle connections and a
                # subscription can legitimately be silent for hours, so this
                # keeps it alive without inventing a notification.
                yield b": keepalive\n\n"
                continue
            yield format_event(_with_subscription_id(payload, request_id))
    finally:
        # Runs on client disconnect, server shutdown and cancellation. Without
        # it the broker keeps a queue nobody reads — and, for the Redis broker,
        # a listener task and its channel subscriptions — for the life of the
        # process.
        broker.unsubscribe(queue)


def _acknowledgement(granted: SubscriptionFilter, request_id: Any) -> dict[str, Any]:
    """The mandatory first frame, naming what will actually be delivered.

    Reports the *granted* filter, not the requested one, so a client that asked
    for something this server will never send sees it absent here instead of
    waiting for a notification that was never coming.
    """
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": SUBSCRIPTIONS_ACKNOWLEDGED_METHOD,
        "params": {
            "notifications": granted.to_dict(),
            "_meta": {SUBSCRIPTION_ID_META_KEY: request_id},
        },
    }


def _with_subscription_id(payload: Any, request_id: Any) -> Any:
    """Stamp the subscription id into a notification's ``params._meta``.

    Done here rather than by the publisher, which does not know who is
    listening: the same ``resources/updated`` goes to every subscription
    watching that URI, and each needs its own id. A payload that already
    carries one is left alone.
    """
    if not isinstance(payload, dict):
        return payload
    params: Any = payload.get("params")
    params = dict(params) if isinstance(params, dict) else {}
    meta: Any = params.get("_meta")
    meta = dict(meta) if isinstance(meta, dict) else {}
    meta.setdefault(SUBSCRIPTION_ID_META_KEY, request_id)
    params["_meta"] = meta
    return {**payload, "params": params}


def subscription_closed_response(request_id: Any) -> dict[str, Any]:
    """The result that ends a subscription the *server* tore down.

    Emitted when nothing was granted and when the lifetime cap elapses; never
    on a client disconnect, there being nobody to read it — the spec's own
    reasoning for making it optional.
    """
    return JsonRpcResponse(
        id=request_id,
        result={"_meta": {SUBSCRIPTION_ID_META_KEY: request_id}},
    ).to_dict()


__all__ = ["build_subscription_stream", "subscription_closed_response"]
