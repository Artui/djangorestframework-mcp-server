"""Server-pushed notifications: who is listening, and for what.

Two transports over one core. A modern client opens ``subscriptions/listen`` —
a POST whose response stream stays open — and names the notification types it
wants; a legacy client calls ``resources/subscribe`` and reads the GET SSE
stream. Both end up as topics in a :class:`SubscriptionBroker`.

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
