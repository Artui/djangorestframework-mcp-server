from __future__ import annotations

import pytest
from rest_framework.permissions import BasePermission, IsAuthenticated

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions


class _Custom(BasePermission):
    pass


def test_wraps_none_to_empty_tuple() -> None:
    assert wrap_spec_permissions(None, label="t") == ()


def test_wraps_empty_to_empty_tuple() -> None:
    assert wrap_spec_permissions([], label="t") == ()


def test_wraps_classes_into_adapters() -> None:
    wrapped = wrap_spec_permissions([IsAuthenticated, _Custom], label="invoices.list")
    assert len(wrapped) == 2
    assert all(isinstance(w, DRFPermissionAdapter) for w in wrapped)
    assert [w.permission_class for w in wrapped] == [IsAuthenticated, _Custom]


def test_rejects_instance_with_label_in_error() -> None:
    with pytest.raises(TypeError, match="'invoices.list'"):
        wrap_spec_permissions([IsAuthenticated()], label="invoices.list")  # type: ignore[list-item]


def test_rejects_non_basepermission_subclass() -> None:
    class NotAPermission:
        pass

    with pytest.raises(TypeError, match="BasePermission subclasses"):
        wrap_spec_permissions([NotAPermission], label="t")  # type: ignore[list-item]
