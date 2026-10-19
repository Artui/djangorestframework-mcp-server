"""A spec drf-services refuses to compile is refused when its tool is registered.

drf-services compiles a spec to the kernel's on first read, and refuses some
declarations there (an output selector declaring ``preconditions``, a ``reads``
entry named after a seed). Registration did not compile, so such a spec
registered and the refusal arrived later, at the first read: on ``tools/list``,
whose operation-condition pass compiles every service tool's spec
(``operation_affordances``), it took the whole listing down for every tool on
the server, and on ``tools/call`` it failed each call. The registrars compile
the spec as their last step, after this package's own checks, so drf-services'
refusal is the startup error and this package's own wording still wins where
both would refuse.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import FieldMarking, SelectorKind
from rest_framework.permissions import AllowAny
from rest_framework_services import QueryParam, SelectorSpec, ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend

_NESTED_PRECONDITIONS = "output_selector_spec declares preconditions"


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _noop() -> None:
    return None


def _rows() -> list[Any]:
    return []


def _refused_service_spec() -> ServiceSpec:
    # A nested selector's preconditions never run, and drf-services refuses
    # them when it compiles the spec.
    return ServiceSpec(
        service=_noop,
        atomic=False,
        permission_classes=[AllowAny],
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST, selector=_rows, preconditions=[lambda **_: None]
        ),
    )


def _good_service_spec() -> ServiceSpec:
    return ServiceSpec(service=_noop, atomic=False, permission_classes=[AllowAny])


def test_a_service_tool_drf_services_cannot_compile_is_refused_at_registration() -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=_NESTED_PRECONDITIONS) as caught:
        server.register_service_tool(name="svc", spec=_refused_service_spec())

    # drf-services' message names the spec, never the tool, so the registrar
    # names it: a server registering many tools needs to find the one at fault.
    assert str(caught.value).startswith("Tool 'svc': ")
    assert server.tools.get("svc") is None


def test_the_listing_of_the_rest_of_the_server_survives_the_refusal() -> None:
    # The hazard the refusal closes: registered, the spec took ``tools/list``
    # down for every other tool too.
    server = _server()
    server.register_service_tool(name="good", spec=_good_service_spec())
    with pytest.raises(ImproperlyConfigured):
        server.register_service_tool(name="svc", spec=_refused_service_spec())

    listed: Any = server.list_tools(user=None)

    assert [tool["name"] for tool in listed["tools"]] == ["good"]


def test_a_selector_tool_drf_services_cannot_compile_is_refused_at_registration() -> None:
    # drf-services refuses ``field_audiences`` with no output serializer to
    # mark when it compiles. A read named after a seed is no longer the case
    # to hold this with: the registrar routes the spec's reads, so this
    # package's own channel check refuses that name first, in its own words.
    server = _server()
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        permission_classes=[AllowAny],
        field_audiences={"label": FieldMarking()},
    )

    with pytest.raises(
        ImproperlyConfigured, match=r"field_audiences and no output serializer"
    ) as caught:
        server.register_selector_tool(name="sel", spec=spec)

    assert str(caught.value).startswith("Tool 'sel': ")
    assert server.tools.get("sel") is None


def test_a_specs_read_named_after_a_seed_is_refused_in_this_packages_words() -> None:
    # The spec's own reads are channels this server routes, so they are held to
    # the reserved names a mount's ``query_params`` is, before drf-services
    # compiles the spec.
    server = _server()
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_rows,
        permission_classes=[AllowAny],
        reads=[QueryParam("user")],
    )

    with pytest.raises(ImproperlyConfigured, match=r"collide with reserved transport keys"):
        server.register_selector_tool(name="sel", spec=spec)

    assert server.tools.get("sel") is None


def test_a_chain_step_drf_services_cannot_compile_is_refused_at_registration() -> None:
    # Every chain step's spec is compiled by the same listing pass.
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=_NESTED_PRECONDITIONS) as caught:
        server.register_chain_tool(
            name="flow",
            steps=[
                ChainStep(alias="first", spec=_good_service_spec()),
                ChainStep(alias="second", spec=_refused_service_spec()),
            ],
        )

    assert str(caught.value).startswith("Tool 'flow': ")
    assert server.tools.get("flow") is None
