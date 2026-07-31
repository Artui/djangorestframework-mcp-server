from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from django.db import transaction

from rest_framework_mcp.constants import JSONRPC_VERSION, RESOURCE_UPDATED_METHOD
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.utils import topic_for_resource


def publish_invalidations(broker: SubscriptionBroker | None, uris: tuple[str, ...]) -> None:
    """Announce that ``uris`` changed — **after the transaction commits**.

    ⚠ **The commit hook is the whole point, not a nicety.** A mutation tool
    usually runs inside ``transaction.atomic()``, and publishing from inside it
    announces a change that may still roll back. A subscriber does the natural
    thing — re-reads the resource — and sees the old value, having been told it
    was new. That is worse than never notifying: a missed notification is
    recovered by the next read, a wrong one teaches the client something false.

    ⚠ **Which is why the sync/async handling here is more than boilerplate.**
    ``transaction.on_commit`` takes a *sync* callable, and the broker's publish
    is async, so the two have to be bridged — but ``async_to_sync`` refuses to
    run on a thread that already has a running event loop, and where the
    callback runs depends on whether there is a transaction at all:

    - **Inside an atomic block**, Django defers the callback to commit time,
      which happens on whichever thread is running the ORM. Under the async
      transport that is a ``sync_to_async`` worker thread, and under the sync
      one it is the request thread — neither has a loop, so the bridge is safe.
    - **Outside one**, ``on_commit`` runs the callback *immediately, inline* —
      on that same loop-free thread, so the bridge is safe there too.

    ⚠ **Which is why the async transport routes through here rather than
    awaiting the broker directly.** Django connections are thread-local, and
    under ASGI the ORM work runs on a ``sync_to_async`` worker while the
    coroutine resumes on the event loop thread. Checking ``in_atomic_block``
    from the loop reads a *different* connection, reports no transaction, and
    publishes immediately — announcing a change that has not committed and may
    not. The announcement therefore happens on the same thread the write did.

    A server with no broker is not misconfigured, it simply does not push, so
    this is a no-op rather than an error.
    """
    if broker is None or not uris:
        return
    transaction.on_commit(lambda: _publish_now(broker, uris))


def publish_after_commit(
    broker: SubscriptionBroker | None, topic: str, payload: dict[str, Any]
) -> None:
    """One notification, on the same terms as an invalidation.

    Shared with task status so both honour the commit boundary by the same
    route — a task moving to ``completed`` inside a transaction has the same
    problem an invalidation does, and a second copy of this reasoning would be
    a second place for it to drift.
    """
    if broker is None:
        return
    transaction.on_commit(lambda: _publish_one(broker, topic, payload))


def _publish_one(broker: SubscriptionBroker, topic: str, payload: dict[str, Any]) -> None:
    async_to_sync(broker.publish)(topic, payload)


def resource_updated(uri: str) -> dict[str, Any]:
    """The ``notifications/resources/updated`` frame for one URI."""
    return {
        "jsonrpc": JSONRPC_VERSION,
        "method": RESOURCE_UPDATED_METHOD,
        "params": {"uri": uri},
    }


def _publish_now(broker: SubscriptionBroker, uris: tuple[str, ...]) -> None:
    """Run the publishes from a committed, loop-free thread."""
    async_to_sync(_publish_all)(broker, uris)


async def _publish_all(broker: SubscriptionBroker, uris: tuple[str, ...]) -> None:
    for uri in uris:
        await broker.publish(topic_for_resource(uri), resource_updated(uri))


__all__ = ["publish_after_commit", "publish_invalidations", "resource_updated"]
