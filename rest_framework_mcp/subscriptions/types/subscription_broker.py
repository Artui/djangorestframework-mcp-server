from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SubscriptionBroker(Protocol):
    """Topic-keyed pub/sub for notifications, with **many** subscribers per topic.

    **Deliberately not ``SSEBroker``**, which this package already has for the
    legacy GET stream: that one keys on session id and allows a session at most
    one live subscriber. Here a notification is addressed to a *topic*, whose
    subscriber set is discovered at publish time, and several clients — or
    several subscriptions from one client — legitimately watch the same
    resource, so replacing the previous subscriber would silently disconnect
    them.

    **Topics are opaque strings** built by
    :mod:`rest_framework_mcp.subscriptions.utils` — a resource URI, a task id, a
    notification kind. The broker never interprets them, so adding a new
    notification type needs no change here.

    **A topic is not an authorization boundary.** Anyone who can name a topic
    can receive it, so the *subscription* checks what the caller may watch before
    it attaches — once per subscription rather than once per delivery, and
    without the broker having to understand principals.

    :meth:`subscribe` returns a queue that receives every payload published to
    any of ``topics`` until :meth:`unsubscribe`. One queue per subscription, not
    per topic — a subscription watching five resources reads one stream, which
    is what the wire format wants. It is awaitable because that is load-bearing:
    it must not return until the subscription is genuinely live, or the caller
    emits "you are subscribed" while publishes still go nowhere.
    """

    async def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, topic: str, payload: Any) -> int: ...

    @property
    def active_subscriptions(self) -> int:
        """How many subscriptions this broker is currently feeding.

        **Per process, not per cluster.** It bounds this worker's occupancy —
        see ``MAX_CONCURRENT_SUBSCRIPTIONS`` — and a cluster-wide count would
        cost a round trip per subscribe to bound a per-worker resource.
        """
        ...


__all__ = ["SubscriptionBroker"]
