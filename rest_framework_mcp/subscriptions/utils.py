"""Topic names, built in one place so publisher and subscriber cannot disagree.

Every topic is a prefixed string; the prefix keeps one namespace from colliding
with another, since the values are caller-supplied.
"""

from __future__ import annotations

from rest_framework_mcp.constants import NotificationKind


def topic_for_kind(kind: NotificationKind) -> str:
    """The topic a list-changed notification is published to.

    One topic per kind, server-wide: a tool list changing is the same event for
    every subscriber.
    """
    return f"kind:{kind.value}"


def topic_for_resource(uri: str) -> str:
    """The topic ``notifications/resources/updated`` for ``uri`` goes to.

    **Exact URI, never a prefix.** The spec permits notifying about a
    *sub*-resource of the one subscribed to, which invites prefix matching, but
    a prefix match over a free-form URI guesses at a scheme this package does
    not own and fails both ways: ``invoices://1`` would match ``invoices://11``,
    while a tenant-scoped ``t1/invoices://…`` would match nothing.

    A publisher that wants a collection watched says so, by publishing the
    collection URI alongside the instance one — an ``invalidates=`` naming both
    ``invoices://{pk}`` and ``invoices://`` is explicit and reviewable where a
    matching rule is neither.
    """
    return f"resource:{uri}"


def topic_for_task(task_id: str) -> str:
    """The topic a task's ``notifications/tasks`` frames go to.

    Task ids carry 32 bytes of entropy, so the topic name is unguessable too,
    but that is a property of the id and not a substitute for the ownership
    check :func:`grant_subscription` makes before attaching: a topic is not an
    authorization boundary.
    """
    return f"task:{task_id}"


__all__ = ["topic_for_kind", "topic_for_resource", "topic_for_task"]
