from __future__ import annotations

import asyncio
from typing import Any


class InMemorySubscriptionBroker:
    """In-process topic fan-out, for development and tests.

    **A multi-worker deployment needs a cross-process broker.** The write that
    triggers a notification lands on whichever worker served that request while
    the subscriber's stream is parked on a different one, so an in-process
    broker delivers to nobody, silently, and the failure looks exactly like "the
    resource never changed". Subscriptions are a **single-worker feature** until
    a cross-process broker is passed to ``MCPServer(subscription_broker=…)``.

    **Not a default.** ``MCPServer`` constructs no broker at all when none is
    given, precisely so this class cannot be reached by accident.

    Unlike the session broker this keeps a *set* of queues per topic, and a
    queue may sit under several topics at once — one subscription watching five
    resources reads a single stream. Both directions of that mapping are kept so
    ``unsubscribe`` does not have to walk every topic.
    """

    def __init__(self) -> None:
        # Instance state, never module-level: two servers in one process keep
        # separate subscription spaces.
        self._by_topic: dict[str, set[asyncio.Queue[Any]]] = {}
        self._topics_by_queue: dict[int, tuple[asyncio.Queue[Any], frozenset[str]]] = {}

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        # Nothing to await — registration is a dict write. Async to match the
        # protocol, which is shaped by the Redis implementation's need to
        # confirm its channels before anyone is told the subscription is live.
        queue: asyncio.Queue[Any] = asyncio.Queue()
        for topic in topics:
            self._by_topic.setdefault(topic, set()).add(queue)
        # Keyed by ``id`` with the queue held in the value, so the identity
        # comparison the reverse index relies on is explicit.
        self._topics_by_queue[id(queue)] = (queue, topics)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        entry = self._topics_by_queue.pop(id(queue), None)
        if entry is None:
            return
        _, topics = entry
        for topic in topics:
            # Always present: ``subscribe`` registered this queue under every one
            # of these topics, and a topic outlives its last subscriber only
            # while that subscriber is still in the index just popped.
            subscribers = self._by_topic[topic]
            subscribers.discard(queue)
            if not subscribers:
                # Topics are unbounded and caller-named (a resource URI), so an
                # emptied one is removed rather than left behind — otherwise a
                # long-lived server accumulates one entry per URI ever watched.
                del self._by_topic[topic]

    @property
    def active_subscriptions(self) -> int:
        return len(self._topics_by_queue)

    async def publish(self, topic: str, payload: Any) -> int:
        """Deliver to every subscriber of ``topic``; returns how many got it.

        ``0`` means nobody was listening, which is ordinary and not an error:
        notifications are best-effort by design, and a client that missed one
        re-reads the resource.
        """
        subscribers = self._by_topic.get(topic)
        if not subscribers:
            return 0
        # Iterate a copy: a subscriber whose stream is unwinding can call
        # ``unsubscribe`` while this runs, and mutating the set mid-iteration
        # would raise into whichever request happened to be publishing.
        for queue in tuple(subscribers):
            await queue.put(payload)
        return len(subscribers)


__all__ = ["InMemorySubscriptionBroker"]
