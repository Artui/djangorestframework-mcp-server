from __future__ import annotations

from typing import Any

from asgiref.sync import async_to_sync
from django.db import transaction

from rest_framework_mcp.constants import JSONRPC_VERSION, RESOURCE_UPDATED_METHOD
from rest_framework_mcp.subscriptions.types.subscription_broker import SubscriptionBroker
from rest_framework_mcp.subscriptions.utils import topic_for_resource


def publish_invalidations(broker: SubscriptionBroker | None, uris: tuple[str, ...]) -> None:
    """Announce that ``uris`` changed — **after the transaction commits**.

    **The commit hook is the whole point.** A mutation tool usually runs inside
    ``transaction.atomic()``, and publishing from inside it announces a change
    that may still roll back: the subscriber re-reads the resource and sees the
    old value, having been told it was new. A missed notification is recovered
    by the next read; a wrong one teaches the client something false.

    ``transaction.on_commit`` takes a *sync* callable while the broker's publish
    is async, and ``async_to_sync`` refuses to run on a thread that already has
    a loop — but both callback paths are loop-free: inside an atomic block
    Django defers to commit time on whichever thread runs the ORM (a
    ``sync_to_async`` worker under ASGI, the request thread otherwise), and
    outside one ``on_commit`` runs inline on that same thread.

    **The async transport routes through here rather than awaiting the broker
    directly** for the same reason. Django connections are thread-local, so
    checking ``in_atomic_block`` from the event loop reads a *different*
    connection, reports no transaction, and publishes a change that has not
    committed. The announcement happens on the thread the write did.

    A server with no broker simply does not push, so this is a no-op rather
    than an error.
    """
    if broker is None or not uris:
        return
    transaction.on_commit(lambda: _publish_now(broker, uris))


def publish_after_commit(
    broker: SubscriptionBroker | None, topic: str, payload: dict[str, Any]
) -> None:
    """One notification, on the same terms as an invalidation.

    Shared with task status so both honour the commit boundary by the same
    route: a task moving to ``completed`` inside a transaction that rolls back
    has the same problem an invalidation does.
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
