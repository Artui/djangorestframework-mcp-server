"""Wire-level coverage for the 0.8.0 dispatch "gains".

Routing service tools through drf-services' ``dispatch_spec`` gained two
capabilities the pre-0.8 pool-based path lacked — collection (bulk) mutations
via ``collection_selector_spec`` and object-level permission enforcement via the
``on_target_resolved=enforce_permissions`` hook. Both were shipped untested;
these exercise them over ``tools/call``.

(A ``many=True`` *list-payload* bulk create is HTTP-only: MCP ``arguments`` is
always a JSON object, so the wire never carries a bare list. The bulk gain that
*is* reachable over MCP is ``collection_selector_spec`` — a filtered-set
mutation driven by object-shaped arguments.)
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice


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


class _NumberInput(drf_serializers.Serializer):
    number = drf_serializers.CharField()


class _DenyObject(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:  # noqa: ARG002
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:  # noqa: ARG002
        return False


class _UnsentFilterSet:
    """Duck-typed ``(data, queryset) -> .qs`` filter_set (no django-filter dep)."""

    def __init__(self, *, data: Any, queryset: Any) -> None:
        self._data = data
        self._queryset = queryset

    @property
    def qs(self) -> Any:
        raw = self._data.get("sent")
        if raw is None:
            return self._queryset
        keep = str(raw).lower() in ("1", "true", "yes")
        return self._queryset.filter(sent=keep)


def _all_invoices() -> Any:
    return Invoice.objects.all()


def _invoice_by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


@pytest.mark.django_db
def test_collection_selector_spec_bulk_mutation_over_the_wire() -> None:
    Invoice.objects.create(number="A", amount_cents=1, sent=False)
    Invoice.objects.create(number="B", amount_cents=1, sent=False)
    Invoice.objects.create(number="C", amount_cents=1, sent=True)

    def _bulk_delete(*, collection: Any) -> dict[str, int]:
        n, _ = collection.delete()
        return {"deleted": n}

    server = _server()
    server.register_service_tool(
        name="invoices.purge_unsent",
        spec=ServiceSpec(
            service=_bulk_delete,
            collection_selector_spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_all_invoices, filter_set=_UnsentFilterSet
            ),
            atomic=False,
        ),
    )
    out = handle_tools_call(
        {"name": "invoices.purge_unsent", "arguments": {"sent": "false"}}, _ctx(server)
    )
    assert isinstance(out, dict)
    assert out.get("isError") is not True
    assert out["structuredContent"] == {"deleted": 2}
    assert list(Invoice.objects.values_list("number", flat=True)) == ["C"]


@pytest.mark.django_db
def test_object_permission_denial_on_update_tool() -> None:
    invoice = Invoice.objects.create(number="A", amount_cents=1)

    def _rename(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
        instance.number = data["number"]
        instance.save(update_fields=["number"])
        return instance

    server = _server()
    server.register_service_tool(
        name="invoices.rename",
        spec=ServiceSpec(
            service=_rename,
            input_serializer=_NumberInput,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_invoice_by_pk
            ),
            permission_classes=[_DenyObject],
            atomic=False,
        ),
    )
    out = handle_tools_call(
        {"name": "invoices.rename", "arguments": {"pk": invoice.pk, "number": "B"}},
        _ctx(server),
    )
    # The object-level guard (on_target_resolved=enforce_permissions) fires on the
    # resolved instance now that drf-services >= 0.21 runs it on the mutation path.
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    invoice.refresh_from_db()
    assert invoice.number == "A"  # unchanged — denied before the service ran
