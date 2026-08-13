"""Server-pushed notifications: who is listening, and for what.

A modern client opens ``subscriptions/listen`` — a POST whose response stream
stays open — and names the notification types it wants. What it is granted
becomes a set of topics in a [`SubscriptionBroker`][rest_framework_mcp.subscriptions.types.subscription_broker.SubscriptionBroker].

The legacy-era ``resources/subscribe`` is **not implemented**: the ``2026-07-28``
schema folds resource subscriptions into the ``subscriptions/listen`` filter,
and serving the predecessor would need a cross-process session-to-URI registry,
since its notifications ride the session's GET stream rather than a stream of
its own.

[`InMemorySubscriptionBroker`][rest_framework_mcp.subscriptions.in_memory_subscription_broker.InMemorySubscriptionBroker] is single-worker only and fails silently
past that. ``RedisSubscriptionBroker`` is the deployable one; it is not imported
here because ``redis`` is an optional extra.
"""

from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter

__all__ = ["InMemorySubscriptionBroker", "SubscriptionBroker", "SubscriptionFilter"]
