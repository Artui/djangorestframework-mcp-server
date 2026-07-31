"""The cross-process broker, against ``fakeredis``.

The one that actually works past a single worker — and therefore the one whose
behaviour matters most, since the in-memory sibling silently delivers nothing in
exactly the deployment people reach for subscriptions in.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fakeredis import FakeAsyncRedis, FakeServer

from rest_framework_mcp.subscriptions.redis_subscription_broker import RedisSubscriptionBroker

pytestmark = pytest.mark.asyncio


def _client() -> FakeAsyncRedis:
    return FakeAsyncRedis()


async def _await_subscriber(client: Any, channel: str, *, timeout: float = 1.0) -> None:
    """Wait until the listener task has actually subscribed.

    ``subscribe`` returns immediately — the Redis subscription happens inside a
    background task — so a test that published straight away would race it.
    """
    deadline: float = asyncio.get_running_loop().time() + timeout
    while True:
        for ch, count in await client.pubsub_numsub(channel):
            name = ch.decode() if isinstance(ch, bytes) else ch
            if name == channel and count > 0:
                return
        if asyncio.get_running_loop().time() > deadline:  # pragma: no cover
            raise AssertionError(f"no subscriber on {channel}")
        await asyncio.sleep(0.005)


async def _drain(queue: asyncio.Queue[Any], *, timeout: float = 1.0) -> Any:
    return await asyncio.wait_for(queue.get(), timeout=timeout)


async def test_a_publish_from_another_worker_reaches_the_subscriber() -> None:
    """The whole reason this class exists: the write lands on one process and
    the stream is parked on another."""
    # Two clients on one fake server — the stand-in for two ASGI workers.
    shared = FakeServer()
    listener, publisher_client = FakeAsyncRedis(server=shared), FakeAsyncRedis(server=shared)
    broker = RedisSubscriptionBroker(listener)
    queue = broker.subscribe(frozenset({"resource:a"}))
    await _await_subscriber(listener, "drf-mcp:sub:resource:a")

    publisher = RedisSubscriptionBroker(publisher_client)
    assert await publisher.publish("resource:a", {"method": "m"}) >= 1
    assert await _drain(queue) == {"method": "m"}

    broker.unsubscribe(queue)
    await listener.aclose()
    await publisher_client.aclose()


async def test_one_subscription_listens_to_all_of_its_topics_at_once() -> None:
    """One listener task per *subscription*, not per topic — the task count is
    bounded by connections rather than by how many things each one watches."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = broker.subscribe(frozenset({"a", "b"}))
    await _await_subscriber(client, "drf-mcp:sub:a")
    await _await_subscriber(client, "drf-mcp:sub:b")
    assert len(broker._tasks) == 1

    await broker.publish("a", 1)
    await broker.publish("b", 2)
    assert {await _drain(queue), await _drain(queue)} == {1, 2}

    broker.unsubscribe(queue)
    await client.aclose()


async def test_publishing_to_a_topic_nobody_watches_reports_zero() -> None:
    client = _client()
    broker = RedisSubscriptionBroker(client)
    assert await broker.publish("quiet", {}) == 0
    await client.aclose()


async def test_unsubscribing_cancels_the_listener() -> None:
    """⚠ Without this the task and its channel subscriptions outlive the
    connection, for the life of the process."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "drf-mcp:sub:a")
    broker.unsubscribe(queue)
    await asyncio.sleep(0.05)
    assert broker._tasks == {}
    await client.aclose()


async def test_unsubscribing_twice_is_a_no_op() -> None:
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = broker.subscribe(frozenset({"a"}))
    broker.unsubscribe(queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_a_subscription_naming_no_topics_starts_no_listener() -> None:
    """An empty filter is legal if odd; the acknowledgement is where the client
    learns it will hear nothing."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = broker.subscribe(frozenset())
    assert broker._tasks == {}
    assert isinstance(queue, asyncio.Queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_the_channel_prefix_is_configurable() -> None:
    """Two servers sharing one Redis must not share a topic space."""
    client = _client()
    broker = RedisSubscriptionBroker(client, channel_prefix="other")
    queue = broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "other:a")
    broker.unsubscribe(queue)
    await client.aclose()


async def test_subscribe_acknowledgement_frames_are_not_delivered_as_payloads() -> None:
    """Redis emits a ``subscribe`` control frame on the same stream; forwarding
    it would put a message on the wire the client never asked for."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "drf-mcp:sub:a")
    await broker.publish("a", {"real": True})
    assert await _drain(queue) == {"real": True}
    broker.unsubscribe(queue)
    await client.aclose()


async def test_constructing_without_the_extra_is_a_clear_error(monkeypatch: Any) -> None:
    """``redis`` is optional, so importing the module must work without it and
    only construction may fail."""
    import rest_framework_mcp.subscriptions.redis_subscription_broker as module

    monkeypatch.setattr(module, "AsyncRedis", None)
    with pytest.raises(ImportError, match=r"\[redis\]"):
        module.RedisSubscriptionBroker(object())
