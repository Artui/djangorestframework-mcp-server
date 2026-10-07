"""A ``QueryParam`` may not take an input the tool offers the caller.

A ``QueryParam``'s value is popped from the arguments and routed to
``request.query_params``, so an input of the same name never receives what the
caller sent. A required target-lookup parameter answered every call
"Missing required argument(s)" for an argument the call carried, which a model
resends until it runs out of retries; a defaulted one resolved the row on its
default whatever the caller asked for. So registration refuses it, for service
tools as for selector tools, reading the names off the schema the tool
advertises, with what that schema does not offer as the caller's exempt.

Each refusal asserts which callable the message names, and that the remedies
come in order: a typed provider first, reading ``request.query_params`` in the
callable second, dropping the ``QueryParam`` last. Each exemption is called, so
what is exempt is decided by what arrives.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import NotClientInput, UnsetType
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from typing_extensions import TypedDict

from rest_framework_mcp import MCPServer, QueryParam
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.handlers.test_query_params import _ctx
from tests.testapp.models import Invoice

_SPREADING = [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]

_REMEDIES = (
    "Fill the parameter from request.query_params with a kwargs= provider whose "
    "TypedDict declares it",
    "read the value there in the callable and drop the input",
    "drop the QueryParam",
)


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _assert_refused(caught: pytest.ExceptionInfo[ImproperlyConfigured], taken_by: str) -> None:
    """``taken_by`` opens the sentence the refusal names the shadowed inputs in."""
    message = str(caught.value)
    assert f": {taken_by} that the tool also declares as a QueryParam." in message
    positions = [message.index(remedy) for remedy in _REMEDIES]
    assert positions == sorted(positions)


def _register_service(
    server: MCPServer, spec: ServiceSpec, *, query_params: tuple[str, ...], **kwargs: Any
) -> Any:
    return server.register_service_tool(
        name="act",
        description="Act.",
        spec=spec,
        query_params=tuple(QueryParam(name) for name in query_params),
        **kwargs,
    )


def _register_selector(
    server: MCPServer, spec: SelectorSpec, *, query_params: tuple[str, ...], **kwargs: Any
) -> Any:
    return server.register_selector_tool(
        name="read",
        description="Read.",
        spec=spec,
        query_params=tuple(QueryParam(name) for name in query_params),
        **kwargs,
    )


# ---------- what a service tool offers ----------


def _invoice_in_tenant(*, pk: str, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _invoice_in_tenant_defaulted(*, pk: str, tenant: str = "acme") -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


def _resolved(*, instance: Any) -> Any:
    return instance


@pytest.mark.parametrize(
    "lookup",
    [
        # Every call answered "Missing required argument(s): `tenant`."
        pytest.param(_invoice_in_tenant, id="required"),
        # Every call resolved the row in ``acme``, whatever tenant it named.
        pytest.param(_invoice_in_tenant_defaulted, id="defaulted"),
    ],
)
def test_a_lookup_parameter_a_query_param_takes_is_refused(lookup: Any) -> None:
    spec = ServiceSpec(
        service=_resolved,
        atomic=False,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lookup),
    )
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(_server(), spec, query_params=("tenant",))
    _assert_refused(caught, "the service's target lookup declares parameter(s) ['tenant']")


class _StatusIn(drf_serializers.Serializer):
    status = drf_serializers.CharField()


def _set_status(*, data: Any) -> Any:
    return data


@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, *_SPREADING])
def test_a_serializer_field_a_query_param_shadows_is_refused(binding: ArgumentBinding) -> None:
    # The service's serializer validates the arguments left once the split has
    # run, so a required field is refused for an argument the call carried. No
    # exemption reaches it: a service tool lays nothing back.
    spec = ServiceSpec(service=_set_status, atomic=False, input_serializer=_StatusIn)
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(_server(), spec, query_params=("status",), argument_binding=binding)
    _assert_refused(caught, "the service's input_serializer declares field(s) ['status']")


def _invoice_with_status(*, pk: str, status: str) -> dict[str, Any]:
    return {"pk": pk, "status": status}


def test_a_name_the_serializer_and_the_lookup_both_take_is_named_as_the_serializers() -> None:
    # The schema keeps the field's property for a name both declare, so the
    # refusal names the serializer's field and not the lookup's parameter.
    spec = ServiceSpec(
        service=_set_status,
        atomic=False,
        input_serializer=_StatusIn,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_with_status
        ),
    )
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(_server(), spec, query_params=("status",))
    _assert_refused(caught, "the service's input_serializer declares field(s) ['status']")
    assert "target lookup declares" not in str(caught.value)


def _set_flag(*, status: str) -> dict[str, Any]:
    return {"status": status}


@pytest.mark.parametrize("binding", _SPREADING)
def test_a_spread_service_parameter_a_query_param_takes_is_refused(
    binding: ArgumentBinding,
) -> None:
    # No serializer and a spreading binding: the service's own parameters are
    # its input, advertised and spread from the arguments.
    spec = ServiceSpec(service=_set_flag, atomic=False)
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(_server(), spec, query_params=("status",), argument_binding=binding)
    _assert_refused(caught, "the service declares parameter(s) ['status']")


class _NumberIn(drf_serializers.Serializer):
    number = drf_serializers.CharField()


def _numbers(*, data: Any) -> list[str]:
    return [item["number"] for item in data]


async def test_a_list_items_field_is_no_argument_a_query_param_takes() -> None:
    # A ``many=True`` spec takes its list under one argument, so an item's
    # field is never an argument of the call: the QueryParam of its name takes
    # nothing, and the items arrive whole. Holds narrowing the serializer's
    # fields to what the schema advertises.
    server = _server()
    spec = ServiceSpec(service=_numbers, atomic=False, many=True, input_serializer=_NumberIn)
    _register_service(server, spec, query_params=("number",))
    out = await server.acall_tool(
        "act", {spec.many_argument: [{"number": "A-1"}], "number": "B-2"}, user=None
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == ["A-1"]


def _invoice_in_region(*, pk: str, tenant: str, region: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant, "region": region}


def _same_tenant(*, tenant: Annotated[str, NotClientInput] = "acme") -> None:
    # The precondition owns ``tenant`` for the whole call.
    return None


def test_a_server_owned_lookup_key_is_not_refused_but_a_plain_one_is() -> None:
    # drf-services drops the caller's ``tenant`` before the lookup reads it
    # (``server_owned_keys``), so the schema does not offer it and a QueryParam
    # of that name takes nothing. ``region`` is the caller's.
    spec = ServiceSpec(
        service=_resolved,
        atomic=False,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_in_region
        ),
        preconditions=[_same_tenant],
    )
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(_server(), spec, query_params=("tenant", "region"))
    _assert_refused(caught, "the service's target lookup declares parameter(s) ['region']")
    assert "'tenant'" not in str(caught.value)


# ---------- what a selector tool offers ----------


class _SentFilter(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()

    class Meta:
        model = Invoice
        fields = ["sent"]


def _invoices() -> Any:
    return Invoice.objects.all()


def test_a_filter_set_field_a_query_param_shadows_is_refused() -> None:
    # The FilterSet reads the arguments the split has stripped, so ``sent`` was
    # never applied and every call listed every row.
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=_invoices, filter_set=_SentFilter)
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_selector(_server(), spec, query_params=("sent",))
    _assert_refused(caught, "the selector's filter_set declares field(s) ['sent']")


# ---------- a name a provider fills is served ----------


class _Status(TypedDict):
    status: str


def _status_from_query(request: Any) -> _Status:
    return {"status": request.query_params["status"]}


def _untyped_status_from_query(request: Any) -> dict[str, Any]:
    return {"status": request.query_params["status"]}


def _tasks_by_status(*, status: str) -> list[Any]:
    return [status]


@pytest.mark.parametrize("binding", _SPREADING)
@pytest.mark.parametrize(
    ("provider", "provides"),
    [
        pytest.param(_status_from_query, (), id="typed-provider"),
        # A provider whose keys cannot be read, which the author vouches for.
        pytest.param(_untyped_status_from_query, ("status",), id="spec-kwargs-provides"),
    ],
)
async def test_a_parameter_a_typed_provider_fills_is_served(
    binding: ArgumentBinding, provider: Any, provides: tuple[str, ...]
) -> None:
    # The way to route a query parameter to a selector: the QueryParam takes the
    # caller's value to ``request.query_params``, and the provider hands it to
    # the parameter. Refused as a shadow before. Under ``SPREAD_CALLER_WINS``
    # the schema still offers the name, so only the check's own subtraction of
    # what the provider fills exempts it.
    server = _server()
    _register_selector(
        server,
        SelectorSpec(kind=SelectorKind.LIST, selector=_tasks_by_status, kwargs=provider),
        query_params=("status",),
        argument_binding=binding,
        spec_kwargs_provides=provides,
    )
    out = await server.acall_tool("read", {"status": "open"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == ["open"]


@pytest.mark.parametrize("binding", _SPREADING)
async def test_a_spread_service_parameter_its_provider_fills_is_served(
    binding: ArgumentBinding,
) -> None:
    # Under ``SPREAD_CALLER_WINS`` the service's schema offers ``status``
    # though its provider fills it, so the check's subtraction is what exempts
    # it there; under ``SPREAD_AUTHOR_WINS`` the schema leaves it out.
    server = _server()
    _register_service(
        server,
        ServiceSpec(service=_set_flag, atomic=False, kwargs=_status_from_query),
        query_params=("status",),
        argument_binding=binding,
    )
    out = await server.acall_tool("act", {"status": "open"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"status": "open"}


class _Tenant(TypedDict):
    tenant: str


def _tenant_from_query(request: Any) -> _Tenant:
    return {"tenant": request.query_params["tenant"]}


def _resolved_in_tenant(*, instance: Any, tenant: str) -> Any:
    return instance


class _TenantIn(drf_serializers.Serializer):
    tenant = drf_serializers.CharField()


def _move(*, data: Any, tenant: str) -> Any:
    return data


@pytest.mark.parametrize(
    ("spec", "taken_by"),
    [
        pytest.param(
            ServiceSpec(
                service=_resolved_in_tenant,
                atomic=False,
                kwargs=_tenant_from_query,
                instance_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, selector=_invoice_in_tenant
                ),
            ),
            "the service's target lookup declares parameter(s) ['tenant']",
            id="lookup-parameter",
        ),
        pytest.param(
            ServiceSpec(
                service=_move, atomic=False, kwargs=_tenant_from_query, input_serializer=_TenantIn
            ),
            "the service's input_serializer declares field(s) ['tenant']",
            id="serializer-field",
        ),
    ],
)
def test_the_services_provider_exempts_no_lookup_parameter_or_field(
    spec: ServiceSpec, taken_by: str
) -> None:
    # The service's provider fills the service's pool. The lookup reads the
    # arguments and its own provider, and the serializer the arguments, so
    # neither receives what the service's provider returns: the lookup was
    # answered "Missing required argument(s): `tenant`." on every call.
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_service(
            _server(),
            spec,
            query_params=("tenant",),
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
    _assert_refused(caught, taken_by)


# ---------- a key the provider may leave to the caller ----------


class _MaybeStatus(TypedDict):
    status: str | UnsetType


def _maybe_status_from_query(request: Any) -> _MaybeStatus:
    return {"status": request.query_params["status"]}


def _invoice_by_status(*, pk: str, status: str) -> dict[str, Any]:
    return {"pk": pk, "status": status}


def _spec_for(kind: str, provider: Any) -> Any:
    if kind == "selector":
        return SelectorSpec(kind=SelectorKind.LIST, selector=_tasks_by_status, kwargs=provider)
    if kind == "service":
        return ServiceSpec(service=_set_flag, atomic=False, kwargs=provider)
    return ServiceSpec(
        service=_resolved,
        atomic=False,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_invoice_by_status, kwargs=provider
        ),
    )


@pytest.mark.parametrize(
    "provider",
    [
        pytest.param(_maybe_status_from_query, id="declinable"),
        pytest.param(_untyped_status_from_query, id="untyped"),
    ],
)
@pytest.mark.parametrize(
    ("kind", "taken_by"),
    [
        ("selector", "the selector declares parameter(s) ['status']"),
        ("service", "the service declares parameter(s) ['status']"),
        ("lookup", "the service's target lookup declares parameter(s) ['status']"),
    ],
)
def test_a_key_the_provider_may_leave_to_the_caller_is_refused(
    kind: str, taken_by: str, provider: Any
) -> None:
    # On a call where the provider does not fill the key, the caller's value is
    # the only one, and the QueryParam has taken it.
    server = _server()
    with pytest.raises(ImproperlyConfigured) as caught:
        if kind == "selector":
            _register_selector(server, _spec_for(kind, provider), query_params=("status",))
        else:
            _register_service(
                server,
                _spec_for(kind, provider),
                query_params=("status",),
                argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            )
    _assert_refused(caught, taken_by)


# ---------- a name the selector tool's input_serializer lays back ----------


def _status_and_query(*, status: str, request: Any) -> dict[str, Any]:
    return {"status": status, "query": dict(request.query_params.items())}


@dataclasses.dataclass
class _StatusFields:
    status: str


class _StatusDataclassIn(DataclassSerializer):
    class Meta:
        dataclass = _StatusFields


@pytest.mark.parametrize(
    "input_serializer",
    [
        pytest.param(_StatusIn, id="serializer"),
        # A dataclass instance is laid back field by field, as a dict is.
        pytest.param(_StatusFields, id="bare-dataclass"),
        pytest.param(_StatusDataclassIn, id="dataclass-serializer"),
    ],
)
def test_a_name_the_input_serializer_lays_back_reaches_the_selector_on_tools_call(
    input_serializer: type,
) -> None:
    # The serializer validates the arguments before the split, and dispatch
    # lays the validated values back over the stripped ones, so the selector
    # receives the caller's value and ``request.query_params`` does too.
    server = _server()
    _register_selector(
        server,
        SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_status_and_query),
        query_params=("status",),
        input_serializer=input_serializer,
    )
    out = handle_tools_call({"name": "read", "arguments": {"status": "open"}}, _ctx(server))
    assert out["structuredContent"] == {"status": "open", "query": {"status": "open"}}


class _ReadOnlyStatusIn(drf_serializers.Serializer):
    status = drf_serializers.CharField(read_only=True)


class _StatusElsewhereIn(drf_serializers.Serializer):
    status = drf_serializers.CharField(source="state", required=False)


def _tasks_by_status_or_state(*, status: str = "open", state: str = "") -> list[Any]:
    return [status, state]


@pytest.mark.parametrize(
    "input_serializer",
    [
        # DRF keeps a read-only field out of the validated values.
        pytest.param(_ReadOnlyStatusIn, id="read-only-field"),
        # The value is laid back under ``state``, so ``status`` runs on its default.
        pytest.param(_StatusElsewhereIn, id="source-elsewhere"),
    ],
)
def test_a_field_not_laid_back_under_its_name_exempts_nothing(input_serializer: type) -> None:
    with pytest.raises(ImproperlyConfigured) as caught:
        _register_selector(
            _server(),
            SelectorSpec(kind=SelectorKind.LIST, selector=_tasks_by_status_or_state),
            query_params=("status",),
            input_serializer=input_serializer,
        )
    _assert_refused(caught, "the selector declares parameter(s) ['status']")


def _tenant_scoped_invoice(*, pk: str, tenant: str) -> dict[str, Any]:
    return {"pk": pk, "tenant": tenant}


async def test_a_lookup_parameter_its_own_provider_fills_is_served() -> None:
    # The lookup's own ``kwargs=`` fills ``tenant``, so the schema does not
    # offer it, and the provider hands the lookup the value the QueryParam
    # routed to ``request.query_params``.
    server = _server()
    _register_service(
        server,
        ServiceSpec(
            service=_resolved,
            atomic=False,
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_tenant_scoped_invoice,
                kwargs=_tenant_from_query,
            ),
        ),
        query_params=("tenant",),
    )
    out = await server.acall_tool("act", {"pk": "1", "tenant": "globex"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"pk": "1", "tenant": "globex"}
