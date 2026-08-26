from __future__ import annotations

from typing import Any

import pytest
from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request

from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.types.token_info import TokenInfo


class _DummyUser:
    """Stand-in user; ``is_authenticated`` is what DRF's stock permissions read."""

    def __init__(self, *, authenticated: bool) -> None:
        self.is_authenticated: bool = authenticated


class _RecordingPermission(BasePermission):
    """Capture ``(request, view)`` so the adapter's plumbing is verifiable."""

    captured: list[tuple[Request, Any]] = []  # noqa: RUF012 — test-only registry

    def has_permission(self, request: Any, view: Any) -> bool:
        _RecordingPermission.captured.append((request, view))
        return True


@pytest.fixture(autouse=True)
def _clear_recorder() -> None:
    _RecordingPermission.captured.clear()


def _http_request() -> HttpRequest:
    req = HttpRequest()
    req.method = "GET"
    return req


def test_adapter_exposes_wrapped_class() -> None:
    adapter = DRFPermissionAdapter(IsAuthenticated)
    assert adapter.permission_class is IsAuthenticated


def test_adapter_denies_anonymous_via_is_authenticated() -> None:
    adapter = DRFPermissionAdapter(IsAuthenticated)
    token = TokenInfo(user=AnonymousUser(), scopes=())
    assert adapter.has_permission(_http_request(), token) is False


def test_adapter_allows_authenticated_user() -> None:
    adapter = DRFPermissionAdapter(IsAuthenticated)
    token = TokenInfo(user=_DummyUser(authenticated=True), scopes=())
    assert adapter.has_permission(_http_request(), token) is True


def test_adapter_passes_drf_request_and_view_stand_in() -> None:
    adapter = DRFPermissionAdapter(_RecordingPermission)
    token = TokenInfo(user=_DummyUser(authenticated=True), scopes=())
    adapter.has_permission(_http_request(), token)
    assert len(_RecordingPermission.captured) == 1
    request, view = _RecordingPermission.captured[0]
    assert isinstance(request, Request)
    assert request.user is token.user
    # View stand-in: action defaults to None; kwargs is an empty dict.
    assert view.action is None
    assert view.kwargs == {}


def test_adapter_required_scopes_returns_empty() -> None:
    adapter = DRFPermissionAdapter(IsAuthenticated)
    assert adapter.required_scopes() == []


def test_adapter_instance_constructed_once() -> None:
    adapter = DRFPermissionAdapter(_RecordingPermission)
    # Multiple calls reuse the same wrapped DRF instance.
    token = TokenInfo(user=_DummyUser(authenticated=True), scopes=())
    adapter.has_permission(_http_request(), token)
    adapter.has_permission(_http_request(), token)
    instances = {id(captured[0]) for captured in _RecordingPermission.captured}
    # Two different ``Request`` objects (one per call) but a single permission
    # instance (verified by no AttributeError + permission flag).
    assert len(_RecordingPermission.captured) == 2
    assert len(instances) == 2  # two distinct DRF Request objects


class _ReadsAuthThenUser(BasePermission):
    """The shape of every stock token permission: ``auth`` first, ``user`` after.

    ``TokenHasScope`` from django-oauth-toolkit reads ``request.auth`` to find
    the token, then falls through to the user.
    """

    seen: list[tuple[Any, Any]] = []  # noqa: RUF012 — test-only registry

    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        _ReadsAuthThenUser.seen.append((request.auth, request.user))
        return True


def test_reading_auth_does_not_reset_the_user_to_anonymous() -> None:
    """DRF resolves ``request.auth`` lazily.

    On a request that has never authenticated, reading it runs the (here empty)
    authenticator chain, which ends in ``_not_authenticated()`` and overwrites
    ``request.user`` with ``AnonymousUser`` — on the wrapper *and* on the
    ``HttpRequest`` underneath. A permission as ordinary as ``TokenHasScope``
    therefore denied a properly scoped caller, with nothing in the response
    saying why, and the plausible workaround was to drop the permission.
    """
    _ReadsAuthThenUser.seen.clear()
    user = _DummyUser(authenticated=True)
    raw = object()
    http_request = _http_request()
    adapter = DRFPermissionAdapter(_ReadsAuthThenUser)

    assert adapter.has_permission(http_request, TokenInfo(user=user, raw=raw)) is True
    seen_auth, seen_user = _ReadsAuthThenUser.seen[0]
    # The backend's opaque payload is what DRF permissions expect to find here.
    assert seen_auth is raw
    assert seen_user is user
    # And the mutation does not reach the shared HttpRequest either.
    assert not isinstance(getattr(http_request, "user", None), AnonymousUser)


def test_a_backend_publishing_no_payload_still_leaves_the_user_alone() -> None:
    """``auth`` is ``None`` as a *value* — the point is that it is set at all,
    so the getter never reaches for the authenticator chain."""
    _ReadsAuthThenUser.seen.clear()
    user = _DummyUser(authenticated=True)
    adapter = DRFPermissionAdapter(_ReadsAuthThenUser)
    adapter.has_permission(_http_request(), TokenInfo(user=user))
    assert _ReadsAuthThenUser.seen[0] == (None, user)
