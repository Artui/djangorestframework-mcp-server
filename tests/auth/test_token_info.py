from __future__ import annotations

from rest_framework_mcp.auth.token_info import TokenInfo


def test_has_scope_present() -> None:
    t = TokenInfo(user=None, scopes=("read", "write"))
    assert t.has_scope("read")


def test_has_scope_absent() -> None:
    t = TokenInfo(user=None, scopes=("read",))
    assert not t.has_scope("write")


def test_has_all_scopes_true() -> None:
    t = TokenInfo(user=None, scopes=("a", "b", "c"))
    assert t.has_all_scopes(["a", "b"])


def test_has_all_scopes_false() -> None:
    t = TokenInfo(user=None, scopes=("a",))
    assert not t.has_all_scopes(["a", "b"])
