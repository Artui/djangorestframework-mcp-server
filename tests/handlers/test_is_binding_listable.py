"""Unit tests for the shared per-binding listability helper."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


class _AllowAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return True

    def required_scopes(self) -> list[str]:
        return []


class _DenyAll:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return False

    def required_scopes(self) -> list[str]:
        return []


class _IsListableAware:
    """Permission with a list-time-specific opt-out."""

    def __init__(self, *, allow_call: bool, allow_list: bool) -> None:
        self.allow_call = allow_call
        self.allow_list = allow_list

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:  # noqa: ARG002
        return self.allow_call

    def is_listable(self, token: TokenInfo) -> bool:  # noqa: ARG002
        return self.allow_list

    def required_scopes(self) -> list[str]:
        return []


def _svc() -> None:
    return None


def _sel() -> list[Any]:
    return []


def _tool(*, permissions: tuple[Any, ...] = (), always_listed: bool = False) -> ToolBinding:
    return ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=_svc, atomic=False),
        permissions=permissions,
        always_listed=always_listed,
    )


def _request() -> HttpRequest:
    return HttpRequest()


def _token() -> TokenInfo:
    return TokenInfo(user=None)


def test_no_permissions_is_listable() -> None:
    assert is_binding_listable(_tool(), _request(), _token()) is True


def test_all_allow_is_listable() -> None:
    assert is_binding_listable(_tool(permissions=(_AllowAll(),)), _request(), _token()) is True


def test_any_deny_is_not_listable() -> None:
    assert is_binding_listable(_tool(permissions=(_DenyAll(),)), _request(), _token()) is False


def test_and_combined_short_circuits_on_first_deny() -> None:
    assert (
        is_binding_listable(
            _tool(permissions=(_AllowAll(), _DenyAll(), _AllowAll())), _request(), _token()
        )
        is False
    )


def test_always_listed_overrides_deny() -> None:
    """``always_listed=True`` opts the binding back into the listing even when denied."""
    assert (
        is_binding_listable(
            _tool(permissions=(_DenyAll(),), always_listed=True), _request(), _token()
        )
        is True
    )


def test_permission_with_is_listable_uses_that_method() -> None:
    perm = _IsListableAware(allow_call=False, allow_list=True)
    assert is_binding_listable(_tool(permissions=(perm,)), _request(), _token()) is True


def test_permission_with_is_listable_returning_false_hides_binding() -> None:
    perm = _IsListableAware(allow_call=True, allow_list=False)
    assert is_binding_listable(_tool(permissions=(perm,)), _request(), _token()) is False


def test_selector_tool_binding_uses_same_shape() -> None:
    binding = SelectorToolBinding(
        name="s",
        description=None,
        spec=SelectorSpec(selector=_sel),
        permissions=(_DenyAll(),),
    )
    assert is_binding_listable(binding, _request(), _token()) is False


def test_resource_binding_uses_same_shape() -> None:
    binding = ResourceBinding(
        name="r",
        uri_template="r://",
        description=None,
        selector=_sel,
        permissions=(_DenyAll(),),
    )
    assert is_binding_listable(binding, _request(), _token()) is False
