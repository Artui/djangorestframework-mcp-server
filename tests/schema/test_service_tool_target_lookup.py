"""A service tool advertises the lookup its target selector reads.

drf-services' dispatch hands a service spec's ``params`` to its
``instance_selector_spec`` / ``collection_selector_spec`` as well as to the
input serializer, and its unknown-argument check admits whatever those
selectors declare (``declared_input_keys``). So a call carrying the lookup is
served, and the ``inputSchema`` has to say the lookup exists, or a client that
validates its arguments against the advertised schema can never send the one
call that works.

Kept beside ``test_service_tool_schema.py`` rather than in it because these
tests drive the whole path, from registration through ``tools/list`` to
``tools/call``, and that file builds bindings directly.
"""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import UnknownArguments
from rest_framework_services.dispatch.utils import declared_input_keys
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer


class _RenameInput(serializers.Serializer):
    number = serializers.CharField(max_length=32)


def _invoice_by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _invoices_by_ids(*, ids: list[int], user: Any) -> Any:
    # ``user`` is a transport seed: the reflection skips it, the bind exempts it.
    return Invoice.objects.filter(pk__in=ids)


def _rename(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
    instance.number = data["number"]
    instance.save(update_fields=["number"])
    return instance


def _rename_spec(**overrides: Any) -> ServiceSpec:
    declared: dict[str, Any] = {
        "service": _rename,
        "atomic": False,
        "permission_classes": [AllowAny],
        "input_serializer": _RenameInput,
        "instance_selector_spec": SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_invoice_by_pk),
        "output_selector_spec": SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
        ),
        **overrides,
    }
    return ServiceSpec(**declared)


def _binding(spec: ServiceSpec, **kwargs: Any) -> ToolBinding:
    return ToolBinding(name="rename", description=None, spec=spec, **kwargs)


def test_the_instance_selectors_lookup_is_advertised() -> None:
    schema = build_service_tool_input_schema(_binding(_rename_spec()))

    assert set(schema["properties"]) == {"number", "pk"}
    assert schema["properties"]["pk"] == {"type": "integer"}


def test_the_collection_selectors_lookup_is_advertised_without_its_seed() -> None:
    spec = _rename_spec(
        instance_selector_spec=None,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_invoices_by_ids),
        output_selector_spec=None,
    )

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) == {"number", "ids"}


def test_the_advertised_lookups_are_the_keys_the_bind_admits() -> None:
    # The agreement the fix rests on, asked of drf-services rather than
    # restated: every name its unknown-argument check admits beyond the
    # serializer and the reserved seeds is a property, and nothing else is.
    spec = _rename_spec(
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_invoices_by_ids),
    )
    admitted = declared_input_keys(spec, serializer=None)
    assert admitted is not None

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) - {"number"} == admitted - RESERVED_POOL_SEEDS


def test_an_input_field_of_the_same_name_wins_over_the_reflected_lookup() -> None:
    class _WithPk(_RenameInput):
        pk = serializers.IntegerField(help_text="The invoice to rename.")

    schema = build_service_tool_input_schema(_binding(_rename_spec(input_serializer=_WithPk)))

    assert schema["properties"]["pk"]["description"] == "The invoice to rename."
    assert "pk" in schema["required"]


def test_a_url_kwarg_of_the_same_name_wins_over_the_reflected_lookup() -> None:
    url_kwarg = UrlKwarg("pk", required=True, description="Route capture.")

    schema = build_service_tool_input_schema(_binding(_rename_spec(), url_kwargs=(url_kwarg,)))

    assert schema["properties"]["pk"] == url_kwarg.json_schema()
    assert "pk" in schema["required"]


def test_a_spec_without_a_target_selector_advertises_only_its_input() -> None:
    spec = _rename_spec(instance_selector_spec=None)

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) == {"number"}


@pytest.mark.django_db
def test_a_call_valid_against_the_advertised_schema_renames_the_target() -> None:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="rename_invoice",
        spec=_rename_spec(),
        unknown_arguments=UnknownArguments.REJECT,
    )
    invoice = Invoice.objects.create(number="INV-1")
    tool = next(t for t in server.list_tools(user=None)["tools"] if t["name"] == "rename_invoice")
    arguments = {"pk": invoice.pk, "number": "INV-2"}

    # The closed schema the issue reported refused ``pk``; the call needs it.
    assert tool["inputSchema"]["additionalProperties"] is False
    jsonschema.validate(arguments, tool["inputSchema"])
    result = server.call_tool("rename_invoice", arguments, user=None).to_dict()

    assert not result.get("isError")
    assert result["structuredContent"]["number"] == "INV-2"
