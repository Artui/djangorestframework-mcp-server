from __future__ import annotations

from types import SimpleNamespace

from django.contrib.auth.models import AnonymousUser

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.transport.utils import principal_for_token


def test_user_with_pk_maps_to_user_principal() -> None:
    token = TokenInfo(user=SimpleNamespace(pk=42))
    assert principal_for_token(token) == "user:42"


def test_anonymous_user_maps_to_shared_anonymous_principal() -> None:
    token = TokenInfo(user=AnonymousUser())
    assert principal_for_token(token) == "anonymous"


def test_none_user_maps_to_anonymous() -> None:
    assert principal_for_token(TokenInfo(user=None)) == "anonymous"
