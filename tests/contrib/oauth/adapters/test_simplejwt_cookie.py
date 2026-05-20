"""Phase 10e coverage: ``SimpleJWTCookieAdapter`` resolution behaviour."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.http import HttpRequest

from rest_framework_mcp.contrib.oauth.adapters.simplejwt_cookie import SimpleJWTCookieAdapter


def _request_with_cookie(value: str, *, cookie_name: str = "access") -> HttpRequest:
    req = HttpRequest()
    req.COOKIES[cookie_name] = value
    return req


def test_no_cookie_returns_none() -> None:
    """Missing cookie → ``None`` (DOT falls back to session auth)."""
    assert SimpleJWTCookieAdapter().hydrate(HttpRequest()) is None


def test_empty_cookie_returns_none() -> None:
    assert SimpleJWTCookieAdapter().hydrate(_request_with_cookie("")) is None


def test_malformed_token_returns_none() -> None:
    """Garbage cookie → silent ``None``, not a raised exception."""
    assert SimpleJWTCookieAdapter().hydrate(_request_with_cookie("not-a-jwt")) is None


@pytest.mark.django_db
def test_valid_token_resolves_user_by_pk() -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    user = get_user_model().objects.create_user(username="alice", password="x")
    token = AccessToken.for_user(user)
    resolved = SimpleJWTCookieAdapter().hydrate(_request_with_cookie(str(token)))
    assert resolved is not None
    assert resolved.pk == user.pk


@pytest.mark.django_db
def test_token_for_unknown_user_returns_none() -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    user = get_user_model().objects.create_user(username="bob", password="x")
    token = AccessToken.for_user(user)
    user.delete()
    assert SimpleJWTCookieAdapter().hydrate(_request_with_cookie(str(token))) is None


@pytest.mark.django_db
def test_custom_cookie_name_via_setting(settings) -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    settings.REST_FRAMEWORK_MCP = {"SIMPLEJWT_ACCESS_COOKIE": "jwt"}
    user = get_user_model().objects.create_user(username="carol", password="x")
    token = AccessToken.for_user(user)
    resolved = SimpleJWTCookieAdapter().hydrate(_request_with_cookie(str(token), cookie_name="jwt"))
    assert resolved is not None
    assert resolved.pk == user.pk


@pytest.mark.django_db
def test_token_without_user_id_claim_returns_none() -> None:
    from rest_framework_simplejwt.tokens import AccessToken

    token = AccessToken()
    # Default ``AccessToken`` carries no ``user_id`` until ``for_user`` populates it.
    assert SimpleJWTCookieAdapter().hydrate(_request_with_cookie(str(token))) is None
