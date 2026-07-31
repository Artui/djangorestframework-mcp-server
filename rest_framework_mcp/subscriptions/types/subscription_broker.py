from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SubscriptionBroker(Protocol):
    """Topic-keyed pub/sub for notifications, with **many** subscribers per topic.

    ⚠ **Deliberately not ``SSEBroker``**, which this package already has. That
    one keys on session id and documents "a session has at most one live
    subscriber" — both assumptions fail here:

    - A notification is addressed to a *topic* ("this resource changed"), not
      to a session. The set of subscribers is discovered at publish time and
      the publisher does not know it.
    - Several clients — and several subscriptions from one client — legitimately
      watch the same resource. Replacing the previous subscriber, which is what
      the session broker does on re-subscribe, would silently disconnect them.

    So it is a second collaborator rather than a widening of the first. The
    session broker keeps serving the legacy GET stream, whose contract really is
    one-per-session.

    **Topics are opaque strings** built by
    :mod:`rest_framework_mcp.subscriptions.utils` — a resource URI, a task id, a
    notification kind. The broker never interprets them, so adding a new
    notification type needs no change here.

    ⚠ **A topic is not an authorization boundary.** Anyone who can name a topic
    can receive it, so the *subscription* checks what the caller may watch before
    it attaches. Putting the check in the broker would be checking it once per
    delivery instead of once per subscription, and would need the broker to
    understand principals.

    :meth:`subscribe` returns a queue that receives every payload published to
    any of ``topics`` until :meth:`unsubscribe`. One queue per subscription, not
    per topic — a subscription watching five resources reads one stream, which
    is what the wire format wants.
    """

    def subscribe(self, topics: frozenset[str]) -> asyncio.Queue[Any]: ...

    def unsubscribe(self, queue: asyncio.Queue[Any]) -> None: ...

    async def publish(self, topic: str, payload: Any) -> int: ...


__all__ = ["SubscriptionBroker"]
