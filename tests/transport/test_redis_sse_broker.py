from __future__ import annotations

import asyncio

import pytest
from fakeredis import FakeAsyncRedis

from rest_framework_mcp.transport.redis_sse_broker import RedisSSEBroker


def _client() -> FakeAsyncRedis:
    """Fresh ``fakeredis`` instance per test — no shared state across tests."""
    return FakeAsyncRedis()


async def _wait_for_subscriber(client, channel: str, *, timeout: float = 0.5) -> None:
    """Wait until the listener task has actually subscribed to ``channel``.

    ``RedisSSEBroker.subscribe`` returns immediately (the listener subscribes
    inside a background task). Tests that publish straight after would race
    the subscription. Poll the Redis-side ``PUBSUB NUMSUB`` until at least
    one subscriber is registered.
    """
    deadline: float = asyncio.get_running_loop().time() + timeout
    while True:
        info = await client.pubsub_numsub(channel)
        # ``pubsub_numsub`` returns ``[(channel, count), ...]`` (bytes keys).
        for entry in info:
            ch, count = entry
            if (ch.decode() if isinstance(ch, bytes) else ch) == channel and count > 0:
                return
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError(f"No subscriber on {channel} within {timeout}s")
        await asyncio.sleep(0.005)


async def test_subscribe_returns_queue_attached_to_channel() -> None:
    client = _client()
    broker = RedisSSEBroker(client)
    queue = broker.subscribe("s1")
    assert isinstance(queue, asyncio.Queue)
    assert broker.has_subscriber("s1")
    # Cleanup
    broker.unsubscribe("s1", queue)
    await client.aclose()


async def test_publish_reaches_subscribed_queue() -> None:
    client = _client()
    broker = RedisSSEBroker(client)
    queue = broker.subscribe("s1")
    try:
        await _wait_for_subscriber(client, "drf-mcp:sse:s1")
        delivered = await broker.publish("s1", {"hi": True})
        assert delivered is True
        payload = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert payload == {"hi": True}
    finally:
        broker.unsubscribe("s1", queue)
        await client.aclose()


async def test_publish_with_no_subscriber_returns_false() -> None:
    client = _client()
    broker = RedisSSEBroker(client)
    delivered = await broker.publish("nobody", {"x": 1})
    assert delivered is False
    await client.aclose()


async def test_resubscribe_replaces_previous_listener() -> None:
    """Re-subscribing cancels the old listener task and replaces the queue."""
    client = _client()
    broker = RedisSSEBroker(client)
    first = broker.subscribe("s1")
    second = broker.subscribe("s1")
    assert first is not second
    # The first queue's task was cancelled; the new queue is what's tracked.
    assert broker._queues["s1"] is second  # type: ignore[attr-defined]
    broker.unsubscribe("s1", second)
    await client.aclose()


async def test_unsubscribe_with_stale_queue_is_noop() -> None:
    """Identity-based unsubscribe protects the new subscriber from old cleanup."""
    client = _client()
    broker = RedisSSEBroker(client)
    old = broker.subscribe("s")
    new = broker.subscribe("s")
    # Old subscriber's cleanup must not remove the new entry.
    broker.unsubscribe("s", old)
    assert broker.has_subscriber("s")
    broker.unsubscribe("s", new)
    assert not broker.has_subscriber("s")
    await client.aclose()


async def test_custom_channel_prefix_isolates_brokers() -> None:
    client = _client()
    broker_a = RedisSSEBroker(client, channel_prefix="env-a")
    broker_b = RedisSSEBroker(client, channel_prefix="env-b")
    qa = broker_a.subscribe("s")
    qb = broker_b.subscribe("s")
    try:
        await _wait_for_subscriber(client, "env-a:s")
        await _wait_for_subscriber(client, "env-b:s")
        # A publish on broker_a's channel must NOT cross into broker_b.
        await broker_a.publish("s", {"src": "a"})
        payload_a = await asyncio.wait_for(qa.get(), timeout=1.0)
        assert payload_a == {"src": "a"}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(qb.get(), timeout=0.1)
    finally:
        broker_a.unsubscribe("s", qa)
        broker_b.unsubscribe("s", qb)
        await client.aclose()


async def test_redis_broker_satisfies_protocol() -> None:
    """``RedisSSEBroker`` is structurally a :class:`SSEBroker`."""
    from rest_framework_mcp.transport.sse_broker import SSEBroker

    client = _client()
    broker = RedisSSEBroker(client)
    assert isinstance(broker, SSEBroker)
    await client.aclose()


async def test_import_error_when_redis_absent(monkeypatch) -> None:
    """The constructor surfaces a clear error when ``redis`` isn't installed."""
    import rest_framework_mcp.transport.redis_sse_broker as mod

    monkeypatch.setattr(mod, "AsyncRedis", None)
    with pytest.raises(ImportError, match="djangorestframework-mcp-server\\[redis\\]"):
        RedisSSEBroker(client=object())
