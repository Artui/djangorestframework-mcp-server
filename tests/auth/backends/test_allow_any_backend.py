from __future__ import annotations

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend


def test_authenticate_returns_anonymous_token() -> None:
    backend = AllowAnyBackend()
    token = backend.authenticate(HttpRequest())
    assert token is not None
    assert isinstance(token.user, AnonymousUser)


def test_authenticate_uses_existing_request_user() -> None:
    backend = AllowAnyBackend()
    request = HttpRequest()
    sentinel = object()
    request.user = sentinel  # type: ignore[assignment]
    token = backend.authenticate(request)
    assert token is not None
    assert token.user is sentinel


def test_protected_resource_metadata_carries_warning() -> None:
    md = AllowAnyBackend().protected_resource_metadata()
    assert md["bearer_methods_supported"] == ["header"]
    assert "_warning" in md


def test_www_authenticate_challenge_default() -> None:
    assert AllowAnyBackend().www_authenticate_challenge() == 'Bearer realm="mcp"'


def test_www_authenticate_challenge_with_error_and_scope() -> None:
    out = AllowAnyBackend().www_authenticate_challenge(scopes=["a", "b"], error="invalid_token")
    assert 'error="invalid_token"' in out
    assert 'scope="a b"' in out
