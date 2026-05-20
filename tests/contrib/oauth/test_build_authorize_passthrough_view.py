"""Phase 10e coverage: the DOT ``AuthorizationView`` adapter hook.

We don't drive a full OAuth flow here — that's DOT's responsibility and
already covered by its own suite. The test only verifies the hook
contract: when an adapter is configured, ``request.user`` is set before
``super().dispatch()`` runs; when no adapter is configured, the view is
transparent.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory

from rest_framework_mcp.contrib.oauth.build_authorize_passthrough_view import (
    build_authorize_passthrough_view,
)


class _StubAdapter:
    """Records the request it was asked to hydrate, returns a sentinel user."""

    def __init__(self, *, user: AbstractBaseUser | None) -> None:
        self.user: AbstractBaseUser | None = user
        self.calls: list[HttpRequest] = []

    def hydrate(self, request: HttpRequest) -> AbstractBaseUser | None:
        self.calls.append(request)
        return self.user


@pytest.fixture
def _mock_dot_dispatch():
    """Patch DOT's ``AuthorizationView.dispatch`` so we don't drive the real flow.

    Returns the mock so tests can inspect the ``request.user`` it saw.
    """
    from oauth2_provider.views import AuthorizationView

    captured: dict[str, Any] = {}

    def fake_dispatch(self: Any, request: HttpRequest, *args: Any, **kwargs: Any) -> HttpResponse:
        captured["user"] = request.user
        return HttpResponse("ok")

    with patch.object(AuthorizationView, "dispatch", fake_dispatch):
        yield captured


@pytest.mark.django_db
def test_adapter_hydrated_user_reaches_dispatch(_mock_dot_dispatch) -> None:
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username="alice", password="x")
    adapter = _StubAdapter(user=user)
    view = build_authorize_passthrough_view(adapter)

    request = RequestFactory().get("/oauth/authorize/")
    request.user = AnonymousUser()  # type: ignore[assignment]
    view(request)

    assert len(adapter.calls) == 1
    assert _mock_dot_dispatch["user"].pk == user.pk


def test_adapter_returning_none_leaves_request_user_alone(_mock_dot_dispatch) -> None:
    adapter = _StubAdapter(user=None)
    view = build_authorize_passthrough_view(adapter)

    request = RequestFactory().get("/oauth/authorize/")
    sentinel = AnonymousUser()
    request.user = sentinel  # type: ignore[assignment]
    view(request)

    assert _mock_dot_dispatch["user"] is sentinel


def test_no_adapter_skips_hydration_entirely(_mock_dot_dispatch) -> None:
    """Mounted without an adapter, the view is transparent over DOT."""
    view = build_authorize_passthrough_view(None)

    request = RequestFactory().get("/oauth/authorize/")
    sentinel = AnonymousUser()
    request.user = sentinel  # type: ignore[assignment]
    view(request)

    assert _mock_dot_dispatch["user"] is sentinel
