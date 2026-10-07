from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.fields import empty
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import UNSET, provider_keys, spec_to_json_schema
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg

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
    return selector_inputs(lookup, url_kwargs=binding.url_kwargs, pool_seeds=pool_seeds)[1]


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
    - the fields of the tool's ``input_serializer`` that fill a value when the
      client sends none (``_serializer_fills``), because the validated values
      overlay the selector's params. Without them a selector parameter its
      serializer defaults would be advertised as required beside a property
      carrying the default. Left out when ``input_serializer_runs`` is false,
      for the route that does not run the serializer (``required_arguments``).

    The binding's ``argument_binding`` says whether the caller's spread outranks
    the provider (``SPREAD_CALLER_WINS``), which decides whether those names
    and the provider's keys are hidden or offered.
    """
    provides = frozenset(binding.spec_kwargs_provides)
    if input_serializer_runs:
        provides |= _serializer_fills(binding.input_serializer)
    return selector_inputs(
        binding.spec,
        url_kwargs=binding.url_kwargs,
        pool_seeds=pool_seeds,
        provides=provides,
        caller_wins=binding.argument_binding is ArgumentBinding.SPREAD_CALLER_WINS,
    )


def _serializer_fills(input_serializer: type | None) -> frozenset[str]:
    """The fields ``input_serializer`` puts in its validated values when the client omits them.

    A writable field with a ``default`` (a ``HiddenField`` included): DRF puts
    its default in ``validated_data``, which a selector tool overlays on the
    selector's params. Each condition is held by a test of its own, because the
    chain is one branch arc:

    - a DRF ``Serializer`` class: a bare ``@dataclass`` validates into a
      dataclass instance, which is not overlaid, so its defaults fill nothing
      (``test_a_dataclass_inputs_default_does_not_fill_the_parameter``; the
      ``isinstance`` arm is the ``None`` every serializer-less tool passes);
    - not a ``DataclassSerializer``, for the same reason, though a field it
      declares can carry a default
      (``test_a_dataclass_serializers_default_does_not_fill_the_parameter``);
    - not ``read_only``: DRF keeps a read-only field's default out of
      ``validated_data`` (``test_a_read_only_default_does_not_fill_the_parameter``);
    - a ``default`` (``test_a_name_the_input_serializer_defaults_is_not_required``
      and ``test_an_optional_field_without_a_default_leaves_the_selector_to_require_it``).
    """
    if (
        not isinstance(input_serializer, type)
        or not issubclass(input_serializer, drf_serializers.Serializer)
        or issubclass(input_serializer, DataclassSerializer)
    ):
        return frozenset()
    return frozenset(
        name
        for name, field in input_serializer().fields.items()
        if not field.read_only and field.default is not empty
    )


__all__ = [
    "declares_default",
    "end_sentence",
    "required_arguments",
    "selector_inputs",
    "selector_tool_inputs",
    "target_lookup",
]
