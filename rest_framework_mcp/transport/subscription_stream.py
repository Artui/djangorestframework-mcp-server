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
    keepalive: float | None = None,
) -> StreamingHttpResponse:
    """Answer ``subscriptions/listen`` with a stream that stays open.

    ⚠ **A different shape from the progress stream**, despite sharing the SSE
    framing. That one wraps a dispatch: it runs work, reports on it, and ends
    with the work's result. This one has no dispatch at all — it opens, and then
    waits indefinitely for events other requests produce. The consequences are
    worth stating plainly:

    - **It ends when the client leaves.** There is no natural completion, so the
      lifetime is the connection's.
    - **It occupies a worker for that whole time.** One parked ASGI task per
      subscriber. That is inherent to the wire format the spec chose (this
      method exists to replace the GET endpoint), not something tuneable here.

    Two ordering rules, both normative and both easy to get wrong:

    1. ``notifications/subscriptions/acknowledged`` **MUST** be the first
       message carrying this subscription's id, and no notification may precede
       it. Emitted before the queue is read at all, so there is no window in
       which an event could overtake it.
    2. Every frame carries the subscription id in ``_meta`` — the id of the
       ``listen`` request that opened the stream. That is what lets one client
       run several subscriptions and tell their notifications apart.

    The closing ``SubscriptionsListenResult`` is sent **only** on a graceful
    teardown. An abrupt client disconnect carries no response, which is the
    spec's own rule and also the only thing that could happen — there is nobody
    left to read it.
    """
    return _sse(
        _stream(
            broker=broker,
            topics=topics,
            granted=granted,
            request_id=request_id,
            keepalive=keepalive if keepalive is not None else keepalive_interval_seconds(),
        )
    )


def _sse(body: AsyncIterator[bytes]) -> StreamingHttpResponse:
    response = StreamingHttpResponse(body, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Without this nginx buffers the whole response and flushes on close, which
    # for a stream that never ends means the client receives nothing, ever.
    response["X-Accel-Buffering"] = "no"
    return response


async def _stream(
    *,
    broker: SubscriptionBroker,
    topics: frozenset[str],
    granted: SubscriptionFilter,
    request_id: Any,
    keepalive: float,
) -> AsyncIterator[bytes]:
    queue: asyncio.Queue[Any] = broker.subscribe(topics)
    try:
        yield format_event(_acknowledgement(granted, request_id))
        while True:
            try:
                payload: Any = await asyncio.wait_for(queue.get(), timeout=keepalive)
            except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct
                # A comment frame. Proxies and load balancers drop idle
                # connections, and a subscription can legitimately be silent for
                # hours — this is what keeps it alive without inventing a
                # notification the client did not ask for.
                yield b": keepalive\n\n"
                continue
            yield format_event(_with_subscription_id(payload, request_id))
    finally:
        # Runs on client disconnect, on server shutdown, and on cancellation.
        # Without it the broker keeps a queue nobody reads and — for the Redis
        # broker — a listener task and its channel subscriptions, for the life
        # of the process.
        broker.unsubscribe(queue)


def _acknowledgement(granted: SubscriptionFilter, request_id: Any) -> dict[str, Any]:
    """The mandatory first frame, naming what will actually be delivered.

    Reports the *granted* filter rather than the requested one. A client that
    asked for ``promptsListChanged`` on a server with no prompts sees it absent
    here and knows immediately, instead of waiting for a notification that was
    never going to come.
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

    Done here rather than by the publisher, because the publisher does not know
    who is listening — the same ``resources/updated`` goes to every subscription
    watching that URI, and each needs its own id. A payload that already carries
    one is left alone, so a future notification that needs to name a different
    subscription can.
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

    Separate from the stream because nothing in the stream can produce it: an
    abrupt disconnect leaves nobody to read a response, so this is only ever
    emitted by a deliberate shutdown path.
    """
    return JsonRpcResponse(
        id=request_id,
        result={"_meta": {SUBSCRIPTION_ID_META_KEY: request_id}},
    ).to_dict()


__all__ = ["build_subscription_stream", "subscription_closed_response"]
