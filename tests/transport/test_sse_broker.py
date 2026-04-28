from __future__ import annotations

from rest_framework_mcp.transport.in_memory_sse_broker import InMemorySSEBroker


async def test_publish_to_subscribed_session_returns_true() -> None:
    broker = InMemorySSEBroker()
    queue = broker.subscribe("s1")
    delivered = await broker.publish("s1", {"hi": True})
    assert delivered is True
    payload = await queue.get()
    assert payload == {"hi": True}


async def test_publish_with_no_subscriber_returns_false() -> None:
    broker = InMemorySSEBroker()
    delivered = await broker.publish("nobody", {"x": 1})
    assert delivered is False


async def test_unsubscribe_clears_registration() -> None:
    broker = InMemorySSEBroker()
    queue = broker.subscribe("s")
    assert broker.has_subscriber("s")
    broker.unsubscribe("s", queue)
    assert not broker.has_subscriber("s")


async def test_unsubscribe_ignores_stale_queue() -> None:
    """Re-subscription replaces the queue; the old generator's unsubscribe is a no-op."""
    broker = InMemorySSEBroker()
    old_queue = broker.subscribe("s")
    new_queue = broker.subscribe("s")  # replaces the registration
    # Old subscriber's cleanup must NOT remove the new subscriber's entry.
    broker.unsubscribe("s", old_queue)
    assert broker.has_subscriber("s")
    # New cleanup does the right thing.
    broker.unsubscribe("s", new_queue)
    assert not broker.has_subscriber("s")


async def test_subscribe_replaces_previous_queue() -> None:
    broker = InMemorySSEBroker()
    first = broker.subscribe("s")
    second = broker.subscribe("s")
    assert first is not second
    # Publishing now reaches only the second queue.
    await broker.publish("s", "hello")
    assert second.qsize() == 1
    assert first.qsize() == 0


async def test_publish_blocks_only_until_consumer_reads() -> None:
    """The asyncio.Queue is unbounded by default — publish never blocks indefinitely."""
    broker = InMemorySSEBroker()
    broker.subscribe("s")
    # Push a few in a row.
    for i in range(5):
        await broker.publish("s", {"i": i})
    queue = broker._queues["s"]  # internal access for test introspection only
    assert queue.qsize() == 5
