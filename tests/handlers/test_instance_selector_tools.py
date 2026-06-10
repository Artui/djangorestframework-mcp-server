"""Sister-repo 0.16 adoption: ``instance_selector_spec`` / ``partial`` /
``serializer``-in-pool on MCP service-tool dispatch."""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import IntegerField, QuerySet, Value
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.server.mcp_server import MCPServer
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.utils import tool_error


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


def _invoice_by_pk(*, pk: int) -> QuerySet[Invoice]:
    return Invoice.objects.filter(pk=pk)


_INSTANCE_SPEC = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_invoice_by_pk)


class _RenameInput(drf_serializers.Serializer):
    pk = drf_serializers.IntegerField()
    number = drf_serializers.CharField()


def _rename_invoice(*, instance: Invoice, data: dict[str, Any]) -> dict[str, Any]:
    instance.number = data["number"]
    instance.save(update_fields=["number"])
    return {"number": instance.number}


def _register_rename(server: MCPServer, **spec_overrides: Any) -> None:
    spec = ServiceSpec(
        service=_rename_invoice,
        input_serializer=_RenameInput,
        instance_selector_spec=_INSTANCE_SPEC,
        atomic=False,
        **spec_overrides,
    )
    server.register_service_tool(
        name="invoices.rename", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )


@pytest.mark.django_db
def test_service_tool_receives_spec_resolved_instance() -> None:
    invoice = Invoice.objects.create(number="orig")
    server = _server()
    _register_rename(server)
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "new"}},
        _ctx(server),
    )
    assert isinstance(out, dict) and "isError" not in out
    invoice.refresh_from_db()
    assert invoice.number == "new"


@pytest.mark.django_db(transaction=True)
async def test_async_service_tool_receives_spec_resolved_instance() -> None:
    invoice = await Invoice.objects.acreate(number="orig")
    server = _server()
    _register_rename(server)
    out = await handle_tools_call_async(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "new"}},
        _ctx(server),
    )
    assert isinstance(out, dict) and "isError" not in out
    await invoice.arefresh_from_db()
    assert invoice.number == "new"


@pytest.mark.django_db
def test_missing_row_is_a_tool_level_not_found() -> None:
    server = _server()
    _register_rename(server)
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": 99999, "number": "new"}},
        _ctx(server),
    )
    error = tool_error(out)
    assert error["type"] == "not_found"


@pytest.mark.django_db(transaction=True)
async def test_async_missing_row_is_a_tool_level_not_found() -> None:
    server = _server()
    _register_rename(server)
    out = await handle_tools_call_async(
        {"name": "invoices.rename", "arguments": {"pk": 99999, "number": "new"}},
        _ctx(server),
    )
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db
def test_does_not_exist_from_instance_selector_is_not_found() -> None:
    def strict_lookup(*, pk: int) -> Invoice:
        return Invoice.objects.get(pk=pk)

    server = _server()
    spec = ServiceSpec(
        service=_rename_invoice,
        input_serializer=_RenameInput,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=strict_lookup),
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.rename", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": 99999, "number": "new"}},
        _ctx(server),
    )
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db
def test_non_queryset_instance_selector_return_is_used_directly() -> None:
    def direct_lookup(*, pk: int) -> Invoice | None:
        return Invoice.objects.filter(pk=pk).first()

    invoice = Invoice.objects.create(number="orig")
    server = _server()
    spec = ServiceSpec(
        service=_rename_invoice,
        input_serializer=_RenameInput,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=direct_lookup),
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.rename", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "new"}},
        _ctx(server),
    )
    assert "isError" not in out
    invoice.refresh_from_db()
    assert invoice.number == "new"


@pytest.mark.django_db
def test_queryset_shaping_applies_to_instance_lookup() -> None:
    seen: dict[str, Any] = {}

    def service(*, instance: Invoice) -> None:
        seen["marker"] = instance.marker

    invoice = Invoice.objects.create(number="x")
    server = _server()
    spec = ServiceSpec(
        service=service,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_invoice_by_pk,
            annotations={"marker": Value(42, output_field=IntegerField())},
        ),
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.touch", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )
    out = handle_tools_call(
        {"name": "invoices.touch", "arguments": {"pk": invoice.pk}}, _ctx(server)
    )
    assert "isError" not in out
    assert seen["marker"] == 42


@pytest.mark.django_db
def test_nested_spec_kwargs_provider_feeds_lookup_pool() -> None:
    def scoped_lookup(*, pk: int, only_sent: bool) -> QuerySet[Invoice]:
        qs = Invoice.objects.filter(pk=pk)
        return qs.filter(sent=True) if only_sent else qs

    unsent = Invoice.objects.create(number="x", sent=False)
    server = _server()
    spec = ServiceSpec(
        service=_rename_invoice,
        input_serializer=_RenameInput,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=scoped_lookup,
            kwargs=lambda view, request: {"only_sent": True},
        ),
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.rename", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": unsent.pk, "number": "new"}},
        _ctx(server),
    )
    # Provider scoped the lookup to sent invoices; the unsent row is invisible.
    assert tool_error(out)["type"] == "not_found"


@pytest.mark.django_db
def test_input_serializer_sees_resolved_instance() -> None:
    seen: dict[str, Any] = {}

    class _Recording(drf_serializers.Serializer):
        pk = drf_serializers.IntegerField()
        number = drf_serializers.CharField()

        def validate(self, attrs: dict[str, Any]) -> dict[str, Any]:
            seen["instance"] = self.instance
            return attrs

    invoice = Invoice.objects.create(number="orig")
    server = _server()
    spec = ServiceSpec(
        service=_rename_invoice,
        input_serializer=_Recording,
        instance_selector_spec=_INSTANCE_SPEC,
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.rename", spec=spec, argument_binding=ArgumentBinding.MERGE, permissions=[]
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "new"}},
        _ctx(server),
    )
    assert "isError" not in out
    assert seen["instance"] == invoice


@pytest.mark.django_db
def test_service_declaring_serializer_receives_bound_instance() -> None:
    captured: dict[str, Any] = {}

    class _CreateInput(drf_serializers.Serializer):
        number = drf_serializers.CharField()

        def create(self, validated_data: dict[str, Any]) -> Invoice:
            return Invoice.objects.create(**validated_data)

    def service(*, serializer: Any) -> dict[str, Any]:
        captured["serializer"] = serializer
        created = serializer.save()
        return {"pk": created.pk}

    server = _server()
    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(service=service, input_serializer=_CreateInput, atomic=False),
        permissions=[],
    )
    out = handle_tools_call(
        {"name": "invoices.create", "arguments": {"number": "n-1"}}, _ctx(server)
    )
    assert "isError" not in out
    assert isinstance(captured["serializer"], _CreateInput)
    assert Invoice.objects.filter(number="n-1").exists()


@pytest.mark.django_db
def test_reserved_seeds_cannot_be_poisoned_from_arguments() -> None:
    captured: dict[str, Any] = {}

    def service(*, instance: Invoice, serializer: Any, data: dict[str, Any]) -> None:
        captured["instance"] = instance
        captured["serializer"] = serializer

    invoice = Invoice.objects.create(number="x")
    server = _server()
    spec = ServiceSpec(
        service=service,
        input_serializer=_RenameInput,
        instance_selector_spec=_INSTANCE_SPEC,
        atomic=False,
    )
    server.register_service_tool(
        name="invoices.touch",
        spec=spec,
        argument_binding=ArgumentBinding.MERGE,
        permissions=[],
    )
    # ``instance`` / ``serializer`` keys in the client arguments are reserved
    # pool seeds: exempt from the unknown-key check, stripped from the spread.
    out = handle_tools_call(
        {
            "name": "invoices.touch",
            "arguments": {
                "pk": invoice.pk,
                "number": "y",
                "instance": "evil",
                "serializer": "evil",
            },
        },
        _ctx(server),
    )
    assert "isError" not in out
    assert captured["instance"] == invoice
    assert isinstance(captured["serializer"], _RenameInput)


@pytest.mark.django_db
def test_spec_partial_relaxes_required_fields() -> None:
    captured: dict[str, Any] = {}

    def service(*, data: dict[str, Any]) -> dict[str, Any]:
        captured["data"] = data
        return {}

    server = _server()
    server.register_service_tool(
        name="invoices.patch",
        spec=ServiceSpec(
            service=service, input_serializer=_RenameInput, partial=True, atomic=False
        ),
        permissions=[],
    )
    out = handle_tools_call({"name": "invoices.patch", "arguments": {}}, _ctx(server))
    assert "isError" not in out
    assert captured["data"] == {}


@pytest.mark.django_db
def test_default_validation_stays_non_partial() -> None:
    server = _server()

    def service(*, data: dict[str, Any]) -> None:
        return None

    server.register_service_tool(
        name="invoices.full",
        spec=ServiceSpec(service=service, input_serializer=_RenameInput, atomic=False),
        permissions=[],
    )
    out = handle_tools_call({"name": "invoices.full", "arguments": {}}, _ctx(server))
    from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError

    # Shape-level rejection stays a protocol error (-32602), not isError.
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
