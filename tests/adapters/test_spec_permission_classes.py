"""Integration tests for ``spec.permission_classes`` plumbing.

These cover the wiring added in Phase 10-pre across the three adapter
entry points (service tools, selector tools, resources). The unit-shaped
tests for the helper and adapter live next to the corresponding modules
under ``tests/auth/permissions/``; what's covered here is the binding-
level result of registration.
"""

from __future__ import annotations

import pytest
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.selector_to_resource import selector_to_resource
from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter


class _Other(BasePermission):
    pass


def _svc() -> None:
    return None


def _sel() -> list[dict[str, str]]:
    return []


def test_service_spec_permission_classes_wrap_into_binding() -> None:
    spec: ServiceSpec = ServiceSpec(
        service=_svc, atomic=False, permission_classes=[IsAuthenticated, _Other]
    )
    binding = service_spec_to_tool(name="t", spec=spec)
    assert len(binding.permissions) == 2
    assert isinstance(binding.permissions[0], DRFPermissionAdapter)
    assert binding.permissions[0].permission_class is IsAuthenticated
    assert isinstance(binding.permissions[1], DRFPermissionAdapter)
    assert binding.permissions[1].permission_class is _Other


def test_service_spec_permission_classes_prepend_to_tool_level() -> None:
    """Spec-declared permissions run before transport-level ones."""

    class _Marker:
        def has_permission(self, request: object, token: object) -> bool:  # noqa: ARG002
            return True

        def required_scopes(self) -> list[str]:
            return []

    tool_perm = _Marker()
    spec: ServiceSpec = ServiceSpec(
        service=_svc, atomic=False, permission_classes=[IsAuthenticated]
    )
    binding = service_spec_to_tool(name="t", spec=spec, permissions=(tool_perm,))
    assert len(binding.permissions) == 2
    assert isinstance(binding.permissions[0], DRFPermissionAdapter)
    assert binding.permissions[1] is tool_perm


def test_service_spec_no_permission_classes_keeps_tool_permissions_intact() -> None:
    class _Marker:
        def has_permission(self, request: object, token: object) -> bool:  # noqa: ARG002
            return True

        def required_scopes(self) -> list[str]:
            return []

    tool_perm = _Marker()
    spec: ServiceSpec = ServiceSpec(service=_svc, atomic=False)
    binding = service_spec_to_tool(name="t", spec=spec, permissions=(tool_perm,))
    assert binding.permissions == (tool_perm,)


def test_service_spec_instance_in_permission_classes_raises() -> None:
    spec: ServiceSpec = ServiceSpec(
        service=_svc,
        atomic=False,
        permission_classes=[IsAuthenticated()],  # type: ignore[list-item]
    )
    with pytest.raises(TypeError, match="'t'"):
        service_spec_to_tool(name="t", spec=spec)


def test_selector_spec_permission_classes_wrap_into_binding() -> None:
    spec: SelectorSpec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_sel, permission_classes=[IsAuthenticated]
    )
    binding = selector_spec_to_tool(name="t", spec=spec)
    assert len(binding.permissions) == 1
    assert isinstance(binding.permissions[0], DRFPermissionAdapter)
    assert binding.permissions[0].permission_class is IsAuthenticated


def test_selector_to_resource_permission_classes_wrap_into_binding() -> None:
    spec: SelectorSpec = SelectorSpec(
        kind=SelectorKind.LIST, selector=_sel, permission_classes=[IsAuthenticated]
    )
    binding = selector_to_resource(name="r", uri_template="r://x", selector=spec)
    assert len(binding.permissions) == 1
    assert isinstance(binding.permissions[0], DRFPermissionAdapter)
    assert binding.permissions[0].permission_class is IsAuthenticated
