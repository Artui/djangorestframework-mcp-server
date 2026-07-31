from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import NotificationKind
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from rest_framework_mcp.subscriptions.utils import topic_for_kind, topic_for_resource


def grant_subscription(
    requested: SubscriptionFilter, context: MCPCallContext
) -> tuple[SubscriptionFilter, frozenset[str]]:
    """Decide what a subscription may actually receive, and from which topics.

    Returns ``(granted, topics)``. ``granted`` goes back to the client in the
    acknowledgement; ``topics`` is what the stream attaches to. They are
    produced together so they cannot disagree — a topic granted but not
    announced would deliver notifications the client was told it would not get,
    and the reverse would promise silence.

    ⚠ **This is the authorization boundary for the whole feature.** A topic name
    is not a secret and the broker does not check anything, so everything that
    decides *who may hear what* is here:

    - **A resource URI is granted only if it resolves to a registered resource
      and this caller may read it.** Otherwise a subscription would be a side
      channel around ``resources/read``: a caller denied the body could still
      learn every time it changed, which leaks activity — often the more
      sensitive signal of the two.
    - ⛔ **``taskIds`` is never granted yet.** Nothing in this package publishes
      to a task topic — the tasks extension defines ``notifications/tasks`` over
      this stream, but wiring ``transition_task`` to publish is separate work.
      Granting it meanwhile would make the acknowledgement *lie*: the client
      would be told the server agreed to honour a subscription that can only
      ever be silent, which is the exact failure the acknowledgement exists to
      prevent. It is refused here, visibly, until there is something to deliver.
    - **A list-changed kind is granted only if the server has such a registry.**
      Matching the capability rule: advertising something this server cannot
      produce leaves a client waiting for an event that will never come.

    ⚠ **A refused entry is dropped, not an error.** The acknowledgement is the
    channel for "you will not get this", and the spec uses it exactly that way —
    unsupported types are *omitted* from the agreed set. Erroring on one bad URI
    would also make the endpoint an oracle: a client could distinguish "no such
    resource" from "not yours" by whether the subscription opened.
    """
    kinds: frozenset[NotificationKind] = frozenset(
        kind for kind in requested.kinds if _has_registry(kind, context)
    )
    uris: tuple[str, ...] = tuple(
        uri for uri in requested.resource_uris if _may_watch_resource(uri, context)
    )
    granted = SubscriptionFilter(kinds=kinds, resource_uris=uris)
    topics: frozenset[str] = frozenset(
        [topic_for_kind(kind) for kind in kinds] + [topic_for_resource(uri) for uri in uris]
    )
    return granted, topics


def _has_registry(kind: NotificationKind, context: MCPCallContext) -> bool:
    """Whether the registry behind ``kind`` holds anything.

    The spec's own example of a type to omit from the acknowledgement is
    "``promptsListChanged`` when the server has no prompts", so this is the
    rule rather than an invention.

    ⚠ Mapped explicitly, with no fallthrough. A fourth kind used to land on the
    resource registry by default — silently wrong, in a module that advertises
    that new kinds need no change here. Now it raises on the way in.
    """
    registries: dict[NotificationKind, int] = {
        NotificationKind.TOOLS_LIST_CHANGED: len(context.tools),
        NotificationKind.PROMPTS_LIST_CHANGED: len(context.prompts),
        NotificationKind.RESOURCES_LIST_CHANGED: len(context.resources),
    }
    return registries[kind] > 0


def _may_watch_resource(uri: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told that ``uri`` changed.

    Resolved through the same registry lookup ``resources/read`` uses, so a
    template-backed URI is matched the way the reader matches it rather than by
    a second, drifting rule.
    """
    resolved: Any = context.resources.resolve(uri)
    if resolved is None:
        return False
    binding: Any = resolved[0]
    allowed, _ = check_permissions(binding.permissions, context.http_request, context.token)
    return bool(allowed)


__all__ = ["grant_subscription"]
