from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from rest_framework.permissions import BasePermission

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter


def wrap_spec_permissions(
    permission_classes: Sequence[type[BasePermission]] | None,
    *,
    label: str,
) -> tuple[DRFPermissionAdapter, ...]:
    """Project a ``Sequence[type[BasePermission]]`` into wrapped ``MCPPermission`` adapters.

    ``permission_classes`` is the value sister-repo's ``ServiceSpec`` /
    ``SelectorSpec`` carries — a list of DRF ``BasePermission`` **classes**.
    Each is wrapped in :class:`DRFPermissionAdapter` so it satisfies the MCP
    permission Protocol. ``None`` and the empty sequence collapse to an empty
    tuple (the spec author's "no permission contract" sentinel).

    Misconfigurations (instances instead of classes; non-``BasePermission``
    subclasses) fail fast with ``TypeError`` — same posture sister-repo
    enforces at ``as_view()`` time, surfaced here at registration so the
    error is visible during development rather than first request.

    ``label`` is the binding name; it's woven into the error message so a
    developer registering many tools knows which one is misconfigured.
    """
    if not permission_classes:
        return ()
    wrapped: list[DRFPermissionAdapter] = []
    for entry in permission_classes:
        if not isinstance(entry, type) or not issubclass(entry, BasePermission):
            received: Any = entry
            raise TypeError(
                f"spec.permission_classes for {label!r} must be a sequence of "
                f"BasePermission subclasses; got {received!r}. Pass the class "
                f"(e.g. `IsAuthenticated`), not an instance (`IsAuthenticated()`)."
            )
        wrapped.append(DRFPermissionAdapter(entry))
    return tuple(wrapped)


__all__ = ["wrap_spec_permissions"]
