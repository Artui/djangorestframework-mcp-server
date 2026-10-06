"""``DRFPermissionAdapter.bind_view_kwargs``: the route a call names, on the adapter's view."""

from __future__ import annotations

from typing import Any

from django.http import HttpRequest
from rest_framework.permissions import BasePermission

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.auth.types.token_info import TokenInfo


def _recording(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A permission granting everything and recording each ``view.kwargs`` it is shown."""

    class _Recording(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return True

    return _Recording


def _check(perm: Any) -> bool:
    return perm.has_permission(HttpRequest(), TokenInfo(user=None))


def test_an_unbound_adapter_judges_an_empty_route() -> None:
    seen: list[dict[str, Any]] = []
    _check(DRFPermissionAdapter(_recording(seen)))

    assert seen == [{}]


def test_binding_hands_the_route_to_every_adapter_and_passes_the_rest_through() -> None:
    seen: list[dict[str, Any]] = []
    first, second = DRFPermissionAdapter(_recording(seen)), DRFPermissionAdapter(_recording(seen))
    scoped = ScopeRequired(["read"])

    bound = DRFPermissionAdapter.bind_view_kwargs((first, scoped, second), {"project_pk": 7})

    # An ``MCPPermission`` judges the request and the token, so it has no view to
    # bind and is returned as it is, in its place.
    assert bound[1] is scoped
    assert len(bound) == 3
    _check(bound[0])
    _check(bound[2])
    assert seen == [{"project_pk": 7}, {"project_pk": 7}]


def test_binding_leaves_the_registered_adapter_unbound() -> None:
    # The registered adapter is shared by every call to the tool, so a route
    # written onto it would be judged on the next caller's call.
    seen: list[dict[str, Any]] = []
    registered = DRFPermissionAdapter(_recording(seen))

    (bound,) = DRFPermissionAdapter.bind_view_kwargs((registered,), {"project_pk": 7})
    _check(registered)

    assert bound is not registered
    assert seen == [{}]


def test_binding_neither_instantiates_the_permission_again_nor_drops_a_subclass_state() -> None:
    made: list[int] = []

    class _Counted(BasePermission):
        def __init__(self) -> None:
            made.append(1)

    class _LabelledAdapter(DRFPermissionAdapter):
        def __init__(self, permission_class: type[BasePermission], *, label: str) -> None:
            super().__init__(permission_class)
            self.label: str = label

    registered = _LabelledAdapter(_Counted, label="projects")

    (bound,) = DRFPermissionAdapter.bind_view_kwargs((registered,), {"project_pk": 7})

    assert isinstance(bound, _LabelledAdapter)
    assert bound.label == "projects"
    assert made == [1]


def test_a_permission_writing_view_kwargs_does_not_reach_the_next_check() -> None:
    seen: list[dict[str, Any]] = []

    class _Writes(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            view.kwargs["written"] = True
            return True

    (bound,) = DRFPermissionAdapter.bind_view_kwargs(
        (DRFPermissionAdapter(_Writes),), {"project_pk": 7}
    )
    _check(bound)
    _check(bound)

    assert seen == [{"project_pk": 7}, {"project_pk": 7}]
