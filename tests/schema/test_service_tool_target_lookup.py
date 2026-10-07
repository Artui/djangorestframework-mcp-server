"""A service tool advertises the lookup its target selector reads.

drf-services' dispatch hands a service spec's ``params`` to the one target
lookup it declares (its ``collection_selector_spec`` or its
``instance_selector_spec``) as well as to the input serializer, and its
unknown-argument check admits whatever that lookup declares
(``declared_input_keys``). So a call carrying the lookup is served, and the
``inputSchema`` has to say the lookup exists, or a client that validates its
arguments against the advertised schema can never send the one call that
works. A spec declaring a lookup dispatch would not call, beside the other one
or beside ``many=True``, is refused by drf-services when it is built.

A lookup is reflected the way a selector tool's own parameters are, told what
this server fills (drf-services' ``supplied=``): a name the pool fills is not
advertised, and every other lookup parameter without a default is required, so
``task_by_pk(*, pk)`` asks for ``pk`` rather than offering it.

Kept beside ``test_service_tool_schema.py`` rather than in it because these
tests drive the whole path, from registration through ``tools/list`` to
``tools/call``, and that file builds bindings directly.
"""

from __future__ import annotations

from typing import Annotated, Any

import jsonschema
import pytest
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    InputRequired,
    NotClientInput,
    UnknownArguments,
)
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


def _invoices_matching(**kwargs: Any) -> Any:
    # A bare ``**kwargs`` leaves the key set this lookup declares open.
    return Invoice.objects.filter(**kwargs)


def _rename(*, instance: Invoice, data: dict[str, Any]) -> Invoice:
    instance.number = data["number"]
    instance.save(update_fields=["number"])
    return instance


def _rename_all(*, collection: Any, data: dict[str, Any]) -> int:
    # The collection lookup seeds ``collection``, never ``instance``.
    return collection.update(number=data["number"])


def _rename_each(*, data: list[dict[str, Any]]) -> None:
    # ``many=True`` resolves no target, so the service takes only the list.
    ...


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


def _listed_input_schema(spec: ServiceSpec) -> dict[str, Any]:
    # Through ``tools/list``, because ``additionalProperties`` is stamped there
    # rather than by the schema builder.
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="rename_invoice", spec=spec, unknown_arguments=UnknownArguments.REJECT
    )
    tools = server.list_tools(user=None)["tools"]
    return next(t for t in tools if t["name"] == "rename_invoice")["inputSchema"]


def test_the_instance_selectors_lookup_is_advertised() -> None:
    schema = build_service_tool_input_schema(_binding(_rename_spec()))

    assert set(schema["properties"]) == {"number", "pk"}
    assert schema["properties"]["pk"] == {"type": "integer"}


def test_the_collection_selectors_lookup_is_advertised_without_its_seed() -> None:
    spec = _rename_spec(
        service=_rename_all,
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
        service=_rename_all,
        instance_selector_spec=None,
        collection_selector_spec=SelectorSpec(kind=SelectorKind.LIST, selector=_invoices_by_ids),
    )
    admitted = declared_input_keys(spec, serializer=None)
    assert admitted is not None

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) - {"number"} == admitted - RESERVED_POOL_SEEDS


def test_a_list_payload_item_advertises_only_the_keys_the_bind_admits() -> None:
    # ``many=True`` dispatch resolves no target, and declares no lookup, so
    # drf-services admits only the input serializer's keys inside an item, and
    # the item's schema offers only those.
    spec = _rename_spec(
        service=_rename_each, many=True, instance_selector_spec=None, output_selector_spec=None
    )
    admitted = declared_input_keys(spec, serializer=_RenameInput())
    assert admitted is not None

    schema = build_service_tool_input_schema(_binding(spec))
    item = schema["properties"][spec.many_argument]["items"]

    assert set(schema["properties"]) == {spec.many_argument}
    assert set(item["properties"]) == admitted - RESERVED_POOL_SEEDS == {"number"}


def test_an_open_target_lookup_leaves_the_schema_open() -> None:
    # The lookup dispatch calls opens the set it reads, so nothing closed may
    # be advertised.
    spec = _rename_spec(
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoices_matching
        ),
    )

    assert _listed_input_schema(spec)["additionalProperties"] is True


def _invoice_in_tenant(*, pk: int, tenant: int) -> Any:
    # Names ``tenant`` plainly and with no default: the lookup alone would
    # advertise it and require it.
    return Invoice.objects.filter(pk=pk)


def _same_tenant(*, tenant: Annotated[int, NotClientInput] = 1) -> None:
    # The precondition owns ``tenant`` for the whole call.
    return None


def test_a_lookup_key_a_precondition_hides_is_not_advertised_but_a_field_of_that_name_is() -> None:
    # drf-services drops the caller's ``tenant`` before the lookup reads it and
    # ``REJECT`` refuses it, because a precondition marks it ``NotClientInput``
    # (``server_owned_keys``), so the schema does not ask for it. A serializer
    # field of the same name is the caller's input, validated into ``data``, and
    # stays advertised with the field's own schema.
    class _WithTenant(_RenameInput):
        tenant = serializers.IntegerField(help_text="The tenant to move the invoice to.")

    lookup = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_invoice_in_tenant)
    hidden = _rename_spec(instance_selector_spec=lookup, preconditions=[_same_tenant])
    kept = _rename_spec(
        instance_selector_spec=lookup, preconditions=[_same_tenant], input_serializer=_WithTenant
    )

    hidden_schema = build_service_tool_input_schema(_binding(hidden))
    kept_schema = build_service_tool_input_schema(_binding(kept))

    assert set(hidden_schema["properties"]) == {"number", "pk"}
    assert "tenant" not in hidden_schema["required"]
    assert declared_input_keys(hidden, serializer=_RenameInput()) == {"number", "pk"}
    assert set(kept_schema["properties"]) == {"number", "pk", "tenant"}
    assert "tenant" in kept_schema["required"]
    assert kept_schema["properties"]["tenant"]["description"] == (
        "The tenant to move the invoice to."
    )


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


def _invoice_by_marked_pk(*, pk: Annotated[int, InputRequired]) -> Any:
    return Invoice.objects.filter(pk=pk)


def test_a_lookup_without_a_default_is_required() -> None:
    # Nothing this server fills supplies ``pk``, so the lookup cannot run without
    # it, and the schema says so rather than leaving it to a marker.
    schema = build_service_tool_input_schema(_binding(_rename_spec()))

    assert schema["required"] == ["pk", "number"]


def test_a_lookup_its_selector_marks_input_required_is_required() -> None:
    # The marker still states it outright; a missing default now says the same.
    spec = _rename_spec(
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_by_marked_pk
        )
    )

    schema = build_service_tool_input_schema(_binding(spec))

    assert schema["required"] == ["pk", "number"]


def test_a_lookup_required_by_both_the_selector_and_the_serializer_is_required_once() -> None:
    # Both sides contribute ``pk`` to ``required``. JSON Schema requires the
    # entries of ``required`` to be unique, so a duplicate makes the whole
    # ``inputSchema`` invalid as a schema, not merely untidy.
    class _WithPk(_RenameInput):
        pk = serializers.IntegerField()

    spec = _rename_spec(
        input_serializer=_WithPk,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_by_marked_pk
        ),
    )

    schema = build_service_tool_input_schema(_binding(spec))

    assert sorted(schema["required"]) == ["number", "pk"]
    jsonschema.Draft202012Validator.check_schema(schema)


def _invoice_by_optional_pk(*, pk: int | None = None) -> Any:
    return Invoice.objects.filter(pk=pk)


def test_a_lookup_with_nothing_required_adds_no_required_list() -> None:
    # No input serializer, and ``pk`` has a default.
    spec = _rename_spec(
        input_serializer=None,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_by_optional_pk
        ),
    )

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) == {"pk"}
    assert "required" not in schema


def test_a_serializer_field_keeps_its_property_and_the_lookup_its_requiredness() -> None:
    # One argument feeds both, so the serializer's declaration describes the
    # value and the lookup, which cannot run without it, makes it required even
    # though the serializer would accept the call without it.
    class _WithOptionalPk(_RenameInput):
        pk = serializers.IntegerField(required=False, help_text="The invoice to rename.")

    schema = build_service_tool_input_schema(
        _binding(_rename_spec(input_serializer=_WithOptionalPk))
    )

    assert schema["properties"]["pk"]["description"] == "The invoice to rename."
    assert "pk" in schema["required"]


def _invoice_by_pk_for_tenant(*, pk: int, tenant: str) -> Any:
    return Invoice.objects.filter(pk=pk, number__startswith=tenant)


def test_a_lookup_parameter_the_server_seeds_is_not_advertised() -> None:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "INV"),
    )
    server.register_service_tool(
        name="rename_invoice",
        spec=_rename_spec(
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=_invoice_by_pk_for_tenant
            )
        ),
    )

    tool = next(t for t in server.list_tools(user=None)["tools"] if t["name"] == "rename_invoice")

    assert set(tool["inputSchema"]["properties"]) == {"pk", "number"}
    assert tool["inputSchema"]["required"] == ["pk", "number"]


def test_a_lookup_a_defaulted_url_kwarg_fills_is_not_required() -> None:
    url_kwarg = UrlKwarg("pk", type="integer", default=1)

    schema = build_service_tool_input_schema(_binding(_rename_spec(), url_kwargs=(url_kwarg,)))

    assert schema["properties"]["pk"] == url_kwarg.json_schema()
    assert schema["required"] == ["number"]


def test_a_spec_without_a_target_selector_advertises_only_its_input() -> None:
    spec = _rename_spec(instance_selector_spec=None)

    schema = build_service_tool_input_schema(_binding(spec))

    assert set(schema["properties"]) == {"number"}


def _invoice_for_user(*, user: Any) -> Any:
    return Invoice.objects.all()


def test_a_lookup_that_asks_nothing_leaves_the_schema_as_the_serializer_built_it() -> None:
    # Its only parameter is a seed, so there is nothing to merge, and the
    # serializer-less ``{"type": "object"}`` is served without an empty
    # ``properties`` grafted onto it.
    spec = _rename_spec(
        input_serializer=None,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_invoice_for_user),
    )

    assert build_service_tool_input_schema(_binding(spec)) == {"type": "object"}


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
