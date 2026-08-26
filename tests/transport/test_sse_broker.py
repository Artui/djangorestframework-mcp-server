from __future__ import annotations

import pytest

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


async def test_a_stalled_reader_cannot_grow_the_queue_without_limit() -> None:
    """The stream blocks on ``yield`` while a paused client drains nothing, and
    ``notify`` may be called per model save. Unbounded, that is one resident
    payload per notification for as long as the connection is held — and it is
    held indefinitely, there being nothing else to close it."""
    broker = InMemorySSEBroker(max_queued_events=2)
    queue = broker.subscribe("s1")
    assert await broker.publish("s1", {"n": 1}) is True
    assert await broker.publish("s1", {"n": 2}) is True
    # Past the bound: the oldest goes, and the caller is told delivery was not
    # clean through the same ``False`` that already means "nobody got this".
    assert await broker.publish("s1", {"n": 3}) is False
    assert queue.qsize() == 2
    assert [queue.get_nowait(), queue.get_nowait()] == [{"n": 2}, {"n": 3}]


async def test_an_unusable_queue_bound_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="max_queued_events must be positive"):
        InMemorySSEBroker(max_queued_events=0)


async def test_active_streams_counts_live_subscribers() -> None:
    """What the concurrency cap is measured against, so it has to track both
    directions."""
    broker = InMemorySSEBroker()
    assert broker.active_streams == 0
    first = broker.subscribe("s1")
    broker.subscribe("s2")
    assert broker.active_streams == 2
    broker.unsubscribe("s1", first)
    assert broker.active_streams == 1
