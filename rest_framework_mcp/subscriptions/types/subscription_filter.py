from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rest_framework_mcp.constants import NotificationKind


@dataclass(frozen=True)
class SubscriptionFilter:
    """What a subscription asked for, and what the server agreed to.

    One type for both directions, because they are the same shape: the client
    sends a filter on ``subscriptions/listen`` and the server answers with the
    subset it will honour. Two types could drift, and the point of the
    acknowledgement is that the client can compare them.

    **Empty means nothing is delivered.** The spec makes every type opt-in and
    says the server **MUST NOT** send what was not requested, so an absent field
    is a refusal rather than a default: a client that sends ``{}`` is
    acknowledged with nothing and its stream closes immediately.
    """

    kinds: frozenset[NotificationKind] = frozenset()
    resource_uris: tuple[str, ...] = ()
    task_ids: tuple[str, ...] = ()

    @classmethod
    def from_params(cls, raw: Any) -> SubscriptionFilter:
        """Read a filter off the wire, ignoring anything unusable.

        A malformed entry is dropped rather than failing the request: the
        acknowledgement reports what actually took effect, so the drop is
        visible, which beats rejecting a subscription over one bad key.
        """
        if not isinstance(raw, dict):
            return cls()
        kinds: set[NotificationKind] = {
            kind for kind in NotificationKind if raw.get(kind.filter_field) is True
        }
        return cls(
            kinds=frozenset(kinds),
            resource_uris=_strings(raw.get("resourceSubscriptions")),
            task_ids=_strings(raw.get("taskIds")),
        )

    def to_dict(self) -> dict[str, Any]:
        """The wire form, omitting everything not asked for.

        Only truthy entries appear: the acknowledgement reads as "these are the
        things you will receive", so a ``false`` or an empty list in it would
        read as a promise about something.
        """
        out: dict[str, Any] = {kind.filter_field: True for kind in sorted(self.kinds, key=str)}
        if self.resource_uris:
            out["resourceSubscriptions"] = list(self.resource_uris)
        if self.task_ids:
            out["taskIds"] = list(self.task_ids)
        return out

    def __bool__(self) -> bool:
        return bool(self.kinds or self.resource_uris or self.task_ids)


def _strings(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(dict.fromkeys(item for item in raw if isinstance(item, str) and item))


__all__ = ["SubscriptionFilter"]
