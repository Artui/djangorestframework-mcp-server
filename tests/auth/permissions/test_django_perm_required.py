from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.permissions.django_perm_required import DjangoPermRequired
from rest_framework_mcp.auth.token_info import TokenInfo


class _AnonUser:
    is_authenticated = False

    def has_perm(self, _: str) -> bool:
        return False


class _AuthedUser:
    is_authenticated = True

    def __init__(self, perms: list[str]) -> None:
        self._perms = perms

    def has_perm(self, perm: str) -> bool:
        return perm in self._perms


def _token(user: Any) -> TokenInfo:
    return TokenInfo(user=user)


def test_denies_unauthenticated_user() -> None:
    perm = DjangoPermRequired("invoices.add")
    assert not perm.has_permission(HttpRequest(), _token(_AnonUser()))


def test_allows_when_user_has_single_perm() -> None:
    perm = DjangoPermRequired("invoices.add")
    assert perm.has_permission(HttpRequest(), _token(_AuthedUser(["invoices.add"])))


def test_allows_when_user_has_all_listed_perms() -> None:
    perm = DjangoPermRequired(["invoices.add", "invoices.change"])
    assert perm.has_permission(
        HttpRequest(), _token(_AuthedUser(["invoices.add", "invoices.change"]))
    )


def test_denies_when_missing_one_perm() -> None:
    perm = DjangoPermRequired(["invoices.add", "invoices.change"])
    assert not perm.has_permission(HttpRequest(), _token(_AuthedUser(["invoices.add"])))


def test_required_scopes_is_empty() -> None:
    assert DjangoPermRequired("x").required_scopes() == []
