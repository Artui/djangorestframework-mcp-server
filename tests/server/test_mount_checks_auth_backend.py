"""Mounting is where an auth backend becomes load-bearing, so it is checked there.

The backend's ``oauth2_provider`` import is lazy on purpose and the smoke job
pins that a bare server *constructs* without DOT -- an in-process server never
authenticates anything, so requiring the extra to build one would break a
supported mode. What is not supported is mounting a transport whose backend
cannot run, and that used to be discovered by a 500 on the first request.
"""

from __future__ import annotations

import sys

import pytest
from django.core.exceptions import ImproperlyConfigured

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.server.mcp_server import MCPServer

_DOT_MODULES = ("oauth2_provider", "oauth2_provider.oauth2_validators")


def _hide_dot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``import oauth2_provider...`` raise, as it does without the extra.

    ``sys.modules[name] = None`` is the documented way to make an import fail
    for an installed package: the submodule has to be hidden as well, or the
    cached entry satisfies ``from oauth2_provider.oauth2_validators import X``
    without the parent being consulted.
    """
    for name in _DOT_MODULES:
        monkeypatch.setitem(sys.modules, name, None)


def test_constructing_without_dot_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant the smoke job pins, asserted here too so it cannot drift."""
    _hide_dot(monkeypatch)

    server = MCPServer(name="in-process")

    assert server.tools is not None


def test_mounting_without_dot_refuses_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MCPServer(name="mounted")
    _hide_dot(monkeypatch)

    with pytest.raises(ImproperlyConfigured, match="django-oauth-toolkit"):
        _ = server.urls


def test_async_mounting_without_dot_refuses_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    server = MCPServer(name="mounted-async")
    _hide_dot(monkeypatch)

    with pytest.raises(ImproperlyConfigured, match="django-oauth-toolkit"):
        _ = server.async_urls


def test_a_backend_needing_nothing_mounts_with_dot_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The check is the backend's own, so a backend with no extra is unaffected."""
    server = MCPServer(name="allow-any", auth_backend=AllowAnyBackend())
    _hide_dot(monkeypatch)

    assert server.urls is not None
