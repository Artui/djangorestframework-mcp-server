"""``guard_resource_object`` — object-level permissions for ``resources/read``."""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import BasePermission
from rest_framework_services import enforce_permissions
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding


def guard_resource_object(binding: ResourceBinding, value: Any, context: OfflineContext) -> None:
    """Run the resource's declared permissions against the value it resolved.

    A resource is registered from a ``SelectorSpec``, so its
    ``permission_classes`` are the author's whole authorization contract — and
    on every other transport the object-level half of that contract
    (``has_object_permission``) runs against the resolved row. Before this
    existed, ``resources/read`` ran the class-level half only, so a spec whose
    ownership test lives in ``has_object_permission`` handed one tenant's row
    to another over this method while holding everywhere else.

    Delegates to drf-services'
    [`enforce_permissions`][rest_framework_services.dispatch.enforce_permissions.enforce_permissions],
    the same guard the tool paths pass to ``dispatch_spec`` as
    ``on_target_resolved``, rather than re-deriving the check here: it owns the
    rule that ``has_object_permission`` runs for a ``Model`` and is skipped for
    a queryset (a ``LIST`` resource is authorized per-set, not per-row), and it
    owns the denial's message and code.

    The classes come back off the binding's wrapped permission tuple. A
    ``ResourceBinding`` keeps the wrapped
    [`DRFPermissionAdapter`][rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter]s
    rather than the spec, so unwrapping them is how the author's contract is
    recovered; a per-registration ``MCPPermission`` is left alone, having no
    object-level half to run. Raises ``PermissionDenied``; the caller maps it.
    """
    permission_classes: list[type[BasePermission]] = [
        perm.permission_class
        for perm in binding.permissions
        if isinstance(perm, DRFPermissionAdapter)
    ]
    if not permission_classes:
        return
    # A spec is what ``enforce_permissions`` reads ``permission_classes`` off,
    # so the binding's fields are lifted back into one. Nothing else on it is
    # consulted.
    spec: SelectorSpec[Any, Any] = SelectorSpec(
        kind=binding.kind,
        selector=binding.selector,
        permission_classes=permission_classes,
    )
    enforce_permissions(spec, context, instance=value)


__all__ = ["guard_resource_object"]
