"""Topic names, built in one place so publisher and subscriber cannot disagree.

Every topic is a prefixed string. The prefix keeps one namespace from colliding
with another as kinds are added — the values are caller-supplied and this package
does not own them.
"""

from __future__ import annotations

from rest_framework_mcp.constants import NotificationKind


def topic_for_kind(kind: NotificationKind) -> str:
    """The topic a list-changed notification is published to.

    One topic per kind, server-wide: a tool list changing is the same event for
    every subscriber, so there is nothing to key it by.
    """
    return f"kind:{kind.value}"


def topic_for_resource(uri: str) -> str:
    """The topic ``notifications/resources/updated`` for ``uri`` goes to.

    ⚠ **Exact URI, never a prefix.** The spec permits notifying about a
    *sub*-resource of the one subscribed to, which invites prefix matching — but
    a prefix match over a free-form URI is a guess about a scheme this package
    does not own, and it fails in both directions: ``invoices://1`` would match
    ``invoices://11``, while a tenant-scoped ``t1/invoices://…`` would match
    nothing a client sensibly subscribed to.

    A publisher that wants a collection watched says so, by publishing the
    collection URI as well as the instance one. That is what an ``invalidates=``
    naming both ``invoices://{pk}`` and ``invoices://`` expresses — the
    declaration is explicit and reviewable, where a matching rule is neither.
    """
    return f"resource:{uri}"


__all__ = ["topic_for_kind", "topic_for_resource"]
