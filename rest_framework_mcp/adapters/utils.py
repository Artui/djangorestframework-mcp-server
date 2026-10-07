"""Adapter-layer helpers shared by the service / selector / chain adapters.

The ``validate_*`` functions run at adapter time — *before* the binding lands
in a registry — so a configuration mistake surfaces during application startup
rather than the first time a client calls the tool. The ``merge_*`` ones fold
the several contributions a binding assembles (tool annotations, ``_meta``
bundles) into the single dict the wire types emit.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.validate_channel_names import validate_channel_names

from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
)
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.schema.utils import declares_default


def validate_serializer_shapes(
    *, label: str, input_serializer: object = None, output_serializer: object = None
) -> None:
    """Fail-fast at registration time on a serializer no MCP path can use.

    Run by the three tool adapters and, for its output alone, by the resource
    adapter.

    **Input** must be ``None``, a DRF ``Serializer`` subclass or a dataclass type.
    That is the one rule every consumer of it shares: drf-services'
    ``build_input_serializer_from_data`` refuses anything else at dispatch, its
    ``serializer_to_json_schema`` refuses it at schema derivation, and
    this package's own ``build_validated_input_serializer`` (the chain path)
    reads ``.fields`` off whatever it builds.

    **Output** must be ``None``, a DRF ``BaseSerializer`` subclass or a dataclass
    type. It is deliberately wider than input, because output is only rendered.
    Every render site — ``render_spec_output`` for tools, ``build_resource_contents``
    for resources — resolves the declaration through drf-services'
    ``renderable_serializer_class`` and calls what comes back as
    ``serializer(value, many=..., context=...).data``. A read-only
    ``BaseSerializer`` subclass, which DRF documents for exactly that job, answers
    it, and ``output_to_json_schema`` honestly derives no schema for one. A
    dataclass is wrapped in a ``DataclassSerializer`` by that resolution, so it
    renders and advertises its fields.

    **Why at registration.** ``tools/list`` derives every tool's schema on each
    request, so a shape upstream refuses fails discovery for the whole server,
    not for the one tool; and a shape dispatch refuses fails every call to the
    tool whether or not its schema derived. Registration is the only place the
    mistake is reported once, against the tool that made it. It runs first in
    each adapter, ahead of the callable-parameter checks, which would otherwise
    read an unusable serializer as one with no fields and blame the callable.
    """
    if input_serializer is not None and not (
        _is_class_of(input_serializer, drf_serializers.Serializer)
        or _is_dataclass_type(input_serializer)
    ):
        raise ImproperlyConfigured(
            f"{label}: input_serializer must be a DRF Serializer subclass or a dataclass "
            f"type, got {input_serializer!r}. Anything else is refused when the tool is "
            "called and when its schema is derived, which fails tools/list for every "
            "tool on the server."
            f"{_instance_hint(input_serializer)}"
        )
    if output_serializer is not None and not (
        _is_class_of(output_serializer, drf_serializers.BaseSerializer)
        or _is_dataclass_type(output_serializer)
    ):
        raise ImproperlyConfigured(
            f"{label}: output serializer must be a DRF BaseSerializer subclass or a "
            f"dataclass type, got {output_serializer!r}. Rendering calls it as "
            "serializer(value, many=..., context=...).data, so every call or read that "
            f"renders it would fail.{_instance_hint(output_serializer)}"
        )


def _is_class_of(value: object, base: type) -> bool:
    return isinstance(value, type) and issubclass(value, base)


def _is_dataclass_type(value: object) -> bool:
    return isinstance(value, type) and dataclasses.is_dataclass(value)


def _instance_hint(value: object) -> str:
    """Name the likeliest cause when the value is an instance of an accepted shape.

    ``InvoiceSerializer(many=True)`` in place of the class is the usual form:
    ``many`` is decided per dispatch, never at declaration.
    """
    if isinstance(value, drf_serializers.BaseSerializer) or (
        dataclasses.is_dataclass(value) and not isinstance(value, type)
    ):
        return " Pass the class itself, not an instance of it."
    return ""


def validate_url_kwargs(
    *,
    label: str,
    url_kwargs: tuple[UrlKwarg, ...],
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> None:
    """Fail-fast at registration time on a bad ``url_kwargs`` declaration.

    A URL kwarg is popped into the off-HTTP ``view.kwargs`` and stripped from the
    spec params, so its name must not collide with a reserved transport key —
    the post-fetch pagination knobs (``ordering`` / ``page`` / ``limit``) or the
    dispatcher's pool seeds — nor be declared twice, nor claim to be ``required``
    while carrying a ``default``. Colliding with an ordinary spec input is
    *allowed*: that is the intended way to route a route-capture the spec also
    reads.

    The checks live in drf-services' ``validate_channel_names``, which folds in
    the pool seeds it owns; the pagination names and the server's own
    ``pool_seeds`` are ours to contribute. A registered seed is reserved at
    dispatch, which strips it from the URL kwargs, so a declaration named after
    one would be accepted here and then silently dropped on every call.
    Sharing the check is what keeps this package's notion of a valid declaration
    from drifting away from the agent toolset's.
    """
    validate_channel_names(
        label=label,
        kind="url_kwargs",
        declarations=url_kwargs,
        reserved=RESERVED_POST_FETCH_KEYS | pool_seeds.names,
    )


def validate_query_params(
    *,
    label: str,
    query_params: tuple[QueryParam, ...],
    url_kwargs: tuple[UrlKwarg, ...] = (),
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> None:
    """Fail-fast at registration time on a bad ``query_params`` declaration.

    The sibling of ``validate_url_kwargs``, delegating the name checks to the
    same shared ``validate_channel_names`` with the same reserved set: a query
    param is popped out of the caller's arguments exactly as a URL kwarg is, so
    the same names are off-limits. ``QueryParam`` carries no ``required`` flag,
    so the validator's required-with-a-default check is inert here by
    construction — a read-shaping param the spec runs fine without cannot be
    required.

    One name cannot route to two channels: a URL kwarg lands in ``view.kwargs``
    and a query param in ``request.query_params``, and a value is popped from the
    arguments once. **That exclusivity is checked here rather than upstream**
    because ``validate_channel_names`` takes one declaration list and so cannot
    see a name in both channels; a concatenated list would report the overlap as
    a duplicate ``url_kwargs`` name and point the consumer at the wrong knob.
    """
    validate_channel_names(
        label=label,
        kind="query_params",
        declarations=query_params,
        reserved=RESERVED_POST_FETCH_KEYS | pool_seeds.names,
    )
    overlap = sorted({qp.name for qp in query_params} & {uk.name for uk in url_kwargs})
    if overlap:
        raise ImproperlyConfigured(
            f"{label}: name(s) {overlap} are declared as both a QueryParam and a "
            "UrlKwarg. A value routes to one channel — query_params reaches "
            "request.query_params, url_kwargs reaches view.kwargs — and is popped "
            "from the arguments once. Pick the channel the reader actually uses."
        )


def validate_selector_parameter_names(
    *,
    label: str,
    selector: Any,
    query_params: tuple[QueryParam, ...],
    input_serializer: type | None,
) -> None:
    """Fail-fast on a selector parameter the selector-tool transport takes away.

    The sibling of ``validate_url_kwargs`` / ``validate_query_params``, from the
    selector's side of the same collision. Those refuse a *channel* named after
    a name the read pipeline owns; this refuses a *selector parameter* that one
    of those names would take, because the parameter registers, is advertised,
    and then never receives what the caller sent. A required one is answered
    "This field is required." for an argument the call carried; a defaulted one
    runs on its default whatever the call asked for. Two cases, each refused
    whether the parameter has a default or not:

    - ``page`` / ``limit`` (``RESERVED_POST_FETCH_KEYS``), which the dispatch
      strips from the selector's arguments whether or not the tool paginates.
    - a name one of the tool's ``query_params`` declares, whose value is routed
      to ``request.query_params`` and split out of the arguments.

    Neither is refused for a name the ``input_serializer`` lays back with the
    caller's value (``_overlaid_field_names``): dispatch overlays the validated
    values on the stripped arguments, so the selector does receive the caller's
    value under that name. The subtraction is held by
    ``test_a_pagination_named_parameter_the_input_serializer_declares_is_allowed``,
    ``test_a_pagination_named_parameter_a_dataclass_input_declares_is_allowed``
    and ``test_a_parameter_a_query_param_shadows_is_allowed_when_the_input_serializer_declares_it``,
    and its limit to the declared names by
    ``test_a_serializer_declaring_another_name_exempts_nothing``.

    A ``UrlKwarg`` sharing a parameter's name stays allowed: its value reaches
    the selector through ``view.kwargs``, as ``validate_url_kwargs`` documents.
    A ``**kwargs`` catch-all names nothing, so there is nothing to refuse.

    Run by ``MCPServer.register_selector_tool`` on the adapter's binding, so it
    reads the tool's effective ``query_params``, including those an
    ``agent_contract`` supplies.
    """
    parameters: frozenset[str] = frozenset(
        parameter.name for parameter in _keyword_parameters(selector)
    ) - _overlaid_field_names(input_serializer)
    pagination: list[str] = sorted(parameters & RESERVED_POST_FETCH_KEYS)
    if pagination:
        raise ImproperlyConfigured(
            f"{label}: the selector declares parameter(s) {pagination!r}, but `page` "
            "and `limit` belong to the read pipeline's pagination, which removes them "
            "from the arguments before the selector is called, so the parameter would "
            "never receive the caller's value. Rename the parameter."
        )
    shadowed: list[str] = sorted(parameters & {qp.name for qp in query_params})
    if shadowed:
        raise ImproperlyConfigured(
            f"{label}: the selector declares parameter(s) {shadowed!r} that the tool "
            "also declares as a QueryParam. A QueryParam's value is routed to "
            "request.query_params and removed from the arguments before the selector "
            "is called, so the parameter would never receive the caller's value. Read "
            "the value from request.query_params and drop the parameter, or drop the "
            "QueryParam so the argument reaches the selector."
        )


def validate_input_serializer_against_callable(
    *,
    label: str,
    input_serializer: type | None,
    callable_: Any,
    argument_binding: ArgumentBinding,
    spec_kwargs_provides: frozenset[str] = frozenset(),
    provides_instance: bool = False,
    provides_collection: bool = False,
    selector_url_kwargs: tuple[UrlKwarg, ...] = (),
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
    is_selector: bool = False,
) -> None:
    """Fail-fast at registration time when input shape doesn't match the callable.

    Runs two complementary checks:

    1. **Serializer fields reach the callable** — every declared
       ``input_serializer`` field must correspond to a named parameter on the
       callable, be a reserved-name exemption (pool seed / post-fetch key), or
       be absorbed by ``**kwargs`` / a ``data`` bundle parameter. Without it, a
       misspelt field name is silently dropped at dispatch.

    2. **Required callable parameters have a source** — every parameter with no
       default must come from something the MCP transport can produce: an
       ``input_serializer`` field, a pool seed the dispatch fills, a selector
       tool's ``UrlKwarg`` that every dispatched call carries, or an explicit
       ``spec_kwargs_provides`` opt-in declaring that ``spec.kwargs(...)``
       supplies it. Post-fetch keys (``page`` / ``limit``) are *not* sources —
       the pipeline consumes them before the callable runs.

       The opt-in is explicit because ``spec.kwargs`` output depends on the
       transport: a spec reused across DRF views and MCP tools sees populated
       URL path params in the first case and none in the second, so it may
       return ``None`` for keys it derives from them.

    ``selector_url_kwargs`` is passed by the selector adapter alone: drf-services
    spreads ``view.kwargs`` into a selector's pool, and into a service tool's
    target lookup but never into the service's own pool.

    ``is_selector`` is passed by the selector adapter too, because **a selector
    is never handed ``data`` or ``serializer``**: drf-services' selector dispatch
    seeds neither and strips both from the spread, under every binding. So
    neither counts as a source for a selector, and a selector tool declaring an
    ``input_serializer`` under ``BUNDLE`` is refused outright, since that binding
    spreads none of the validated fields either and the payload has no way to
    reach the selector. ``_validate_data_only``'s demand for ``data``,
    ``serializer`` or ``**kwargs`` under ``BUNDLE`` is a service's rule.

    ``input_serializer=None`` skips check (1) but check (2) still runs against
    the pool-seed and opt-in sources. ``callable_=None`` short-circuits
    everything — the per-adapter ``selector=None`` / ``service=None`` guards
    cover that with a more specific error.
    """
    if callable_ is None:
        return

    sig = _resolve_signature(callable_)
    if sig is None:  # pragma: no cover - paired with _resolve_signature's except branch
        # Builtin / C-extension callables expose no signature, so the check
        # cannot fire; falling through beats raising on something the framework
        # cannot introspect.
        return

    if argument_binding is ArgumentBinding.BUNDLE:
        if input_serializer is not None:
            if is_selector:
                # Before ``_validate_data_only``, which a ``data`` or
                # ``**kwargs`` selector passes, and then ran with no payload
                # (``test_a_bundled_selector_with_an_input_serializer_is_refused``).
                _refuse_bundled_selector_input(label)
            _validate_data_only(label, sig)
    else:
        if input_serializer is not None:
            _validate_merge_or_replace(label, sig, input_serializer, is_selector=is_selector)

    _validate_required_params_have_sources(
        label=label,
        sig=sig,
        input_serializer=input_serializer,
        argument_binding=argument_binding,
        spec_kwargs_provides=spec_kwargs_provides,
        provides_instance=provides_instance,
        provides_collection=provides_collection,
        selector_url_kwargs=selector_url_kwargs,
        pool_seeds=pool_seeds,
        is_selector=is_selector,
    )


def _refuse_bundled_selector_input(label: str) -> None:
    raise ImproperlyConfigured(
        f"{label}: argument_binding=BUNDLE on a selector tool with an input_serializer "
        "leaves the validated payload no way to reach the selector. A selector is never "
        "handed `data` or `serializer`, and BUNDLE spreads none of the validated fields, "
        "so every call would validate the arguments and then drop them. Use a spreading "
        "argument_binding (SPREAD_AUTHOR_WINS, the selector default) and take the fields "
        "as parameters, or drop the input_serializer."
    )


def _resolve_signature(callable_: Any) -> inspect.Signature | None:
    """Best-effort ``inspect.signature`` that tolerates exotic callables."""
    try:
        return inspect.signature(callable_)
    except (TypeError, ValueError):  # pragma: no cover - defensive fallback
        return None


def _overlaid_field_names(input_serializer: type | None) -> frozenset[str]:
    """The names a selector tool's validated input lays back with the caller's value.

    Selector dispatch overlays the validated values on the arguments in both of
    the shapes they arrive in (``handlers.selector_tool_dispatch._validated_values``):
    a plain DRF ``Serializer``'s ``dict``, and the dataclass instance a bare
    ``@dataclass`` or a ``DataclassSerializer`` validates into. So every shape
    the adapter admits can lay a name back, and the names that carry the
    caller's value are the same for all three: the serializer's fields, as
    built for the call (a bare dataclass is wrapped in a ``DataclassSerializer``
    there too), that are

    - not ``read_only``: DRF keeps the field out of the validated values
      (``read-only-field`` of
      ``test_a_field_whose_value_is_not_laid_back_exempts_nothing``), and a
      dataclass field so declared is laid back as its default, never as what
      the caller sent (``read-only-dataclass-field``);
    - bound to their own name: a field with ``source="number"`` puts its value
      under ``number``, and ``source="*"`` merges it, so neither lays back the
      name the field is declared under (``source-elsewhere``).

    The dataclass shapes were once left out, when dispatch overlaid only a
    ``dict``; they are held by
    ``test_a_pagination_named_parameter_a_dataclass_input_declares_is_allowed``.
    ``None`` is a tool with no ``input_serializer``, which every such
    registration passes through
    (``test_a_defaulted_pagination_named_parameter_is_refused``). Anything else
    was refused by ``validate_serializer_shapes`` before this runs.
    """
    if input_serializer is None:
        return frozenset()
    serializer: Any = (
        DataclassSerializer(dataclass=input_serializer)
        if dataclasses.is_dataclass(input_serializer)
        else input_serializer()
    )
    return frozenset(
        name
        for name, field in serializer.fields.items()
        if not field.read_only and field.source == name
    )


def _keyword_parameters(callable_: Any) -> list[inspect.Parameter]:
    """The parameters a keyword pool can fill, or none for an exotic callable.

    A ``**kwargs`` catch-all is not one: no argument is bound to its own name
    (``test_a_catch_alls_own_name_is_not_a_parameter_name``).
    """
    sig = _resolve_signature(callable_)
    parameters = sig.parameters.values() if sig is not None else ()
    return [
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    ]


def _validate_data_only(label: str, sig: inspect.Signature) -> None:
    if _accepts_var_keyword(sig):
        return
    if "data" in sig.parameters:
        return
    if "serializer" in sig.parameters:
        # The bound, validated serializer is itself a pool seed: a callable that
        # owns persistence via ``serializer.save()`` receives the payload
        # through it and needs no ``data`` parameter.
        return
    raise ImproperlyConfigured(
        f"{label}: argument_binding=BUNDLE requires the callable to declare a "
        "`data` parameter (or `serializer`, or accept `**kwargs`) — the validated "
        "input payload is forwarded under those names. The callable declares "
        "none of them, so the payload would be silently dropped at dispatch time."
    )


def _validate_merge_or_replace(
    label: str, sig: inspect.Signature, input_serializer: type, *, is_selector: bool
) -> None:
    if _accepts_var_keyword(sig):
        return
    declared_params: frozenset[str] = frozenset(
        name
        for name, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    )
    # A service declaring ``data`` receives the whole validated payload under
    # that name, so its fields need not map to individual parameters — a
    # deliberate spread-mode pattern (``def fn(*, data, request)``). A selector
    # is never handed ``data``, so one declaring it (with a default, or the
    # source check refuses it) still drops every field it does not take
    # (``test_a_selectors_data_parameter_does_not_take_the_fields_it_leaves_out``).
    if "data" in declared_params and not is_selector:
        return
    fields: frozenset[str] = frozenset(_serializer_field_names(input_serializer))
    exempt: frozenset[str] = RESERVED_POOL_SEEDS | RESERVED_POST_FETCH_KEYS
    unmatched: set[str] = set(fields - declared_params - exempt)
    if unmatched:
        raise ImproperlyConfigured(
            f"{label}: input_serializer declares field(s) {sorted(unmatched)!r} "
            "that the dispatched callable does not accept as parameters and the "
            "callable has no `**kwargs` catch-all (nor a `data` parameter to "
            "receive the validated payload as a bundle). Those fields would be "
            "silently dropped at dispatch time. Add the parameter(s) to the "
            "callable signature, declare `**kwargs` / `data`, or remove the "
            "field(s) from the serializer."
            f"{_SELECTOR_HINT if is_selector else ''}"
        )


# ``data`` is the one missing name the generic remedy above misdirects: it is not
# a serializer field to add, it is the validated payload of one.
_DATA_HINT = (
    " Nothing fills `data` without an input_serializer: declare one, give `data` a "
    "default, or take the arguments as individual parameters under a spreading "
    "argument_binding."
)

# The two seeds a service is handed beside an ``input_serializer`` and a selector
# never is: drf-services' selector dispatch seeds neither and strips both from
# the spread.
_NEVER_HANDED_TO_A_SELECTOR: frozenset[str] = frozenset({"data", "serializer"})

# For a selector, declaring an ``input_serializer`` fills neither name, so the
# service remedy above would misdirect it.
_SELECTOR_HINT = (
    " A selector is never handed `data` or `serializer`, with or without an "
    "input_serializer: drf-services' selector dispatch seeds neither and strips both "
    "from the arguments. Take the validated fields as individual parameters under a "
    "spreading argument_binding, or give the parameter a default."
)


def _validate_required_params_have_sources(
    *,
    label: str,
    sig: inspect.Signature,
    input_serializer: type | None,
    argument_binding: ArgumentBinding,
    spec_kwargs_provides: frozenset[str],
    provides_instance: bool,
    provides_collection: bool,
    selector_url_kwargs: tuple[UrlKwarg, ...],
    pool_seeds: PoolSeeds,
    is_selector: bool,
) -> None:
    """Every required callable parameter must have a static source.

    Sources, in priority order:

    - **Pool seeds.** ``request`` / ``user`` / ``progress`` always;
      ``instance`` and ``collection`` only when the spec resolves one, and
      ``serializer`` and ``data`` only for a service, when an
      ``input_serializer`` is declared. Never for a selector, which drf-services
      hands neither, nor under a serializer field of either name, since the
      spread strips both (``test_a_spreading_selector_requiring_data_or_serializer_is_refused``,
      whose ``a-field-named-data`` case holds the field half).
      Every name the server's ``pool_seeds=`` registers, always: dispatch
      resolves each into every pool, so a callable declaring one is satisfiable
      on every call.
    - **``input_serializer`` fields**, in the spread modes only, where the
      validated dict is spread into the pool. Under ``BUNDLE`` the fields ride
      inside ``data`` and their names never reach the callable as kwargs.
    - **``selector_url_kwargs``** that are ``required`` or declare a default: a
      call omitting a required one is refused before dispatch, and a default is
      seeded when the call omits it, so every call that reaches the selector
      carries the name in ``view.kwargs``, which dispatch spreads into the pool.
      A ``UrlKwarg`` with neither reaches the pool only when the caller sends
      it, and so is no source. The ``or`` is one branch arc, held by
      ``test_a_required_url_kwarg_fills_a_required_selector_parameter``,
      ``test_a_defaulted_url_kwarg_fills_a_required_selector_parameter`` and
      ``test_a_url_kwarg_a_call_may_omit_is_no_source``.
    - **``spec_kwargs_provides``** — the explicit opt-in that
      ``spec.kwargs(view, request)`` supplies these names at dispatch.

    ``data`` is no source without an ``input_serializer`` under any binding.
    drf-services seeds it from a validated serializer, or from the extras an
    ``UnknownArguments.PASSTHROUGH`` policy forwards: none at all under
    ``BUNDLE``, where this transport forwards no extras, and under a spreading
    binding only the arguments the call happened to carry, so a call carrying
    none leaves it unfilled. Held by
    ``test_a_bundled_service_requiring_data_without_an_input_serializer_is_refused``
    and ``test_a_trust_mode_service_requiring_data_is_refused``.

    **A required positional-only parameter is refused first**, ``**kwargs`` or
    not: dispatch passes every argument by keyword, so nothing fills it and
    every call raises ``TypeError``, while a catch-all takes the argument of its
    name into ``kwargs`` rather than into the slot
    (``test_a_required_positional_only_parameter_is_refused``). One with a
    default runs on it, so it registers
    (``test_a_defaulted_positional_only_parameter_registers_and_runs_on_its_default``).
    Refused for a service as well as a selector, since the failure is the same.

    ``**kwargs`` callables are otherwise exempt: every required name is
    structurally satisfiable. With ``input_serializer=None`` a spreading binding is in trust
    mode — the client's raw ``arguments`` are spread verbatim, so there is no
    static contract and every required parameter counts as one the caller
    supplies, **except a reserved pool seed**: drf-services strips every
    ``pool_seeds.reserved`` name from the spread, so a caller cannot supply
    ``instance`` or ``serializer`` and only the sources above can. Held by
    ``test_trust_mode_does_not_count_a_reserved_seed_as_the_callers`` and the
    spreading cases of ``test_an_instance_lookup_seeds_no_collection``.
    """
    positional_only: list[str] = sorted(
        name
        for name, param in sig.parameters.items()
        if param.kind is inspect.Parameter.POSITIONAL_ONLY
        and param.default is inspect.Parameter.empty
    )
    if positional_only:
        raise ImproperlyConfigured(
            f"{label}: callable declares positional-only parameter(s) {positional_only!r} "
            "with no default. Dispatch passes every argument by keyword, so nothing on "
            "any transport can fill it and every call would raise TypeError. Drop the `/` "
            "so the parameter can be passed by keyword, or give it a default."
        )
    if _accepts_var_keyword(sig):
        return
    # Only a parameter a keyword can fill is counted: a ``*args`` has no default
    # and needs no source, since dispatch binds by keyword and leaves it empty
    # (``test_a_var_positional_parameter_needs_no_source``).
    required_params: frozenset[str] = frozenset(
        name
        for name, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and param.default is inspect.Parameter.empty
    )
    # ``progress`` is an unconditional seed even though most requests carry
    # nowhere to send it: drf-services substitutes its no-op reporter, so the
    # parameter is always satisfiable and refusing to register a service that
    # declares one would refuse a service that runs perfectly well.
    sources: set[str] = {"request", "user", "progress"}
    if provides_instance:
        sources.add("instance")
    if provides_collection:
        sources.add("collection")
    if input_serializer is not None and not is_selector:
        sources.add("serializer")
        sources.add("data")
    sources.update(spec_kwargs_provides)
    sources.update(pool_seeds.names)
    # ``declares_default`` is the test the URL-kwarg split seeds a default by, so
    # a ``default=None`` that the split leaves unseeded is no source here either.
    sources.update(
        url_kwarg.name
        for url_kwarg in selector_url_kwargs
        if url_kwarg.required or declares_default(url_kwarg.default)
    )
    if argument_binding is not ArgumentBinding.BUNDLE:
        if input_serializer is not None:
            fields: frozenset[str] = frozenset(_serializer_field_names(input_serializer))
            if is_selector:
                fields -= _NEVER_HANDED_TO_A_SELECTOR
            sources.update(fields)
        else:
            # Trust mode: raw ``arguments`` are spread verbatim, so the client
            # can in principle supply any name the callable declares, other than
            # the reserved seeds dispatch strips from that spread.
            sources.update(required_params - pool_seeds.reserved)
    missing: set[str] = set(required_params) - sources
    if missing:
        sources_human = ", ".join(sorted(sources)) or "(none)"
        raise ImproperlyConfigured(
            f"{label}: callable declares required parameter(s) {sorted(missing)!r} "
            "with no static source on the MCP transport. Available sources are: "
            f"{sources_human}. Add the parameter(s) to ``input_serializer``, give "
            "them defaults on the callable, accept ``**kwargs``, or — if "
            "``spec.kwargs(...)`` is intentionally supplying them — pass "
            "``spec_kwargs_provides=(...)`` at registration to acknowledge that "
            "contract. (``spec.kwargs`` output is not assumed because its "
            "behaviour can differ between DRF API-view and MCP transports.)"
            f"{_missing_seed_hint(missing, is_selector=is_selector)}"
        )


def _missing_seed_hint(missing: set[str], *, is_selector: bool) -> str:
    """The remedy for a missing ``data`` / ``serializer``, which the generic one misdirects.

    A selector's when it misses either, since declaring an ``input_serializer``
    fills neither for a selector; held on both sides by
    ``test_a_spreading_selector_requiring_data_or_serializer_is_refused`` and
    ``test_the_selector_remedy_accompanies_only_data_or_serializer``. A service's
    when it misses ``data``, as before.
    """
    if is_selector and missing & _NEVER_HANDED_TO_A_SELECTOR:
        return _SELECTOR_HINT
    if "data" in missing:
        return _DATA_HINT
    return ""


def merge_tool_annotations(
    explicit: dict[str, Any] | None, *, read_only: bool, idempotent: bool | None = None
) -> dict[str, Any]:
    """Auto-derive a tool's MCP ``ToolAnnotations``, explicit hints winning.

    A tool's mutation profile is known from its kind, so the standard MCP hints
    are stamped here rather than hand-set downstream:

    - ``read_only=True`` (selector tools, and chains whose every step is a
      selector) → ``{"readOnlyHint": True}``. ``destructiveHint`` /
      ``idempotentHint`` are deliberately *not* emitted — the MCP spec defines
      them as meaningful only when ``readOnlyHint`` is false — so ``idempotent``
      is ignored here.
    - ``read_only=False`` (service tools, and chains with any service step) →
      ``{"readOnlyHint": False, "destructiveHint": True}``. A mutation is
      destructive by default.

    ``idempotent`` is a service spec's declared ``ServiceSpec.idempotent``,
    which a service tool passes through. A declared ``True`` or ``False``
    becomes ``idempotentHint`` on a mutation; ``None`` (undeclared, the
    default) leaves the hint absent, and a client reads its absence as
    ``false``, the MCP default. drf-services keeps ``None`` apart from
    ``False`` so that a transport publishing the fact never turns silence into
    a claim, which is why a declared ``False`` is published rather than
    dropped. A chain passes nothing: being idempotent is a property of a whole
    operation, and two idempotent steps in sequence need not be one.

    Both conjuncts of the derivation are held by a test, because a deleted
    one leaves branch coverage at 100%:
    ``test_a_read_only_tool_never_derives_the_idempotent_hint`` holds
    ``not read_only``, and ``test_an_undeclared_service_spec_leaves_the_hint_absent``
    holds ``idempotent is not None``.

    Any hint supplied at registration via ``annotations=`` overrides the derived
    default: a non-destructive mutation passes
    ``annotations={"destructiveHint": False}``, an undeclared spec can still
    add ``{"idempotentHint": True}``, and either kind can set ``title`` /
    ``openWorldHint``. The result is stored on the binding, so it is the single
    source of truth for ``tools/list`` and for anything reading
    ``binding.annotations``.
    """
    derived: dict[str, Any] = (
        {"readOnlyHint": True} if read_only else {"readOnlyHint": False, "destructiveHint": True}
    )
    if not read_only and idempotent is not None:
        derived["idempotentHint"] = idempotent
    return {**derived, **(explicit or {})}


def merge_meta(*pieces: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge ``_meta`` contributions into one bundle, later wins.

    ``_meta`` is the base protocol's open extension namespace, where each
    extension owns a top-level key and several sources may contribute at once:
    the ``meta=`` a consumer passes at registration plus whatever a framework
    feature derives. Combining them here means a later feature injects its key
    by adding one argument at the adapter call site.

    Semantics, deliberately narrow:

    - **Shallow**, one level deep. A later piece replaces an earlier one's value
      for the same top-level key outright rather than deep-merging into it —
      extension keys are opaque bundles owned by one extension, and splicing two
      together produces a shape neither owner declared.
    - **Later wins**, so call sites read as precedence order: framework piece
      first and the consumer's ``meta=`` last for "consumer overrides", the
      reverse when the framework must win.
    - ``None`` and empty pieces are skipped, and the result is always a new dict,
      so no piece is mutated and no caller shares a mutable default.
    """
    merged: dict[str, Any] = {}
    for piece in pieces:
        if piece:
            merged.update(piece)
    return merged


def _accepts_var_keyword(sig: inspect.Signature) -> bool:
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _serializer_field_names(input_serializer: type) -> Iterable[str]:
    """Best-effort field-name extraction for the kinds of inputs MCP accepts.

    Supports the same shapes ``build_input_schema`` does: a DRF
    ``Serializer`` subclass (via ``_declared_fields``) or a bare ``@dataclass``
    (via ``dataclasses.fields``). Anything else yields nothing to validate
    against, which is preferable to a false positive.
    """
    if isinstance(input_serializer, type) and issubclass(
        input_serializer, drf_serializers.Serializer
    ):
        return tuple(input_serializer._declared_fields.keys())
    if isinstance(input_serializer, type) and dataclasses.is_dataclass(input_serializer):
        return tuple(f.name for f in dataclasses.fields(input_serializer))
    return ()


__all__ = [
    "merge_meta",
    "merge_tool_annotations",
    "validate_input_serializer_against_callable",
    "validate_query_params",
    "validate_selector_parameter_names",
    "validate_serializer_shapes",
    "validate_url_kwargs",
]
