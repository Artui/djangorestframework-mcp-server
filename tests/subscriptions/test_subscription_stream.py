"""The ``subscriptions/listen`` stream itself.

Driven through the generator rather than the Django response wrapper, for the
reason the progress-stream tests do the same: ``StreamingHttpResponse`` does not
forward ``aclose()`` to the underlying generator, so the disconnect path is
unreachable through it.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from rest_framework_mcp import NotificationKind, SubscriptionFilter
from rest_framework_mcp.constants import SUBSCRIPTION_ID_META_KEY
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.transport.subscription_stream import _stream as stream
from rest_framework_mcp.transport.subscription_stream import (
    build_subscription_stream,
    subscription_closed_response,
)

pytestmark = pytest.mark.asyncio


def _frame(raw: bytes) -> Any:
    assert raw.startswith(b"data: ")
    return json.loads(raw[len(b"data: ") :].strip())


def _open(broker: Any, topics: set[str], granted: SubscriptionFilter, **kw: Any) -> Any:
    return stream(
        broker=broker,
        topics=frozenset(topics),
        granted=granted,
        request_id=kw.pop("request_id", 7),
        max_seconds=kw.pop("max_seconds", None),
        keepalive=kw.pop("keepalive", 30.0),
    )


async def test_the_acknowledgement_is_the_first_frame() -> None:
    """⚠ Normative: no notification may precede it, so it is emitted before the
    queue is read at all — there is no window for an event to overtake it."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)))
    first = _frame(await anext(gen))
    assert first["method"] == "notifications/subscriptions/acknowledged"
    assert first["params"]["notifications"] == {"resourceSubscriptions": ["a"]}
    await gen.aclose()


async def test_the_acknowledgement_carries_the_subscription_id() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter(resource_uris=("t",)), request_id="sub-1")
    assert _frame(await anext(gen))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == "sub-1"
    await gen.aclose()


async def test_the_acknowledgement_reports_the_granted_set_not_the_requested_one() -> None:
    """A client that asked for something this server cannot produce learns on
    the first frame, instead of waiting for an event that will never come."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter())
    assert _frame(await anext(gen))["params"]["notifications"] == {}
    await gen.aclose()


async def test_a_published_notification_reaches_the_stream() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)))
    await anext(gen)  # acknowledgement
    await broker.publish(
        "resource:a",
        {"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": "a"}},
    )
    frame = _frame(await anext(gen))
    assert frame["method"] == "notifications/resources/updated"
    await gen.aclose()


async def test_every_notification_carries_the_subscription_id() -> None:
    """Stamped by the stream, not the publisher — the same event goes to every
    subscription watching that URI and each needs its own id."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)), request_id=42)
    await anext(gen)
    await broker.publish("resource:a", {"jsonrpc": "2.0", "method": "m", "params": {"uri": "a"}})
    assert _frame(await anext(gen))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 42
    await gen.aclose()


async def test_two_subscriptions_stamp_their_own_ids_on_the_same_event() -> None:
    broker = InMemorySubscriptionBroker()
    one = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)), request_id=1)
    two = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)), request_id=2)
    await anext(one)
    await anext(two)
    await broker.publish("resource:a", {"jsonrpc": "2.0", "method": "m", "params": {}})
    assert _frame(await anext(one))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 1
    assert _frame(await anext(two))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 2
    await one.aclose()
    await two.aclose()


async def test_a_payload_that_already_names_a_subscription_is_left_alone() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter(), request_id=1)
    await anext(gen)
    await broker.publish(
        "t", {"jsonrpc": "2.0", "method": "m", "params": {"_meta": {SUBSCRIPTION_ID_META_KEY: 9}}}
    )
    assert _frame(await anext(gen))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 9
    await gen.aclose()


async def test_a_non_dict_payload_passes_through_untouched() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter())
    await anext(gen)
    await broker.publish("t", "raw")
    assert _frame(await anext(gen)) == "raw"
    await gen.aclose()


async def test_a_notification_with_no_params_still_gets_its_meta() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter(), request_id=3)
    await anext(gen)
    await broker.publish("t", {"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    assert _frame(await anext(gen))["params"]["_meta"][SUBSCRIPTION_ID_META_KEY] == 3
    await gen.aclose()


async def test_a_quiet_subscription_is_kept_alive() -> None:
    """A subscription can legitimately be silent for hours, and proxies drop
    idle connections. The comment frame holds it open without inventing a
    notification the client did not ask for."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter(), keepalive=0.01)
    await anext(gen)
    assert await anext(gen) == b": keepalive\n\n"
    await gen.aclose()


async def test_a_stream_keeps_serving_notifications_after_a_quiet_spell() -> None:
    """The loop has to come back round. A subscription that went idle and then
    stopped delivering would be the worst failure this feature has — silent, and
    indistinguishable from nothing having changed."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter(), keepalive=0.01)
    await anext(gen)
    assert await anext(gen) == b": keepalive\n\n"
    await broker.publish("t", {"jsonrpc": "2.0", "method": "after-idle", "params": {}})
    assert _frame(await anext(gen))["method"] == "after-idle"
    await gen.aclose()


async def test_the_broker_is_released_when_the_client_leaves() -> None:
    """⚠ Otherwise the broker holds a queue nobody reads — and, for the Redis
    broker, a listener task and its channel subscriptions — for the life of the
    process."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter())
    await anext(gen)
    assert broker._by_topic != {}
    await gen.aclose()
    assert broker._by_topic == {}


async def test_the_stream_is_released_even_if_it_never_yielded() -> None:
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"t"}, SubscriptionFilter())
    await gen.aclose()
    assert broker._by_topic == {}


async def test_the_response_carries_the_headers_a_stream_needs() -> None:
    """Without ``X-Accel-Buffering`` nginx buffers the whole response and
    flushes on close — which, for a stream that never ends, means the client
    receives nothing at all."""
    response = build_subscription_stream(
        broker=InMemorySubscriptionBroker(),
        topics=frozenset({"t"}),
        granted=SubscriptionFilter(resource_uris=("t",)),
        request_id=1,
        max_seconds=None,
    )
    assert response["Content-Type"] == "text/event-stream"
    assert response["Cache-Control"] == "no-cache"
    assert response["X-Accel-Buffering"] == "no"
    await response.streaming_content.aclose()


async def test_the_closing_result_names_the_subscription() -> None:
    """Sent only on a deliberate teardown — an abrupt disconnect leaves nobody
    to read a response, which is the spec's own rule."""
    body = subscription_closed_response("sub-9")
    assert body["id"] == "sub-9"
    assert body["result"]["_meta"][SUBSCRIPTION_ID_META_KEY] == "sub-9"


async def test_a_subscription_only_hears_the_topics_it_named() -> None:
    """The MUST NOT at the heart of the feature: opt-in is enforced by what the
    stream attaches to, not by filtering on the way out."""
    broker = InMemorySubscriptionBroker()
    gen = _open(broker, {"resource:a"}, SubscriptionFilter(resource_uris=("a",)), keepalive=0.01)
    await anext(gen)
    await broker.publish("kind:tools/list_changed", {"jsonrpc": "2.0", "method": "nope"})
    assert await anext(gen) == b": keepalive\n\n"
    await gen.aclose()


async def test_kinds_render_deterministically_in_the_acknowledgement() -> None:
    broker = InMemorySubscriptionBroker()
    granted = SubscriptionFilter(
        kinds=frozenset(
            {NotificationKind.TOOLS_LIST_CHANGED, NotificationKind.RESOURCES_LIST_CHANGED}
        )
    )
    first = _frame(await anext(_open(broker, {"t"}, granted)))
    assert set(first["params"]["notifications"]) == {"toolsListChanged", "resourcesListChanged"}
    for _ in range(5):
        again = _frame(await anext(_open(broker, {"t"}, granted)))
        assert list(again["params"]["notifications"]) == list(first["params"]["notifications"])


async def test_concurrent_publishes_do_not_disturb_an_unwinding_subscriber() -> None:
    """A subscriber's ``finally`` can run while another request is mid-publish;
    iterating the live set would raise into whichever request that was."""
    broker = InMemorySubscriptionBroker()
    queues = [await broker.subscribe(frozenset({"t"})) for _ in range(3)]
    broker.unsubscribe(queues[1])
    assert await broker.publish("t", {}) == 2
    assert isinstance(queues[0], asyncio.Queue)
