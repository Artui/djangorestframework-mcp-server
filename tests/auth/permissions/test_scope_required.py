from __future__ import annotations

from django.http import HttpRequest

from rest_framework_mcp.auth.permissions.scope_required import ScopeRequired
from rest_framework_mcp.auth.token_info import TokenInfo


def test_scope_required_allows_when_all_present() -> None:
    perm = ScopeRequired(["a", "b"])
    token = TokenInfo(user=None, scopes=("a", "b", "c"))
    assert perm.has_permission(HttpRequest(), token)


def test_scope_required_denies_when_missing() -> None:
    perm = ScopeRequired(["a", "b"])
    token = TokenInfo(user=None, scopes=("a",))
    assert not perm.has_permission(HttpRequest(), token)


def test_scope_required_required_scopes_returns_copy() -> None:
    scopes = ["a"]
    perm = ScopeRequired(scopes)
    out = perm.required_scopes()
    assert out == ["a"]
    out.append("z")
    # Internal state is untouched.
    assert perm.required_scopes() == ["a"]
