from __future__ import annotations

import asyncio
from typing import Any


class InMemorySubscriptionBroker:
    """In-process topic fan-out. The default, and single-worker only.

    ⚠ **A multi-worker deployment needs a cross-process broker.** The write that
    triggers a notification lands on whichever worker served that request, and
    the subscriber's stream is parked on a different one — so an in-process
    broker delivers to nobody, silently. The failure looks exactly like "the
    resource never changed", which is why it is worth being blunt about:
    subscriptions are a **single-worker feature** until a broker that crosses
    processes is wired in. Pass one to ``MCPServer(subscription_broker=…)``.

    Unlike the session broker this keeps a *set* of queues per topic, and a
    queue may sit under several topics at once — one subscription watching five
    resources reads a single stream. Both directions of that mapping are kept so
    ``unsubscribe`` does not have to walk every topic.
    """

    def __init__(self) -> None:
        # Instance state, never module-level: two servers in one process keep
        # separate subscription spaces, and a test cannot leak into the next.
        self._by_topic: dict[str, set[asyncio.Queue[Any]]] = {}
        self._topics_by_queue: dict[int, tuple[asyncio.Queue[Any], frozenset[str]]] = {}

    def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]:
        queue: asyncio.Queue[Any] = asyncio.Queue()
        for topic in topics:
            self._by_topic.setdefault(topic, set()).add(queue)
        # Keyed by ``id`` because ``asyncio.Queue`` is unhashable-by-value only
        # in the sense that equality is identity — using the object as a dict
        # key works, but holding the queue in the value keeps the reverse index
        # readable and makes the identity comparison explicit.
        self._topics_by_queue[id(queue)] = (queue, topics)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None:
        entry = self._topics_by_queue.pop(id(queue), None)
        if entry is None:
            return
        _, topics = entry
        for topic in topics:
            # Always present: ``subscribe`` registered this queue under every one
            # of these topics, and a topic is only deleted once its last
            # subscriber is discarded — which cannot have happened while this
            # queue was still in the reverse index we just popped.
            subscribers = self._by_topic[topic]
            subscribers.discard(queue)
            if not subscribers:
                # Topics are unbounded and caller-named (a resource URI), so an
                # emptied one is removed rather than left as a permanent empty
                # set — otherwise a long-lived server accumulates one entry per
                # URI anyone ever watched.
                del self._by_topic[topic]

    async def publish(self, topic: str, payload: Any) -> int:
        """Deliver to every subscriber of ``topic``; returns how many got it.

        The count is for the caller's benefit — ``0`` means nobody was
        listening, which is ordinary and not an error. Notifications are
        best-effort by design: a client that missed one re-reads the resource.
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
