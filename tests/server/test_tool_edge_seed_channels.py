"""A tool's ``UrlKwarg`` or ``QueryParam`` named after a seed the DRF edge owns is refused.

drf-services' DRF edge seeds ``request`` and ``view`` on every call and its
validator binds ``serializer``, and since drf-services 0.58.0 none of the three
is in the kernel's ``RESERVED_POOL_SEEDS``, which ``validate_channel_names``
always reserves. A channel of one of those names registered, was listed to the
model, and then never reached what read it: the edge's value outranks it. A
spec's own ``reads`` of those names is refused when drf-services compiles the
spec, so this is the same rule for the channels a mount declares itself.

Every name, both channels and both tool kinds is a case: the reserved set is
one union, so a name dropped from it shows only as a missing case.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind
from rest_framework.permissions import AllowAny
from rest_framework_services import ArgumentBinding, SelectorSpec, ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_EDGE_NAMES = ["request", "view", "serializer"]


def _service(**_: Any) -> dict[str, str]:
    return {"ok": "true"}


def _rows(*, project: str = "", **_: Any) -> list[dict[str, str]]:
    return []


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _register(server: MCPServer, kind: str, **channels: Any) -> None:
    if kind == "service":
        server.register_service_tool(
            name="op",
            spec=ServiceSpec(service=_service, atomic=False, permission_classes=[AllowAny]),
            argument_binding=ArgumentBinding.BUNDLE,
            **channels,
        )
    else:
        server.register_selector_tool(
            name="op",
            spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_rows, permission_classes=[AllowAny]
            ),
            **channels,
        )


@pytest.mark.parametrize("kind", ["service", "selector"])
@pytest.mark.parametrize("name", _EDGE_NAMES)
def test_a_url_kwarg_named_after_an_edge_seed_is_refused(kind: str, name: str) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured) as caught:
        _register(server, kind, url_kwargs=(UrlKwarg(name),))

    message = str(caught.value)
    # The tool, the channel and the name, so the mount knows what to rename.
    assert f"tool 'op': url_kwargs name(s) ['{name}'] collide" in message
    assert server.tools.get("op") is None


@pytest.mark.parametrize("kind", ["service", "selector"])
@pytest.mark.parametrize("name", _EDGE_NAMES)
def test_a_query_param_named_after_an_edge_seed_is_refused(kind: str, name: str) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured) as caught:
        _register(server, kind, query_params=(QueryParam(name),))

    message = str(caught.value)
    assert f"tool 'op': query_params name(s) ['{name}'] collide" in message
    assert server.tools.get("op") is None


@pytest.mark.parametrize("kind", ["service", "selector"])
def test_a_channel_named_otherwise_still_registers(kind: str) -> None:
    server = _server()

    _register(server, kind, url_kwargs=(UrlKwarg("project"),), query_params=(QueryParam("q"),))

    assert server.tools.get("op") is not None


@pytest.mark.parametrize("kind", ["service", "selector"])
@pytest.mark.parametrize("name", ["result", "queryset"])
def test_a_channel_named_after_a_newer_kernel_seed_is_refused(kind: str, name: str) -> None:
    # Not edge seeds: the kernel's ``RESERVED_POOL_SEEDS`` gained both, and the
    # channel check reserves every seed the server's pool carries.
    for channels in ({"url_kwargs": (UrlKwarg(name),)}, {"query_params": (QueryParam(name),)}):
        server = _server()
        with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\] collide"):
            _register(server, kind, **channels)
