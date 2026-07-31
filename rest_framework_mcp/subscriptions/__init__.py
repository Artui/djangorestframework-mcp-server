"""Server-pushed notifications: who is listening, and for what.

A modern client opens ``subscriptions/listen`` — a POST whose response stream
stays open — and names the notification types it wants. What it is granted
becomes a set of topics in a :class:`SubscriptionBroker`.

⛔ The legacy-era ``resources/subscribe`` is **not implemented**. It is not the
counterpart of this method but its predecessor: the ``2026-07-28`` schema folds
resource subscriptions into the ``subscriptions/listen`` filter and says so.
Serving it needs a cross-process session-to-URI registry, since a legacy
subscription's notifications ride the session's GET stream rather than a stream
of its own.

⚠ The in-memory broker is single-worker only, and fails silently past that: the
write that triggers a notification lands on one worker and the subscriber's
stream is parked on another. :class:`RedisSubscriptionBroker` is the deployable
one; it is not imported here because ``redis`` is an optional extra.
"""

from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter

__all__ = ["InMemorySubscriptionBroker", "SubscriptionBroker", "SubscriptionFilter"]
