from __future__ import annotations

import dataclasses
from collections.abc import Callable, Iterable
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.fields import empty
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import UNSET, provider_keys, server_owned_keys, spec_to_json_schema
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.schema.input_schema import build_input_schema

_SENTENCE_ENDINGS: tuple[str, ...] = (".", "!", "?")


def end_sentence(text: str) -> str:
    """``text`` with a full stop added, unless it already ends a sentence.

    Wording this package appends goes after text it did not write: a consumer's
    ``QueryParam`` description, a serializer's error message. Neither reliably
    ends in punctuation (a field-selection library's "`items` field is not
    found" does not, DRF's own "This field is required." does), and a sentence
    appended to one that has not ended reads as a run-on to the model it is
    written for.
    """
    stripped = text.rstrip()
    return stripped if stripped.endswith(_SENTENCE_ENDINGS) else f"{stripped}."


def declares_default(default: Any) -> bool:
    """Whether a channel declaration carries a value to seed when the caller omits one.

    ``UrlKwarg`` / ``QueryParam`` are declared in the sister package, and the
    sentinel standing for "no default" there is version-dependent: older
    releases spell it ``None``, newer ones spell it with the package's ``UNSET``
    sentinel so that a deliberate ``default=None`` becomes expressible. Both are
    treated as *no default* here, which is correct against either release —
    testing only for ``None`` would seed the literal ``UNSET`` object as a real
    value for every declaration that names no default at all.

    Here rather than beside the splits in ``handlers.utils`` that seed the
    default, because the schema builders read it too: a ``UrlKwarg`` declaring
    one fills its parameter, so the selector behind it does not require it.
    """
    return default is not None and default is not UNSET


def selector_inputs(
    spec: SelectorSpec[Any, Any],
    *,
    url_kwargs: Iterable[UrlKwarg] = (),
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    provides: frozenset[str] = frozenset(),
    caller_wins: bool = False,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """A selector's input schema as this server calls it, and what a call needs.

    A signature alone cannot say which parameters the caller sends and which the
    transport fills, so drf-services' reflection is told: ``supplied`` names what
    this server fills in the pool ``dispatch_spec`` calls the selector with, read
    from the sources that pool is built from. A supplied name is not advertised,
    and every other parameter without a default is required. The union has four
    parts, each held by a test, because deleting one leaves every branch covered:

    - ``pool_seeds.reserved``: drf-services' own seeds and the registered ones.
      ``base_pool`` fills every registered seed, and dispatch strips every
      reserved name from what the client sends. drf-services adds its own seeds
      to any ``supplied`` set itself, so the registered half is the part only
      this server can state. Held by ``test_a_registered_seed_is_not_advertised``
      and ``test_a_lookup_parameter_the_server_seeds_is_not_advertised``.
    - the name of every ``UrlKwarg`` declaring a default, which reaches the pool
      through ``view.kwargs`` whenever the client leaves it out. Held by
      ``test_a_url_kwarg_with_a_default_fills_the_parameter_so_it_is_not_required``.
      One with no default reaches the pool only when the client sends it, so the
      selector's signature still says whether it must;
      ``test_a_url_kwarg_with_no_default_leaves_the_selector_to_require_it``
      holds that filter.
    - the keys the spec's ``kwargs=`` provider always fills, as drf-services'
      ``provider_keys`` reads them (not the ones it may decline, below). Held
      by ``test_a_name_a_typed_provider_returns_is_not_asked_for``.
    - ``provides``: what the caller knows fills a name and the spec cannot say
      -- a selector tool's ``spec_kwargs_provides=`` and the names its
      ``input_serializer`` defaults. Held by
      ``test_a_name_spec_kwargs_provides_declares_is_not_advertised`` and
      ``test_a_name_the_input_serializer_defaults_is_not_required``.

    **A name the provider may fill, without saying it will, is advertised and
    not required for lacking a default**: every name, beside a provider whose
    keys cannot be read, and a key the provider may decline (``provider_keys``
    returns it apart), which drf-services removes from the pool when it comes
    back ``UNSET``, so it does not satisfy the parameter. ``required`` keeps such
    a name only where the reflection requires it without ``supplied`` (an
    ``InputRequired`` marker, a required ``TypedDict`` key), and the second
    value never names it, because only the assembled pool can say whether it
    arrived, and drf-services checks the markers against that pool itself.
    This is the Pydantic-AI ``SpecToolset``'s rule, so one spec asks for the same
    arguments on both routes. Each half is held by a test:

    - the untyped provider, whose second value is empty:
      ``test_an_untyped_provider_leaves_every_parameter_optional``;
    - a declinable key, in the schema and on the call side:
      ``test_a_key_the_provider_may_decline_is_offered_but_not_required`` and
      ``test_a_key_the_provider_may_decline_is_not_refused``;
    - a marker standing (``name in declared``):
      ``test_a_marked_parameter_stays_required_beside_an_untyped_provider`` and
      ``test_a_marked_key_the_provider_may_decline_stays_required``;
    - every other name staying required and checked (``name in checked``):
      ``test_a_parameter_without_a_default_is_required``;
    - no check for a marked declinable key:
      ``test_a_marked_key_the_provider_may_decline_is_not_refused``.

    ``provides`` still drops its names beside an untyped provider: declaring a
    name is not a claim that the provider fills nothing else.

    **Under ``SPREAD_CALLER_WINS`` (``caller_wins``) the provider's keys and the
    ``provides`` names are offered rather than supplied**: advertised as the
    reflection describes them, and neither required nor checked, marker or not,
    because dispatch applies the caller's spread after them, so they fill a name
    only when the caller sends none. Held by
    ``test_under_caller_wins_a_typed_providers_keys_are_offered_but_not_required``,
    ``test_under_caller_wins_a_spec_kwargs_provides_name_is_offered_but_not_required``
    and, on the call side, ``test_under_caller_wins_a_provider_filled_name_is_not_refused``.
    "Marker or not" means ``name in declared`` does not keep an offered name,
    which ``test_under_caller_wins_a_marked_provider_key_is_neither_required_nor_refused``
    holds on the schema and on every route's call. Only a selector tool passes
    ``caller_wins``: drf-services lays a target lookup's provider over the
    arguments under every binding, so a lookup is read author-wins
    (``test_a_target_lookups_provider_outranks_the_caller_under_every_binding``).
    A seed and a defaulted ``UrlKwarg`` stay supplied under every binding,
    because dispatch strips a reserved name from the caller's spread and the
    channel split pops a ``UrlKwarg`` name out of it
    (``test_under_caller_wins_a_seed_is_still_not_advertised``).

    The second value is what a call is refused without: the schema's own
    ``required`` less every name a provider may fill, so the call and the
    schema cannot disagree.
    """
    # The reader is drf-services', the static half of the ``kwargs=`` contract
    # whose runtime half dispatch owns, so both spec transports read a provider
    # the same way: a key holding ``UnsetType`` only inside a container is
    # filled, one annotation that does not resolve costs only what it names,
    # and a generic ``TypedDict`` is read with its arguments bound. Each is held
    # here by a test of the schema a tool advertises:
    # ``test_a_key_holding_unset_inside_a_container_is_filled``,
    # ``test_a_parameter_type_imported_only_for_type_checking_leaves_the_keys_readable``,
    # ``test_a_value_type_that_does_not_resolve_makes_only_its_key_optional`` and
    # ``test_a_generic_typed_dicts_argument_decides_which_keys_may_be_declined``.
    keys = provider_keys(spec.kwargs)
    filled, declinable = keys if keys is not None else (frozenset(), frozenset())
    defaulted = frozenset(uk.name for uk in url_kwargs if declares_default(uk.default))
    supplied = pool_seeds.reserved | defaulted
    offered: frozenset[str] = frozenset()
    if caller_wins:
        offered = filled | provides
    else:
        supplied |= filled | provides
    # ``phase="input"`` always returns a dict (only the output phase is
    # nullable), so ``or {}`` here and below only narrows the type.
    schema: dict[str, Any] = spec_to_json_schema(spec, phase="input", supplied=supplied) or {}
    uninformed: dict[str, Any] = spec_to_json_schema(spec, phase="input") or {}
    declared: list[str] = uninformed.get("required", [])
    required = [name for name in schema.get("required", []) if name not in offered]
    # ``None`` is every name: a provider whose keys cannot be read may fill any.
    checked = () if keys is None else tuple(name for name in required if name not in declinable)
    kept = [name for name in required if name in checked or name in declared]
    # Both schema builders read ``required`` with a default and emit it only
    # when it is non-empty, so an empty list here stands for none.
    return {**schema, "required": kept}, checked


def target_lookup(spec: ServiceSpec[Any, Any, Any]) -> SelectorSpec[Any, Any] | None:
    """The selector spec a service resolves its target through, as dispatch calls it.

    The one target lookup the spec declares, or ``None``. drf-services refuses
    at construction every spec that would declare a lookup dispatch never
    calls: an ``instance_selector_spec`` beside a ``collection_selector_spec``,
    and either beside ``many=True``, whose dispatch resolves no target. So
    whichever lookup is declared is the one dispatch calls, and the one
    drf-services' ``declared_input_keys`` admits the keys of, and a ``many=True``
    spec has none. ``test_a_spec_declaring_a_lookup_dispatch_never_calls_is_refused``
    holds the construction refusal this reading rests on.
    """
    if spec.collection_selector_spec is not None:
        return spec.collection_selector_spec
    return spec.instance_selector_spec


def required_arguments(
    binding: SelectorToolBinding | ToolBinding,
    *,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    input_serializer_runs: bool = True,
) -> tuple[str, ...]:
    """The arguments a call to ``binding`` cannot go without, as its schema requires them.

    Read off the same ``selector_inputs`` the ``inputSchema`` is built from, so
    what a call is refused for and what the client was told cannot drift apart.
    Only the selectors' half: an input serializer's required fields are checked
    by the serializer, which answers a missing one itself.

    ``input_serializer_runs=False`` is for the route that does not run a
    selector tool's MCP-only ``input_serializer`` (``call_tool``). Its defaults
    fill nothing there, so a name only they fill is required of that call,
    though the schema, which describes the routes that run it, does not ask for
    it. Held by
    ``test_call_tool_refuses_a_name_only_the_input_serializer_it_skips_would_fill``.

    **A service tool's lookup key the server owns is not required of the
    caller**, as the service tool's schema does not advertise it: a key the
    service or one of its preconditions marks ``NotClientInput``
    (``server_owned_keys``), less the names the ``input_serializer``'s schema
    lists, which stay the caller's input. drf-services drops the caller's value
    for such a key before the lookup reads it, so refusing a call for leaving
    it out named a key no resend could deliver; one nothing on the server
    fills is the author's gap, which drf-services answers as the lookup's own
    error. Held by ``test_a_lookup_key_the_server_owns_is_not_asked_of_the_caller``.
    """
    if isinstance(binding, SelectorToolBinding):
        return selector_tool_inputs(
            binding, pool_seeds=pool_seeds, input_serializer_runs=input_serializer_runs
        )[1]
    lookup = target_lookup(binding.spec)
    if lookup is None:
        return ()
    # Read author-wins whatever the binding says: dispatch lays a lookup's
    # provider over the arguments in every mode.
    required = selector_inputs(lookup, url_kwargs=binding.url_kwargs, pool_seeds=pool_seeds)[1]
    # The subtraction ``service_tool_schema`` applies to what it advertises,
    # read off the same serializer schema, so the two cannot drift apart.
    fields: dict[str, Any] = build_input_schema(binding.spec.input_serializer).get("properties", {})
    owned = server_owned_keys(binding.spec) - frozenset(fields)
    return tuple(name for name in required if name not in owned)


def selector_tool_inputs(
    binding: SelectorToolBinding,
    *,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    input_serializer_runs: bool = True,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """``selector_inputs`` for a selector tool's own selector, as registered.

    Two sources drf-services' reflection cannot read off the spec, because both
    are declared at registration rather than on it, go in as ``provides``:

    - ``spec_kwargs_provides=``, the opt-in drf-mcp already counts as a
      parameter's source at registration.
    - the names the tool's ``input_serializer`` fills when the client sends
      none (``laid_back_inputs``), because the validated values are laid back
      over the selector's params, a dataclass instance's as well as a
      ``dict``. Without them a selector parameter its serializer or its
      dataclass defaults would be advertised as required, though a call
      leaving it out is served. Left out when ``input_serializer_runs`` is
      false, for the route that does not run the serializer
      (``required_arguments``).

    The binding's ``argument_binding`` says whether the caller's spread outranks
    the provider (``SPREAD_CALLER_WINS``), which decides whether those names
    and the provider's keys are hidden or offered.
    """
    provides = frozenset(binding.spec_kwargs_provides)
    if input_serializer_runs:
        provides |= frozenset(laid_back_inputs(binding.input_serializer)[1])
    return selector_inputs(
        binding.spec,
        url_kwargs=binding.url_kwargs,
        pool_seeds=pool_seeds,
        provides=provides,
        caller_wins=binding.argument_binding is ArgumentBinding.SPREAD_CALLER_WINS,
    )


def laid_back_inputs(
    input_serializer: type | drf_serializers.Serializer | None,
) -> tuple[frozenset[str], dict[str, Callable[[], Any]]]:
    """What a selector tool's ``input_serializer`` lays back over the selector's params.

    The one reader of it, so the three places that need it cannot drift:
    registration's source count for a selector
    (``adapters.utils._validate_required_params_have_sources``) and its
    pagination exemption (``validate_selector_parameter_names``), the names a
    selector tool's schema does not require (``selector_tool_inputs``), and the
    defaults dispatch supplies for a URL kwarg the call left out
    (``handlers.selector_tool_dispatch._url_kwarg_defaults``). Their agreement
    is held by
    ``test_registration_the_schema_and_dispatch_agree_on_what_an_input_lays_back``,
    over a bare ``@dataclass``, a ``DataclassSerializer`` and a plain
    ``Serializer``.

    Dispatch lays the validated values back in both shapes they arrive in
    (``handlers.selector_tool_dispatch._validated_values``): a plain
    ``Serializer``'s ``dict``, and the dataclass instance a bare ``@dataclass``
    or a ``DataclassSerializer`` validates into, every field under its own
    name. Read off the serializer's fields as built for the call, a bare
    dataclass wrapped in a ``DataclassSerializer`` as dispatch wraps it, or off
    the bound serializer dispatch validated with, whose fields' defaults read
    that call's context. Two answers:

    - **the names laid back with the caller's value**: a field that is not
      ``read_only``, since DRF keeps a read-only field out of the validated
      values and a dataclass field so declared is laid back as its default,
      and bound to its own name, since ``source="number"`` puts the value
      under ``number`` and ``source="*"`` merges it. A ``DataclassSerializer``'s
      generated fields are among them, which its ``_declared_fields`` alone
      left out.
    - **the names it fills when the caller sends nothing**, each with what
      produces the value, which is always the author's default and never
      anything the caller sent. One of the names above whose field declares a
      ``default`` (a ``HiddenField`` included), in either shape, because DRF
      puts the default in the validated values. In the dataclass shape, also
      every field of the dataclass that declares a default of its own,
      whether its serializer field is generated, read-only or absent, because
      the instance is built with it; the serializer field's default comes
      first, since DRF builds the instance with that one. A field the
      serializer requires is counted too, and harmlessly: its own schema keeps
      the name required, and it refuses a call without it before any default
      is read.

    Each condition is held by a test, since a chain of them is one branch arc:

    - ``None`` (no ``input_serializer``): every serializer-less registration,
      which fails at collection without it;
    - a class rather than the bound serializer dispatch passes:
      ``test_a_url_kwarg_the_call_left_out_reaches_the_selector_only_as_a_namesake_default``;
    - a bare dataclass, wrapped:
      ``test_a_pagination_named_parameter_a_dataclass_input_declares_is_allowed``;
    - not ``read_only``: ``read-only-field`` and ``read-only-dataclass-field`` of
      ``test_a_field_whose_value_is_not_laid_back_exempts_nothing``, and
      ``test_a_read_only_default_does_not_fill_the_parameter``;
    - bound to its own name: ``source-elsewhere`` of the same test;
    - a ``default``: ``test_an_optional_field_without_a_default_leaves_the_selector_to_require_it``;
    - the dataclass shape: ``test_a_dataclass_inputs_default_fills_the_parameter``
      and the agreement test's dataclass cases;
    - a dataclass default at all:
      ``test_an_optional_field_over_no_dataclass_default_leaves_the_selector_to_require_it``;
    - the serializer field's default first: ``declared-default`` of
      ``test_a_dataclass_inputs_route_is_the_kwarg_sent_or_the_namesake_default``.

    ``None`` is a tool with no ``input_serializer``. Any other shape was
    refused at registration (``adapters.utils.validate_serializer_shapes``).
    """
    if input_serializer is None:
        return frozenset(), {}
    serializer: Any = input_serializer
    if isinstance(input_serializer, type):
        serializer = (
            DataclassSerializer(dataclass=input_serializer)
            if dataclasses.is_dataclass(input_serializer)
            else input_serializer()
        )
    fields = serializer.fields
    overlaid = frozenset(
        name for name, field in fields.items() if not field.read_only and field.source == name
    )
    fills: dict[str, Callable[[], Any]] = {
        name: fields[name].get_default for name in overlaid if fields[name].default is not empty
    }
    if isinstance(serializer, DataclassSerializer):
        for field in dataclasses.fields(serializer.dataclass_definition.dataclass_type):
            default = _dataclass_default(field)
            if default is not None:
                fills.setdefault(field.name, default)
    return overlaid, fills


def _dataclass_default(field: dataclasses.Field[Any]) -> Callable[[], Any] | None:
    """What produces ``field``'s default when the dataclass is built without it, if anything.

    A ``default_factory`` is called per instance, as the dataclass calls it
    (``test_a_dataclass_default_factory_fills_the_parameter``); a field with
    neither produces nothing
    (``test_an_optional_field_over_no_dataclass_default_leaves_the_selector_to_require_it``).
    """
    if field.default_factory is not dataclasses.MISSING:
        return field.default_factory
    if field.default is not dataclasses.MISSING:
        value = field.default
        return lambda: value
    return None


__all__ = [
    "declares_default",
    "end_sentence",
    "laid_back_inputs",
    "required_arguments",
    "selector_inputs",
    "selector_tool_inputs",
    "target_lookup",
]
