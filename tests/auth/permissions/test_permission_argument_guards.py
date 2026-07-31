"""Guards on how permissions are constructed and registered.

Every case here is a configuration that used to be accepted and then failed
*silently* — a binding that reads as guarded at the registration site and
gates nothing at dispatch. None of them raised, and none of them showed up in
the unguarded-tool warning, which is what made them worth refusing at the point
they are written.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import DjangoPermRequired, MCPServer, ScopeRequired
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def _server() -> MCPServer:
    return MCPServer(name="g", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


# ----- a single scope as a bare string -----


def test_a_bare_string_scope_is_one_scope_not_nine() -> None:
    """The reported bug: ``list("mcp:admin")`` used to split into characters.

    Nothing failed at registration; it surfaced much later as a permission
    that could never be satisfied and a challenge reading
    ``scope="m c p : a d m i n"``.
    """
    assert ScopeRequired("mcp:admin").required_scopes() == ["mcp:admin"]


def test_the_list_form_is_unchanged() -> None:
    assert ScopeRequired(["a", "b"]).required_scopes() == ["a", "b"]


def test_the_two_permission_classes_now_agree_on_a_bare_string() -> None:
    """The asymmetry is what caused the bug — one sibling accepted a string,
    the other silently mangled it, and a developer who learned the first
    naturally wrote the same thing for the second."""
    assert ScopeRequired("x").required_scopes() == ["x"]
    assert DjangoPermRequired("app.perm")._perms == ["app.perm"]


def test_a_bare_string_scope_actually_gates() -> None:
    """Not just stored correctly — satisfiable by the token that should satisfy it."""
    permission = ScopeRequired("mcp:admin")
    assert permission.has_permission(None, TokenInfo(user=None, scopes=("mcp:admin",)))
    assert not permission.has_permission(None, TokenInfo(user=None, scopes=("other",)))


# ----- an empty permission is not a permission -----


@pytest.mark.parametrize("factory", [ScopeRequired, DjangoPermRequired])
def test_an_empty_requirement_is_refused(factory: Any) -> None:
    """``all(...)`` over nothing is ``True``, so an empty one permits everything
    while reading as a guard — and satisfies the unguarded-tool check that
    would otherwise have warned."""
    with pytest.raises(ImproperlyConfigured, match="at least one"):
        factory([])


# ----- permissions= must contain permissions -----


def test_a_string_passed_to_permissions_is_refused() -> None:
    """It would spread into one entry per character, none of them a permission.

    The tuple is non-empty, so the unguarded-tool check stays quiet; at
    dispatch every entry is unusable and the call is allowed. A tool that
    reads as guarded and gates nothing.
    """
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="one entry per character"):
        server.register_service_tool(
            name="t",
            description="x",
            spec=ServiceSpec(service=lambda **_: {}, atomic=False),
            permissions="ScopeRequired",  # type: ignore[arg-type]
        )


def test_an_entry_that_cannot_gate_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="cannot answer"):
        server.register_service_tool(
            name="t",
            description="x",
            spec=ServiceSpec(service=lambda **_: {}, atomic=False),
            permissions=[object()],
        )


def test_the_guard_covers_resources_and_prompts_too() -> None:
    """The same kwarg exists on every registration method, so does the check."""
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="cannot answer"):
        server.register_prompt(name="p", render=lambda: "hi", permissions=[object()])


def test_a_permission_implementing_only_the_gate_is_accepted() -> None:
    """``required_scopes`` is documented as having an implied ``[]`` default.

    An ``isinstance`` check against the runtime-checkable Protocol would
    reject this — it tests for *every* member — so the guard asks only for
    the method that actually gates.
    """

    class _GateOnly:
        def has_permission(self, request: Any, token: Any) -> bool:
            return False

    server = _server()
    binding = server.register_service_tool(
        name="t",
        description="x",
        spec=ServiceSpec(service=lambda **_: {}, atomic=False),
        permissions=[_GateOnly()],
    )
    assert len(binding.permissions) == 1


def test_a_gate_only_permission_denies_at_dispatch_as_well_as_in_listings() -> None:
    """⚠ The second bug this closes.

    ``is_binding_listable`` duck-types and honoured such a permission, while
    ``check_permissions`` skipped anything failing ``isinstance(...)`` against
    the runtime-checkable Protocol — which includes ``required_scopes``. The
    binding vanished from listings *and the call went through*.
    """
    from django.http import HttpRequest

    from rest_framework_mcp.handlers.utils import check_permissions

    class _GateOnly:
        def has_permission(self, request: Any, token: Any) -> bool:
            return False

    allowed, scopes = check_permissions((_GateOnly(),), HttpRequest(), TokenInfo(user=None))
    assert allowed is False
    assert scopes == []
