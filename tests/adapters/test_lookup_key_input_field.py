"""A service tool's ``input_serializer`` field named like a key its target lookup reads is refused.

drf-services withholds a name the target lookup reads from the input
serializer's data, so the row is the lookup's to choose and never the body's.
A writable field of that name is then missing on every call, and the call was
refused as ``{"pk": ["This field is required."]}``, with nothing written, under
every binding, whether the field was required or not. In 0.52.1 the same tool
served. Registration now refuses it, naming the tool, the field and the lookup,
on every registrar.

A read-only field and a ``HiddenField`` take nothing from the body, so each
registers and serves
(``test_a_field_of_the_lookup_s_key_taking_nothing_from_the_body_registers_and_serves``).
"""

from __future__ import annotations

from typing import Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import SelectorKind, SpecRegistry
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import ArgumentBinding, SelectorSpec, ServiceSpec, UrlKwarg

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.server.mcp_server import MCPServer
from tests.testapp.models import Invoice

_ROUTES = ["direct", "decorator", "register_specs"]
_BINDINGS = [ArgumentBinding.BUNDLE, ArgumentBinding.SPREAD_AUTHOR_WINS]
_MESSAGE = (
    r"(?s)service tool 'rename'.*input_serializer declares field\(s\) \['pk'\]"
    r".*target lookup reads"
)


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _invoice_by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _rename(*, instance: Any, data: Any = None, **kwargs: Any) -> dict[str, Any]:
    return {"number": instance.number}


def _input(*, hidden: bool = False, **pk_kwargs: Any) -> type[serializers.Serializer]:
    pk = serializers.HiddenField(default=0) if hidden else serializers.IntegerField(**pk_kwargs)
    return type(
        "RenameInput", (serializers.Serializer,), {"pk": pk, "number": serializers.CharField()}
    )


def _register(
    server: MCPServer,
    input_serializer: type,
    *,
    route: str = "direct",
    argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE,
    lookup: str = "instance",
) -> None:
    target = SelectorSpec(
        kind=SelectorKind.RETRIEVE if lookup == "instance" else SelectorKind.LIST,
        selector=_invoice_by_pk,
    )
    spec = ServiceSpec(
        service=_rename,
        input_serializer=input_serializer,
        atomic=False,
        permission_classes=[AllowAny],
        **{f"{lookup}_selector_spec": target},
    )
    if route == "decorator":
        # The decorator takes a whole spec for anything beyond its own knobs.
        server.service_tool(
            name="rename", spec=spec, argument_binding=argument_binding, permissions=[]
        )(_rename)
        return
    knobs: dict[str, Any] = {"argument_binding": argument_binding, "permissions": []}
    if route == "direct":
        server.register_service_tool(name="rename", spec=spec, **knobs)
        return
    registry = SpecRegistry()
    registry.register("rename", spec)
    server.register_specs(registry, overrides={"rename": knobs})


@pytest.mark.parametrize("binding", _BINDINGS, ids=lambda member: member.name)
@pytest.mark.parametrize("route", _ROUTES)
@pytest.mark.parametrize("required", [True, False], ids=["required", "optional"])
def test_a_field_named_like_the_lookup_s_key_is_refused_at_registration(
    required: bool, route: str, binding: ArgumentBinding
) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=_MESSAGE):
        _register(server, _input(required=required), route=route, argument_binding=binding)

    assert server.tools.get("rename") is None


def test_a_collection_lookup_s_key_is_refused_too() -> None:
    with pytest.raises(ImproperlyConfigured, match=_MESSAGE):
        _register(_server(), _input(), lookup="collection")


@pytest.mark.django_db
@pytest.mark.parametrize("binding", _BINDINGS, ids=lambda member: member.name)
@pytest.mark.parametrize("hidden", [False, True], ids=["read-only", "hidden"])
def test_a_field_of_the_lookup_s_key_taking_nothing_from_the_body_registers_and_serves(
    hidden: bool, binding: ArgumentBinding
) -> None:
    invoice = Invoice.objects.create(number="INV-1")
    server = _server()
    _register(server, _input(hidden=hidden, read_only=True), argument_binding=binding)
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    out = handle_tools_call(
        {"name": "rename", "arguments": {"pk": invoice.pk, "number": "INV-2"}}, ctx
    )

    assert isinstance(out, dict) and "isError" not in out
    assert out["structuredContent"] == {"number": "INV-1"}


def _invoices(**kwargs: Any) -> Any:
    return Invoice.objects.all()


def test_a_lookup_read_its_selector_does_not_declare_is_left_to_the_kernel() -> None:
    # drf-services withholds a read only where the lookup's selector declares
    # it as a parameter, so a read reaching a catch-all is no lookup key here.
    # Its clash with a writable field is the kernel's, refused as the spec
    # compiles, in words naming the read. Holds the guard leaving the lookup's
    # ``reads`` out of the reflection.
    target = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_invoices, reads=[UrlKwarg("number", "string")]
    )
    spec = ServiceSpec(
        service=_rename,
        input_serializer=_input(read_only=True),
        atomic=False,
        permission_classes=[AllowAny],
        instance_selector_spec=target,
    )

    with pytest.raises(ImproperlyConfigured) as caught:
        _server().register_service_tool(name="rename", spec=spec, permissions=[])

    assert str(caught.value).startswith("Tool 'rename': ")
    assert "reads declares UrlKwarg('number')" in str(caught.value)
    assert "target lookup reads" not in str(caught.value)


class _NumberFilter(django_filters.FilterSet):
    number = django_filters.CharFilter()

    class Meta:
        model = Invoice
        fields: list[str] = []


@pytest.mark.django_db
def test_a_lookup_filter_named_like_a_field_is_left_to_the_kernel() -> None:
    # A filter is no parameter of the lookup's selector, so drf-services does
    # not withhold the field for it. The clash is the kernel's, as two sources
    # of one parameter, and it answers each call rather than registration.
    # Holds the guard leaving the lookup's ``filter_set`` out of the reflection.
    invoice = Invoice.objects.create(number="INV-1")
    target = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=_invoice_by_pk, filter_set=_NumberFilter
    )
    spec = ServiceSpec(
        service=_rename,
        input_serializer=_input(read_only=True),
        atomic=False,
        permission_classes=[AllowAny],
        instance_selector_spec=target,
    )
    server = _server()
    server.register_service_tool(name="rename", spec=spec, permissions=[])
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    with pytest.raises(ImproperlyConfigured, match=r"\['number'\] are declared by two sources"):
        handle_tools_call(
            {"name": "rename", "arguments": {"pk": invoice.pk, "number": "INV-2"}}, ctx
        )
