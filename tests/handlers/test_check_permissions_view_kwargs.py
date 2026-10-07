"""``check_permissions(..., shape=...)``: the route a call names, on each adapter's view."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework.permissions import BasePermission

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.permissions.utils import DispatchShape
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.utils import check_permissions


def _recording(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A permission granting everything and recording each ``view.kwargs`` it is shown."""

    class _Recording(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return True

    return _Recording


class _Gate:
    """An ``MCPPermission`` judging the request and token, recording the object judged."""

    def __init__(self, judged: list[Any]) -> None:
        self._judged = judged

    def has_permission(self, request: Any, token: Any) -> bool:
        self._judged.append(self)
        return False

    def required_scopes(self) -> list[str]:
        return ["read"]


def _check(
    permissions: tuple[Any, ...], view_kwargs: dict[str, Any] | None = None
) -> tuple[bool, list[str]]:
    """Judge ``permissions`` on a shape carrying ``view_kwargs`` alone, or on none."""
    shape = None if view_kwargs is None else DispatchShape(kwargs=view_kwargs)
    return check_permissions(permissions, HttpRequest(), TokenInfo(user=None), shape=shape)


def test_an_unbound_adapter_judges_an_empty_route() -> None:
    seen: list[dict[str, Any]] = []
    DRFPermissionAdapter(_recording(seen)).has_permission(HttpRequest(), TokenInfo(user=None))

    assert seen == [{}]


def test_without_a_shape_every_adapter_is_judged_on_an_empty_route() -> None:
    # The paths with no route to name (prompts, completion, chain steps) pass
    # none, and their adapters are judged as registered.
    seen: list[dict[str, Any]] = []

    assert _check((DRFPermissionAdapter(_recording(seen)),)) == (True, [])
    assert seen == [{}]


def test_a_shape_reaches_every_adapter_and_passes_the_rest_through() -> None:
    seen: list[dict[str, Any]] = []
    judged: list[Any] = []
    first, second = DRFPermissionAdapter(_recording(seen)), DRFPermissionAdapter(_recording(seen))
    gate = _Gate(judged)

    allowed, scopes = _check((first, gate, second), view_kwargs={"project_pk": 7})

    # An ``MCPPermission`` judges the request and the token, so it has no view to
    # bind and is judged as it is, its denial and its scopes intact.
    assert judged == [gate]
    assert judged[0] is gate
    assert (allowed, scopes) == (False, ["read"])
    assert seen == [{"project_pk": 7}, {"project_pk": 7}]


def test_the_registered_adapter_is_left_unbound() -> None:
    # The registered adapter is shared by every call to the tool, so a route
    # written onto it would be judged on the next caller's call.
    seen: list[dict[str, Any]] = []
    registered = DRFPermissionAdapter(_recording(seen))

    _check((registered,), view_kwargs={"project_pk": 7})
    _check((registered,))

    assert seen == [{"project_pk": 7}, {}]


def test_the_permission_is_not_instantiated_again_nor_a_subclass_state_dropped() -> None:
    made: list[int] = []
    labels: list[str] = []

    class _Counted(BasePermission):
        def __init__(self) -> None:
            made.append(1)

    class _LabelledAdapter(DRFPermissionAdapter):
        def __init__(self, permission_class: type[BasePermission], *, label: str) -> None:
            super().__init__(permission_class)
            self.label: str = label

        def has_permission(self, request: Any, token: Any) -> bool:
            labels.append(self.label)
            return super().has_permission(request, token)

    registered = _LabelledAdapter(_Counted, label="projects")

    assert _check((registered,), view_kwargs={"project_pk": 7}) == (True, [])
    assert labels == ["projects"]
    assert made == [1]


def test_a_permission_writing_view_kwargs_reaches_neither_the_next_check_nor_the_caller() -> None:
    seen: list[dict[str, Any]] = []

    class _Writes(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            view.kwargs["written"] = True
            return True

    adapter = DRFPermissionAdapter(_Writes)
    route: dict[str, Any] = {"project_pk": 7}
    _check((adapter, adapter), view_kwargs=route)
    _check((adapter,), view_kwargs=route)
    # And on a path naming no route, where every check is made with the
    # registered adapter itself.
    _check((adapter,))
    _check((adapter,))

    assert seen == [{"project_pk": 7}, {"project_pk": 7}, {"project_pk": 7}, {}, {}]
    assert route == {"project_pk": 7}
