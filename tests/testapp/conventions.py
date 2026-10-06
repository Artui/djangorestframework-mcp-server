"""A server with somewhere for each ``AgentConventions`` line to land.

- ``invoices.list`` is a paged selector tool declaring a read-shaping
  ``QueryParam``, so its param carries the scope sentence, and a selection its
  serializer refuses while rendering carries it again in the ``isError`` text.
- ``invoices.rename`` is a service tool resolving its target through a lookup
  with no default, so a call leaving ``pk`` out earns the missing-argument
  message.
- Both render ``_Row``, whose ``id`` is a handle with no wording of its own, so
  each tool's ``outputSchema`` carries the handle description and its
  ``description`` the handle line.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services import MARKING, FieldMarking
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import AgentConventions, MCPServer, QueryParam
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.testapp.models import Invoice

REFUSED_SELECTION = "{items{id}}"


class _Row(serializers.ModelSerializer):
    """An invoice that refuses any ``fields`` selection while it renders."""

    class Meta:
        model = Invoice
        fields = ["id", "number"]
        extra_kwargs = {"id": {"style": {MARKING: FieldMarking.handle()}}}

    def to_representation(self, instance: Any) -> Any:
        if self.context["request"].query_params.get("fields"):
            raise serializers.ValidationError("`items` field is not found", code="not_found")
        return super().to_representation(instance)


class _Rename(serializers.Serializer):
    number = serializers.CharField()


def _invoices() -> Any:
    return Invoice.objects.order_by("pk")


def _by_pk(*, pk: int) -> Invoice:
    return Invoice.objects.get(pk=pk)


def _rename(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
    instance.number = data["number"]
    instance.save()
    return instance


def conventions_server(
    conventions: AgentConventions | None = None, **server_kwargs: Any
) -> MCPServer:
    """A fresh server speaking ``conventions``, or the defaults when ``None``.

    ``server_kwargs`` reach ``MCPServer`` as given: a task store and executor
    for a test that runs a call as a task.
    """
    server = MCPServer(
        name="conventions",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        conventions=conventions,
        **server_kwargs,
    )
    server.register_selector_tool(
        name="invoices.list",
        description="List invoices.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_invoices, output_serializer=_Row),
        paginate=True,
        query_params=(QueryParam("fields", description="Fields to return."),),
    )
    server.register_service_tool(
        name="invoices.rename",
        description="Rename an invoice.",
        spec=ServiceSpec(
            service=_rename,
            atomic=False,
            input_serializer=_Rename,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_by_pk),
            output_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=_Row),
        ),
    )
    return server


__all__ = ["REFUSED_SELECTION", "conventions_server"]
