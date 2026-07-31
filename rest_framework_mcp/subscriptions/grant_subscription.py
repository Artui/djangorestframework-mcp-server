from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import NotificationKind
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions
from rest_framework_mcp.subscriptions.types.subscription_filter import SubscriptionFilter
from rest_framework_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
    topic_for_task,
)


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
    - **A task id is granted only to the principal that created it**, and only
      on a server that runs tasks at all. The same comparison ``tasks/get``
      makes, for the same reason.
    - **A list-changed kind is granted only if the server has such a registry.**
      Matching the capability rule: advertising something this server cannot
      produce leaves a client waiting for an event that will never come.

    ⚠ **A refused entry is dropped, not an error.** The acknowledgement is the
    channel for "you will not get this", and the spec uses it exactly that way —
    unsupported types are *omitted* from the agreed set. Erroring on one bad URI
    would also make the endpoint an oracle: a client could distinguish "no such
    resource" from "not yours" by whether the subscription opened.

    ⛔ **One exception, and it is the spec's:** a client asking for ``taskIds``
    without declaring the tasks extension **MUST** get a JSON-RPC error rather
    than a quiet omission. That check happens at the transport, before this runs
    — see ``_maybe_subscribe`` — because it produces an error response rather
    than a stream. It is not an oracle: it turns on what the *client* declared,
    not on anything about the tasks it named.
    """
    kinds: frozenset[NotificationKind] = frozenset(
        kind for kind in requested.kinds if _has_registry(kind, context)
    )
    uris: tuple[str, ...] = tuple(
        uri for uri in requested.resource_uris if _may_watch_resource(uri, context)
    )
    task_ids: tuple[str, ...] = tuple(
        task_id for task_id in requested.task_ids if _may_watch_task(task_id, context)
    )
    granted = SubscriptionFilter(kinds=kinds, resource_uris=uris, task_ids=task_ids)
    topics: frozenset[str] = frozenset(
        [topic_for_kind(kind) for kind in kinds]
        + [topic_for_resource(uri) for uri in uris]
        + [topic_for_task(task_id) for task_id in task_ids]
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


def _may_watch_task(task_id: str, context: MCPCallContext) -> bool:
    """Whether this caller may be told how ``task_id`` is progressing.

    The same ownership comparison ``tasks/get`` makes. A task's status is as
    revealing as its result — knowing that someone else's export finished is
    knowing they ran one.
    """
    # Imported here rather than at module scope: ``handlers.tasks_utils`` reaches
    # the handler layer, which reaches back to this module through the trigger
    # path. The cycle is genuine and this is the documented exception.
    from rest_framework_mcp.handlers.tasks_utils import owned_by_caller

    store = context.tasks
    if store is None:
        return False
    record = store.get(task_id)
    return record is not None and owned_by_caller(record, context)


__all__ = ["grant_subscription"]
