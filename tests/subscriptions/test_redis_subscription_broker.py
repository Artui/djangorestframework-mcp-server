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
    return FakeAsyncRedis(server=FakeServer())


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
    queue = await broker.subscribe(frozenset({"resource:a"}))
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
    queue = await broker.subscribe(frozenset({"a", "b"}))
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
    """Without this the task and its channel subscriptions outlive the
    connection, for the life of the process."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "drf-mcp:sub:a")
    broker.unsubscribe(queue)
    await asyncio.sleep(0.05)
    assert broker._tasks == {}
    await client.aclose()


async def test_unsubscribing_twice_is_a_no_op() -> None:
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    broker.unsubscribe(queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_a_subscription_naming_no_topics_starts_no_listener() -> None:
    """An empty filter is legal if odd; the acknowledgement is where the client
    learns it will hear nothing."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset())
    assert broker._tasks == {}
    assert isinstance(queue, asyncio.Queue)
    broker.unsubscribe(queue)
    await client.aclose()


async def test_the_channel_prefix_is_configurable() -> None:
    """Two servers sharing one Redis must not share a topic space."""
    client = _client()
    broker = RedisSubscriptionBroker(client, channel_prefix="other")
    queue = await broker.subscribe(frozenset({"a"}))
    await _await_subscriber(client, "other:a")
    broker.unsubscribe(queue)
    await client.aclose()


async def test_subscribe_acknowledgement_frames_are_not_delivered_as_payloads() -> None:
    """Redis emits a ``subscribe`` control frame on the same stream; forwarding
    it would put a message on the wire the client never asked for."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
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


async def test_subscribe_returns_only_once_the_channels_are_live() -> None:
    """The race this closes: ``subscribe`` used to register in a background
    task, so the caller emitted "you are subscribed" while publishes were still
    going nowhere — in exactly the multi-process deployment this class exists
    for, where the publisher is another process and waits for nobody."""
    shared = FakeServer()
    listener, publisher = FakeAsyncRedis(server=shared), FakeAsyncRedis(server=shared)
    broker = RedisSubscriptionBroker(listener)
    queue = await broker.subscribe(frozenset({"a"}))

    # No polling helper, no sleep: the await is the guarantee.
    assert await RedisSubscriptionBroker(publisher).publish("a", {"n": 1}) >= 1
    assert await _drain(queue) == {"n": 1}

    broker.unsubscribe(queue)
    await listener.aclose()
    await publisher.aclose()


async def test_the_channels_are_given_back_when_a_subscription_unwinds() -> None:
    """Observable now that the release is a real coroutine rather than a
    suppressed block in a cancelled task's ``finally`` — a wrong method name
    would have leaked the channel silently."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    queue = await broker.subscribe(frozenset({"a"}))
    assert (await client.pubsub_numsub("drf-mcp:sub:a"))[0][1] == 1

    broker.unsubscribe(queue)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if (await client.pubsub_numsub("drf-mcp:sub:a"))[0][1] == 0:
            break
    assert (await client.pubsub_numsub("drf-mcp:sub:a"))[0][1] == 0
    await client.aclose()


async def test_active_subscriptions_counts_this_workers_streams() -> None:
    """What ``MAX_CONCURRENT_SUBSCRIPTIONS`` bounds. Per process by design — a
    cluster-wide count would cost a round trip on every subscribe to bound
    something that is already a per-worker resource."""
    client = _client()
    broker = RedisSubscriptionBroker(client)
    assert broker.active_subscriptions == 0
    one = await broker.subscribe(frozenset({"a"}))
    two = await broker.subscribe(frozenset({"b"}))
    assert broker.active_subscriptions == 2
    broker.unsubscribe(one)
    assert broker.active_subscriptions == 1
    broker.unsubscribe(two)
    await client.aclose()


async def test_two_servers_on_one_redis_do_not_share_a_topic_space() -> None:
    """Topic names are built from caller-supplied values — a notification kind,
    a resource URI — so two servers that register the same ``SelectorSpec``
    under the same URI derive the *same* topic. On a shared default prefix a
    subscriber authorized on the public server then receives the internal
    server's change signals, learning the change cadence of a resource it was
    never granted, past the ``resources/read`` check ``grant_subscription``
    gates subscriptions on. The cache-backed stores fold the server name into
    their key prefix for exactly this reason."""
    shared = FakeServer()
    public = RedisSubscriptionBroker(FakeAsyncRedis(server=shared), namespace="public")
    internal = RedisSubscriptionBroker(FakeAsyncRedis(server=shared), namespace="internal")

    queue = await public.subscribe(frozenset({"resource:invoices://1"}))
    await _await_subscriber(
        public._client,  # noqa: SLF001
        public._channel("resource:invoices://1"),  # noqa: SLF001
    )
    # The other server publishes the identical topic name.
    assert await internal.publish("resource:invoices://1", {"n": 1}) == 0
    assert queue.empty()

    # ...and the same server still reaches its own subscriber.
    assert await public.publish("resource:invoices://1", {"n": 2}) == 1
    assert await _drain(queue) == {"n": 2}

    public.unsubscribe(queue)
    await public._client.aclose()  # noqa: SLF001
    await internal._client.aclose()  # noqa: SLF001
