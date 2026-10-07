"""A selector tool's ``inputSchema`` asks for what a call needs and nothing the server fills.

A signature read on its own cannot say which parameters the caller sends and
which the transport fills: ``get_invoice(*, pk)`` and ``outstanding(*, tenant)``
look alike, yet a client must send ``pk`` while ``tenant`` is a pool seed. So the
reflection is told what this server fills -- drf-services' ``supplied=`` -- and
answers with the rule a transport needs: a filled name is not advertised, and
every other parameter without a default is required.

What the server fills is read from the sources the call's pool is built from:
the registered seeds (and drf-services' own), a ``UrlKwarg`` that declares a
default, the keys a ``kwargs=`` provider annotated with a ``TypedDict`` returns,
the names ``spec_kwargs_provides=`` declares, and the fields the tool's
``input_serializer`` fills with a default. A provider whose keys cannot be read
may fill any parameter, so beside one nothing is inferred required.

Each source has a test of its own here, because deleting one from the union
leaves every branch covered. Built through ``MCPServer.list_tools`` rather than
the schema builder, so each test reads what a client is served.
"""

from __future__ import annotations

import dataclasses
import inspect
import pathlib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, Generic, TypeVar, Union

import pytest
from rest_framework import serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import DEFAULT_POOL_SEEDS, InputRequired, UnsetType
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from typing_extensions import NotRequired, TypedDict

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer

if TYPE_CHECKING:
    # Imported for annotations only, where flake8-type-checking rules move such
    # imports, so the name does not exist when the hints are resolved at runtime.
    from django.http import HttpRequest

_T = TypeVar("_T")


def _server(**kwargs: Any) -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None, **kwargs)


def _schema(server: MCPServer, name: str = "get") -> dict[str, Any]:
    listed: Any = server.list_tools(user=None)
    return next(tool for tool in listed["tools"] if tool["name"] == name)["inputSchema"]


def _register(server: MCPServer, selector: Any, **kwargs: Any) -> None:
    spec_kwargs: dict[str, Any] = {}
    if "provider" in kwargs:
        spec_kwargs["kwargs"] = kwargs.pop("provider")
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector, **spec_kwargs),
        **kwargs,
    )


def _by_pk(*, pk: int) -> Any:
    return Invoice.objects.filter(pk=pk)


def _by_pk_and_tenant(*, pk: int, tenant: str) -> Any:
    return Invoice.objects.filter(pk=pk, number__startswith=tenant)


def _by_pk_or_first(*, pk: int | None = None) -> Any:
    return Invoice.objects.filter(pk=pk) if pk is not None else Invoice.objects.all()


def _reporting(*, pk: int, progress: Any) -> Any:
    return Invoice.objects.filter(pk=pk)


def _marked(*, pk: int, number: Annotated[str, InputRequired]) -> Any:
    return Invoice.objects.filter(pk=pk, number=number)


class _ScopeBase(TypedDict):
    tenant: str


class _Scope(_ScopeBase, total=False):
    # ``total=False`` rather than ``NotRequired[...]``: under this module's
    # postponed annotations the qualifier is a string ``TypedDict`` does not
    # read, and ``region`` would land in ``__required_keys__``.
    region: str


class _GenericScope(TypedDict, Generic[_T]):
    tenant: _T


def _typed_provider() -> _Scope:
    return {"tenant": "acme"}


def _generic_provider() -> _GenericScope[str]:
    return {"tenant": "acme"}


def _untyped_provider() -> dict[str, Any]:
    return {"tenant": "acme"}


def _unresolvable_provider() -> Any:
    return {"tenant": "acme"}


# A return annotation naming a type that does not exist, so ``get_type_hints``
# raises. Assigned rather than written in the signature, where a linter would
# refuse the undefined name.
_unresolvable_provider.__annotations__ = {"return": "NoSuchScope"}


class _UnresolvableFieldScope(TypedDict):
    # The return annotation resolves; one of the class's own fields does not,
    # and the other does.
    tenant: NoSuchType  # noqa: F821
    region: str


def _unresolvable_field_provider() -> _UnresolvableFieldScope:
    return {"tenant": "acme"}


class _RegionScope(TypedDict):
    region: str


def _region_provider() -> _RegionScope:
    return {"region": "eu"}


# ``UnsetType`` in a value: drf-services lets a provider decline a key with
# ``UNSET``, and a declined key does not satisfy the parameter, so it is not
# counted as filled. Spelled three ways, because each reaches the check by a
# different route: a PEP 604 union, ``typing.Union`` (a separate origin before
# 3.14), and a ``NotRequired`` qualifier, which only ``typing_extensions``'
# hints strip on the older Pythons.
class _MaybeScope(TypedDict):
    tenant: str | UnsetType
    region: str


class _MaybeUnionScope(TypedDict):
    tenant: Union[str, UnsetType]  # noqa: UP007 -- the spelling under test
    region: str


class _MaybeNotRequiredScope(TypedDict):
    tenant: NotRequired[str | UnsetType]
    region: str


# A union that does not admit ``UNSET``, so ``tenant`` is filled.
class _NoneableScope(TypedDict):
    tenant: str | None
    region: str


def _maybe_provider() -> _MaybeScope:
    return {"tenant": "acme", "region": "eu"}


def _maybe_union_provider() -> _MaybeUnionScope:
    return {"tenant": "acme", "region": "eu"}


def _maybe_not_required_provider() -> _MaybeNotRequiredScope:
    return {"tenant": "acme", "region": "eu"}


def _noneable_provider() -> _NoneableScope:
    return {"tenant": "acme", "region": "eu"}


def _by_pk_tenant_region(*, pk: int, tenant: str, region: str) -> Any:
    return Invoice.objects.filter(pk=pk, number__startswith=tenant + region)


class _ContainerScope(TypedDict):
    # ``UnsetType`` inside a container: the key is always a list or a dict, and
    # only an item or a value of it may be ``UNSET``.
    tenant: str
    regions: list[str | UnsetType]
    labels: dict[str, str | UnsetType]


def _container_provider() -> _ContainerScope:
    return {"tenant": "acme", "regions": ["eu"], "labels": {}}


def _by_pk_tenant_regions_labels(*, pk: int, tenant: str, regions: list, labels: dict) -> Any:
    return Invoice.objects.filter(pk=pk)


def _hidden_parameter_provider(*, request: HttpRequest) -> _MaybeScope:
    # The return annotation resolves at runtime; the parameter's does not.
    return {"tenant": "acme", "region": "eu"}


def _generic_declining_provider() -> _GenericScope[str | UnsetType]:
    return {"tenant": "acme"}


def test_a_parameter_without_a_default_is_required() -> None:
    server = _server()
    _register(server, _by_pk)

    assert _schema(server)["required"] == ["pk"]


def test_a_parameter_with_a_default_stays_optional() -> None:
    server = _server()
    _register(server, _by_pk_or_first)

    schema = _schema(server)

    assert "pk" in schema["properties"]
    assert "required" not in schema


def test_a_registered_seed_is_not_advertised() -> None:
    # The registered half of the supplied set: drf-services adds only its own
    # seeds, so without the server's ``tenant`` would be asked of the client as a
    # required argument that dispatch then strips.
    server = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
    _register(server, _by_pk_and_tenant)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_dispatcher_seed_is_not_advertised() -> None:
    server = _server()
    _register(server, _reporting)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_url_kwarg_with_a_default_fills_the_parameter_so_it_is_not_required() -> None:
    server = _server()
    _register(server, _by_pk, url_kwargs=(UrlKwarg("pk", type="integer", default=1),))

    schema = _schema(server)

    # Still a property -- the ``UrlKwarg``'s, carrying its default -- because a
    # client may send it. Not required, because the default fills it otherwise.
    assert schema["properties"]["pk"] == {"type": "integer", "default": 1}
    assert "required" not in schema


def test_a_url_kwarg_with_no_default_leaves_the_selector_to_require_it() -> None:
    # It reaches the pool only when the client sends it, so it is the client's
    # to send, and the selector's signature says whether it must.
    server = _server()
    _register(server, _by_pk, url_kwargs=(UrlKwarg("pk", type="integer"),))

    schema = _schema(server)

    assert schema["properties"]["pk"] == {"type": "integer"}
    assert schema["required"] == ["pk"]


def test_a_name_a_typed_provider_returns_is_not_asked_for() -> None:
    server = _server()
    _register(server, _by_pk_tenant_region, provider=_typed_provider)

    schema = _schema(server)

    # ``region`` is a ``NotRequired`` key: the provider owns it, so it is
    # supplied as much as ``tenant`` is.
    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_parameterised_typed_dict_is_read_off_its_origin() -> None:
    server = _server()
    _register(server, _by_pk_and_tenant, provider=_generic_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_an_untyped_provider_leaves_every_parameter_optional() -> None:
    # It may fill ``pk`` too, and nothing says it does not, so requiring ``pk``
    # could refuse a call that runs. Both stay advertised, neither required.
    server = _server()
    _register(server, _by_pk_and_tenant, provider=_untyped_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert "required" not in schema


def test_a_provider_whose_annotation_does_not_resolve_is_untyped() -> None:
    server = _server()
    _register(server, _by_pk_and_tenant, provider=_unresolvable_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert "required" not in schema


def test_a_value_type_that_does_not_resolve_makes_only_its_key_optional() -> None:
    # Its return annotation names a ``TypedDict`` whose ``tenant`` value cannot
    # be read, so whether the provider may decline that key cannot be either:
    # it is offered and not required, as a declinable key is. ``region``
    # resolves, so it is filled and hidden, and ``pk`` stays required. The
    # whole provider used to read as untyped, which offered ``region`` and
    # required nothing.
    server = _server()
    _register(server, _by_pk_tenant_region, provider=_unresolvable_field_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def test_a_parameter_type_imported_only_for_type_checking_leaves_the_keys_readable() -> None:
    # Only the return annotation says which keys the provider fills, so a
    # parameter typed with a name that does not exist at runtime costs nothing:
    # ``region`` is filled and hidden, ``tenant`` may be declined and is
    # offered, and ``pk`` stays required. Resolving every annotation together
    # read the provider as untyped, which offered ``region`` and required
    # nothing.
    server = _server()
    _register(server, _by_pk_tenant_region, provider=_hidden_parameter_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


@pytest.mark.parametrize(
    "provider", [_maybe_provider, _maybe_union_provider, _maybe_not_required_provider]
)
def test_a_key_the_provider_may_decline_is_offered_but_not_required(provider: Any) -> None:
    # ``tenant`` may come back ``UNSET``, which leaves it to the caller, so it is
    # advertised; the provider may also fill it, so it is not required (and a
    # call is not refused for it: the handler tests). ``region`` cannot be
    # declined, so it is filled as before.
    server = _server()
    _register(server, _by_pk_tenant_region, provider=provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def _by_pk_and_marked_tenant(*, pk: int, tenant: Annotated[str, InputRequired]) -> Any:
    return Invoice.objects.filter(pk=pk)


def test_a_marked_key_the_provider_may_decline_stays_required() -> None:
    # Declinable reads like the untyped case for that one key: the missing
    # default asks nothing, the selector's own marker still does, as the
    # Pydantic-AI toolset advertises the same spec.
    server = _server()
    _register(server, _by_pk_and_marked_tenant, provider=_maybe_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk", "tenant"]


def test_a_union_that_does_not_admit_unset_is_still_filled() -> None:
    server = _server()
    _register(server, _by_pk_tenant_region, provider=_noneable_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_key_holding_unset_inside_a_container_is_filled() -> None:
    # The provider cannot decline ``regions`` or ``labels``: each comes back as
    # a list or a dict, and only a value that is ``UNSET`` itself is dropped
    # from the pool. So both are filled and hidden, as ``tenant`` is, and a
    # client's value for either is not offered to be overwritten. Walking every
    # argument of every generic found ``UnsetType`` inside them and offered both.
    server = _server()
    _register(server, _by_pk_tenant_regions_labels, provider=_container_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_generic_typed_dicts_argument_decides_which_keys_may_be_declined() -> None:
    # ``_GenericScope[str | UnsetType]`` binds ``tenant: _T`` to a value the
    # provider may decline, so ``tenant`` is offered and not required, as the
    # same key written out (``tenant: str | UnsetType``) is. Read off the
    # unparameterised origin, ``tenant`` was the bare type variable, counted as
    # filled and hidden, and a declining provider left the call short of it.
    server = _server()
    _register(server, _by_pk_and_tenant, provider=_generic_declining_provider)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


# ---------- SPREAD_CALLER_WINS: the caller's value outranks the provider's ----------


def test_under_caller_wins_a_typed_providers_keys_are_offered_but_not_required() -> None:
    # The caller's spread is applied after the provider's keys, so a client may
    # set them; the provider fills them otherwise, so none is required.
    server = _server()
    _register(
        server,
        _by_pk_tenant_region,
        provider=_typed_provider,
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant", "region"}
    assert schema["required"] == ["pk"]


def test_under_caller_wins_a_spec_kwargs_provides_name_is_offered_but_not_required() -> None:
    # A typed provider beside it, so ``pk`` is still inferred required and the
    # declared name's requiredness is what this test reads.
    server = _server()
    _register(
        server,
        _by_pk_and_tenant,
        provider=_region_provider,
        spec_kwargs_provides=("tenant",),
        argument_binding=ArgumentBinding.SPREAD_CALLER_WINS,
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk", "tenant"}
    assert schema["required"] == ["pk"]


def test_under_caller_wins_a_seed_is_still_not_advertised() -> None:
    # Dispatch strips a reserved name from the caller's spread under every
    # binding, so a seed outranks the caller whatever the binding says.
    server = _server(pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))
    _register(server, _by_pk_and_tenant, argument_binding=ArgumentBinding.SPREAD_CALLER_WINS)

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]


def test_a_marked_parameter_stays_required_beside_an_untyped_provider() -> None:
    # The cut keeps what the selector states outright: requiredness inferred
    # from a missing default goes, an ``InputRequired`` marker stays.
    server = _server()
    _register(server, _marked, provider=_untyped_provider)

    schema = _schema(server)

    assert schema["required"] == ["number"]


def test_a_name_spec_kwargs_provides_declares_is_not_advertised() -> None:
    # The registration-time declaration that the spec's provider fills a name,
    # which drf-mcp already counts as that parameter's source.
    # Declared beside an untyped provider, the realistic pairing: the declared
    # name goes, and ``pk`` stays optional because the provider is still opaque.
    server = _server()
    _register(
        server,
        _by_pk_and_tenant,
        provider=_untyped_provider,
        spec_kwargs_provides=("tenant",),
    )

    schema = _schema(server)

    assert set(schema["properties"]) == {"pk"}
    assert "required" not in schema


# ---------- the input serializer's defaults ----------


class _DefaultedPk(serializers.Serializer):
    pk = serializers.IntegerField(default=1)


class _HiddenTenant(serializers.Serializer):
    pk = serializers.IntegerField()
    tenant = serializers.HiddenField(default="acme")


class _ReadOnlyPk(serializers.Serializer):
    pk = serializers.IntegerField(read_only=True, default=1)


class _OptionalPk(serializers.Serializer):
    pk = serializers.IntegerField(required=False)


@dataclasses.dataclass
class _PkInput:
    pk: int = 1


class _PkInputSerializer(DataclassSerializer):
    # Declared rather than generated, so the field carries a default (a
    # generated one leaves it to the dataclass) and registration sees ``pk``.
    pk = serializers.IntegerField(default=1)

    class Meta:
        dataclass = _PkInput


def test_a_name_the_input_serializer_defaults_is_not_required() -> None:
    # The validated values overlay the selector's params, so the default
    # reaches it. Advertised with the serializer's property, default and all.
    server = _server()
    _register(server, _by_pk, input_serializer=_DefaultedPk)

    schema = _schema(server)

    assert schema["properties"]["pk"] == {"type": "integer", "default": 1}
    assert "required" not in schema


def test_a_hidden_field_fills_the_parameter_so_it_is_not_required() -> None:
    # ``tenant`` stays a property only because the serializer's reflection
    # advertises a ``HiddenField``; the selector's reflection no longer does.
    server = _server()
    _register(server, _by_pk_and_tenant, input_serializer=_HiddenTenant)

    assert _schema(server)["required"] == ["pk"]


def test_an_optional_field_without_a_default_leaves_the_selector_to_require_it() -> None:
    # Omitted, it is absent from the validated values too, so nothing reaches
    # the selector's ``pk``.
    server = _server()
    _register(server, _by_pk, input_serializer=_OptionalPk)

    assert _schema(server)["required"] == ["pk"]


def test_a_read_only_default_does_not_fill_the_parameter() -> None:
    # DRF keeps a read-only field's default out of ``validated_data``.
    server = _server()
    _register(server, _by_pk, input_serializer=_ReadOnlyPk)

    assert _schema(server)["required"] == ["pk"]


def test_a_dataclass_inputs_default_does_not_fill_the_parameter() -> None:
    # A dataclass validates into an instance, which is not overlaid on the
    # selector's params.
    server = _server()
    _register(server, _by_pk, input_serializer=_PkInput)

    assert _schema(server)["required"] == ["pk"]


def test_a_dataclass_serializers_default_does_not_fill_the_parameter() -> None:
    server = _server()
    _register(server, _by_pk, input_serializer=_PkInputSerializer)

    assert _schema(server)["required"] == ["pk"]


# ---------- the concepts page's example ----------

_CONCEPTS = pathlib.Path(__file__).resolve().parents[2] / "docs" / "concepts.md"

# What drf-services calls a ``kwargs=`` provider with: the view and the request,
# and nothing else from the pool.
_PROVIDER_POOL = frozenset({"view", "request"})


def _requiredness_example() -> str:
    """The ``python`` block under the page's selector-requiredness heading."""
    page = _CONCEPTS.read_text()
    section = page[page.index("{ #selector-requiredness }") :]
    start = section.index("```python\n") + len("```python\n")
    return section[start : section.index("```", start)]


class _ScopedInvoices:
    """The page's ``Invoice``: the test app's model has no ``tenant`` field.

    Records each lookup and answers it by ``pk`` alone, so the test reads what
    the selector was called with and the call still renders a real row.
    """

    def __init__(self) -> None:
        self.lookups: list[dict[str, Any]] = []

    def filter(self, **lookups: Any) -> Any:
        self.lookups.append(lookups)
        return Invoice.objects.filter(pk=lookups["pk"])


@pytest.mark.django_db
def test_the_concepts_page_example_runs_as_written() -> None:
    invoices = _ScopedInvoices()
    namespace: dict[str, Any] = {"Invoice": SimpleNamespace(objects=invoices)}
    exec(compile(_requiredness_example(), str(_CONCEPTS), "exec"), namespace)
    invoice = Invoice.objects.create(number="INV-1")
    server = _server()
    server.register_selector_tool(
        name="get",
        spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=namespace["get_invoice"],
            output_serializer=InvoiceOutputSerializer,
            kwargs=namespace["scope"],
        ),
    )
    user = SimpleNamespace(is_authenticated=True, tenant="acme")

    # A parameter outside the pool is never bound, so the provider would raise
    # ``TypeError`` at the first call.
    assert set(inspect.signature(namespace["scope"]).parameters) <= _PROVIDER_POOL
    schema = _schema(server)
    result = server.call_tool("get", {"pk": invoice.pk}, user=user).to_dict()

    # The comment on ``get_invoice`` in the example.
    assert set(schema["properties"]) == {"pk"}
    assert schema["required"] == ["pk"]
    assert result["structuredContent"]["number"] == "INV-1"
    assert invoices.lookups == [{"pk": invoice.pk, "tenant": "acme"}]
