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
from rest_framework_services import provider_keys
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.validate_channel_names import validate_channel_names

from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
)
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.service_tool_schema import (
    build_service_tool_input_schema,
    declared_service_input,
)
from rest_framework_mcp.schema.utils import (
    declares_default,
    laid_back_inputs,
    selector_inputs,
    selector_tool_inputs,
    target_lookup,
)


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
    while carrying a ``default``. Colliding with an input is not refused here:
    a selector's parameter and a target lookup's receive the value through
    ``view.kwargs``, so the name is how a route capture reaches the callable
    that reads it. A service tool's input of the same name never receives it,
    which ``validate_url_kwarg_inputs`` refuses.

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
    input_serializer: type | None,
    kind: SelectorKind,
) -> None:
    """Fail-fast on a ``page`` / ``limit`` selector parameter the transport takes away.

    The sibling of ``validate_url_kwargs`` / ``validate_query_params``, from the
    selector's side of the same collision. Those refuse a *channel* named after
    a name the read pipeline owns; this refuses a *selector parameter* named
    ``page`` or ``limit`` (``RESERVED_POST_FETCH_KEYS``) on a ``LIST`` selector
    tool, whose dispatch strips both from the selector's arguments whether or
    not the tool paginates. The parameter registers, is advertised, and then
    never receives what the caller sent: a required one is answered "This field
    is required." for an argument the call carried, and a defaulted one runs on
    its default whatever the call asked for, so it is refused with a default or
    without. A selector parameter named like a ``QueryParam`` is the same
    collision on another name, refused for both tool kinds by
    ``validate_query_param_inputs``.

    **Only on a ``LIST`` tool.** A ``RETRIEVE`` tool cannot pair with
    ``paginate``, so nothing on any route takes either name from its selector,
    which receives the caller's value as it receives any other; the Pydantic-AI
    ``SpecToolset`` reserves the names on a list tool alone, so one spec is a
    tool on both routes. The kind is held by
    ``test_a_retrieve_selectors_pagination_named_parameter_registers``.

    Not refused for a name the ``input_serializer`` lays back with the caller's
    value (``_overlaid_field_names``): dispatch overlays the validated values on
    the stripped arguments, so the selector does receive the caller's value
    under that name. The subtraction is held by
    ``test_a_pagination_named_parameter_the_input_serializer_declares_is_allowed``
    and ``test_a_pagination_named_parameter_a_dataclass_input_declares_is_allowed``,
    and its limit to the declared names by
    ``test_a_serializer_declaring_another_name_exempts_nothing``.

    A ``**kwargs`` catch-all names nothing, so there is nothing to refuse.
    """
    if kind is not SelectorKind.LIST:
        return
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


def validate_query_param_inputs(
    binding: ToolBinding | SelectorToolBinding,
    *,
    spec_kwargs_provides: frozenset[str] = frozenset(),
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> None:
    """Refuse a ``QueryParam`` named like an input the tool offers the caller.

    A ``QueryParam``'s value is popped from the arguments and routed to
    ``request.query_params``, so an input of the same name never receives the
    caller's value. The input registers, is advertised, and then fails or runs
    wrong on every call: a required target-lookup parameter was answered
    "Missing required argument(s)" for an argument the call carried, which a
    model resends until it runs out of retries, and a defaulted one resolved the
    row on its default whatever the caller asked for. So registration refuses
    it, for both tool kinds, as the Pydantic-AI ``SpecToolset`` refuses it.

    **The names checked are the ones the tool's ``inputSchema`` offers as the
    call's own input**, read through the reader that builds that schema, so the
    refusal and the schema cannot disagree about which names are the caller's:

    - **a selector tool**: the selector's parameters and ``filter_set`` fields,
      as ``selector_tool_inputs`` reflects them for the schema. Not the
      ``input_serializer``'s fields, which validate the arguments *before* the
      ``QueryParam`` split (``handlers.selector_tool_dispatch``), so the split
      takes nothing from them. A ``filter_set`` field is named as one
      (``test_a_filter_set_field_a_query_param_shadows_is_refused``): the
      FilterSet reads the stripped arguments, so it never applied the value.
    - **a service tool**: everything ``build_service_tool_input_schema``
      advertises, built without the ``QueryParam`` and ``UrlKwarg``
      declarations, because each is advertised under its own name and would
      hide the input it shadows (``_service_tool_inputs``, whose sets
      ``validate_url_kwarg_inputs`` reads too). That is the
      ``input_serializer``'s fields, which validate the arguments left
      once the split has run; the service's own parameters, where a spreading
      binding with no serializer advertises them; and the target lookup's
      parameters, which the schema merges in beside them
      (``test_a_lookup_parameter_a_query_param_takes_is_refused`` holds the
      lookup's names, ``test_a_serializer_field_a_query_param_shadows_is_refused``
      the fields, and ``test_a_spread_service_parameter_a_query_param_takes_is_refused``
      the service's own). A ``many=True`` item's fields are not arguments of
      the call, so the serializer's fields count only where the schema lists
      them at the top (``test_a_list_items_field_is_no_argument_a_query_param_takes``).

    A ``UrlKwarg``'s name cannot be a ``QueryParam``'s, which
    ``validate_query_params`` has already refused, so leaving the ``UrlKwarg``
    declarations out of the read changes nothing here. Read off the binding, so the ``query_params`` are the tool's
    effective ones, an ``agent_contract``'s included
    (``test_a_query_param_from_the_agent_contract_shadows_too``).

    What the schema does not offer the caller is exempt by construction: a key
    the server keeps from the call (``server_owned_keys``), which a service
    tool's schema leaves out of its target lookup's names
    (``test_a_server_owned_lookup_key_is_not_refused_but_a_plain_one_is``),
    and, under ``SPREAD_AUTHOR_WINS``, a name the selector's or a spread
    service's ``kwargs=`` provider fills. Three exemptions are made here,
    because the schema still offers the name while the caller's value reaches
    the reader by another way:

    - **a name the ``kwargs=`` provider declares it fills**, its
      ``provider_keys`` ``filled`` set, or that ``spec_kwargs_provides=``
      claims. The provider owns the parameter, and one reading
      ``request.query_params`` is the ordinary way to route a query parameter
      to a callable, so the caller's value is served. The schema still offers
      such a name under ``SPREAD_CALLER_WINS``, for a selector
      (``test_a_parameter_a_typed_provider_fills_is_served``) and for a spread
      service (``test_a_spread_service_parameter_its_provider_fills_is_served``),
      so a call is what each asserts. **Only for the callable that provider feeds**: a
      selector's own, and a spread service's own parameters, never a
      service's serializer fields or its target lookup's parameters, which
      read the arguments rather than the service's pool
      (``test_the_services_provider_exempts_no_lookup_parameter_or_field``).
    - **keeping a key the provider may decline**, and every key of a provider
      whose annotation does not say what it returns: on a call where it is not
      filled the caller's value is the only one, and the ``QueryParam`` took
      it (``test_a_key_the_provider_may_leave_to_the_caller_is_refused``).
    - **for a selector tool, a name its ``input_serializer`` lays back with the
      caller's value** (``_overlaid_field_names``), because the serializer read
      the arguments before the split and dispatch lays the validated values
      back over the stripped ones
      (``test_a_parameter_a_query_param_shadows_is_allowed_when_the_input_serializer_declares_it``
      on ``acall_tool``, ``test_a_name_the_input_serializer_lays_back_reaches_the_selector_on_tools_call``
      on ``tools/call``). Not for a service tool, whose serializer reads the
      stripped arguments (``test_a_serializer_field_a_query_param_shadows_is_refused``).
      ``call_tool`` does not run a selector tool's ``input_serializer`` at all,
      so on that route nothing lays the value back
      (``test_call_tool_leaves_a_query_params_value_to_request_query_params``).

    ``spec_kwargs_provides`` is passed by both adapters from the argument they
    were given, since a ``ToolBinding`` does not keep it.
    """
    declared: frozenset[str] = frozenset(query_param.name for query_param in binding.query_params)
    # Not a condition of the rule, which intersects with ``declared`` anyway: it
    # spares reading the schema at registration for the tools declaring none.
    if not declared:
        return
    filled = _provider_filled(binding, spec_kwargs_provides)
    groups: tuple[tuple[str, frozenset[str]], ...]
    if isinstance(binding, SelectorToolBinding):
        offered = frozenset(
            selector_tool_inputs(binding, pool_seeds=pool_seeds)[0].get("properties", {})
        )
        taken = (offered - filled - _overlaid_field_names(binding.input_serializer)) & declared
        # The reflection lists a ``filter_set``'s fields beside the parameters,
        # and the FilterSet reads the stripped arguments as the selector does.
        filtering = taken & frozenset(getattr(binding.spec.filter_set, "base_filters", ()))
        kind, reader, target = "selector tool", "the selector is called", "the selector"
        groups = (
            ("the selector declares parameter(s)", taken - filtering),
            ("the selector's filter_set declares field(s)", filtering),
        )
    else:
        fields, own, looked_up = _service_tool_inputs(binding, filled=filled, pool_seeds=pool_seeds)
        kind, target = "service tool", "the spec"
        reader = (
            "the input_serializer validates them and the service and its target lookup are called"
        )
        groups = (
            ("the service's input_serializer declares field(s)", fields & declared),
            ("the service declares parameter(s)", own & declared),
            ("the service's target lookup declares parameter(s)", looked_up & declared),
        )
    parts = [f"{subject} {sorted(names)!r}" for subject, names in groups if names]
    if parts:
        raise ImproperlyConfigured(
            f"{kind} {binding.name!r}: {' and '.join(parts)} that the tool also declares "
            "as a QueryParam. A QueryParam's value is routed to request.query_params and "
            f"removed from the arguments before {reader}, so the input would never "
            "receive the caller's value. Fill the parameter from request.query_params "
            "with a kwargs= provider whose TypedDict declares it (a target lookup's "
            "parameter, on the lookup's own SelectorSpec), or read the value there in the "
            f"callable and drop the input, or drop the QueryParam so the argument reaches "
            f"{target}."
        )


def validate_url_kwarg_inputs(
    binding: ToolBinding,
    *,
    spec_kwargs_provides: frozenset[str] = frozenset(),
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> None:
    """Refuse a ``UrlKwarg`` named like a service tool's input that never receives it.

    A ``UrlKwarg``'s value is popped from the arguments and seeded into
    ``view.kwargs``. drf-services hands ``view.kwargs`` to the spec's target
    lookup and to its ``kwargs=`` provider, and never to the service, which is
    handed neither ``view`` nor the route. So two inputs of the same name
    register, are advertised, and never receive the caller's value:

    - **a spread service's own parameter**, where a spreading binding with no
      ``input_serializer`` advertises it: a required one was answered
      "Missing required argument(s): 'project_pk'" on every call, and a
      defaulted one ran on its default, so the call archived row 0
      (``test_a_spread_service_parameter_a_url_kwarg_takes_is_refused``);
    - **an ``input_serializer`` field**, which validates the arguments left once
      the split has run, so a required one was answered "This field is
      required." for an argument the call carried
      (``test_a_serializer_field_a_url_kwarg_takes_is_refused``).

    The names are the ``QueryParam`` refusal's own sets
    (``_service_tool_inputs``), so the two refusals read the schema the same way.
    What does receive the value is not refused: **a target lookup's parameter**,
    since drf-services spreads ``view.kwargs`` into the lookup's pool
    (``test_a_target_lookup_parameter_a_url_kwarg_takes_is_served``), unless a
    spreading service declares the same name, which is still handed nothing
    (``test_a_parameter_the_service_and_its_lookup_both_take_is_refused``), and **a
    parameter the service's own ``kwargs=`` provider declares it fills**, the
    ``own`` set's subtraction of ``filled``, because a provider is handed
    ``view`` and fills the parameter from the route
    (``test_a_parameter_the_services_provider_fills_from_the_route_is_served``,
    whose ``SPREAD_CALLER_WINS`` row holds the subtraction: under
    ``SPREAD_AUTHOR_WINS`` the schema does not offer such a name at all). A
    selector tool's parameter is served too, through the selector's pool, so a
    selector tool is not checked here. A ``UrlKwarg`` no input of the service
    takes registers, since the sets are read with the declarations left out
    (``test_a_url_kwarg_no_input_takes_registers``).
    """
    declared: frozenset[str] = frozenset(url_kwarg.name for url_kwarg in binding.url_kwargs)
    # Not a condition of the rule, which intersects with ``declared`` anyway: it
    # spares reading the schema at registration for the tools declaring none.
    if not declared:
        return
    filled = _provider_filled(binding, spec_kwargs_provides)
    fields, own, looked_up = _service_tool_inputs(binding, filled=filled, pool_seeds=pool_seeds)
    # ``own`` counts a name the target lookup also takes as the lookup's, which
    # is right for the schema and for a ``QueryParam``, whose value neither
    # receives. A ``UrlKwarg``'s value does reach the lookup, so a spreading
    # service declaring the same name is the one left without it: a required
    # one answered "Missing required argument(s)" and a defaulted one ran on
    # its default while the lookup resolved the row the call named
    # (``test_a_parameter_the_service_and_its_lookup_both_take_is_refused``).
    # Read off the service's own declared input, before the lookup is merged
    # in, so a lookup-only name stays served
    # (``test_a_target_lookup_parameter_a_url_kwarg_takes_is_served``).
    shared = (
        (
            looked_up
            & frozenset(
                declared_service_input(
                    dataclasses.replace(binding, query_params=(), url_kwargs=()),
                    pool_seeds=pool_seeds,
                ).get("properties", {})
            )
        )
        - fields
        - filled
    )
    groups = (
        ("the service's input_serializer declares field(s)", fields & declared),
        ("the service declares parameter(s)", (own | shared) & declared),
    )
    parts = [f"{subject} {sorted(names)!r}" for subject, names in groups if names]
    if parts:
        raise ImproperlyConfigured(
            f"service tool {binding.name!r}: {' and '.join(parts)} that the tool also "
            "declares as a UrlKwarg. A UrlKwarg's value is routed to view.kwargs and "
            "removed from the arguments before the input_serializer validates them and "
            "the service is called, and drf-services hands view.kwargs to the spec's "
            "target lookup and kwargs= provider, never to the service, so the input "
            "would never receive the caller's value. Fill the parameter from view.kwargs "
            "with a kwargs= provider whose TypedDict declares it, or take the value as a "
            "parameter of the spec's target lookup and drop the input, or drop the "
            "UrlKwarg so the argument reaches the spec."
        )


def _provider_filled(
    binding: ToolBinding | SelectorToolBinding, spec_kwargs_provides: frozenset[str]
) -> frozenset[str]:
    """The names the spec's ``kwargs=`` provider fills for certain, and the ones claimed for it.

    ``None`` from ``provider_keys`` is a provider whose keys cannot be read,
    which fills nothing for certain; ``declinable`` stays out on purpose, since
    on a call where such a key is not filled the caller's value is the only one.
    """
    keys = provider_keys(binding.spec.kwargs)
    return (keys.filled if keys is not None else frozenset()) | spec_kwargs_provides


def _service_tool_inputs(
    binding: ToolBinding, *, filled: frozenset[str], pool_seeds: PoolSeeds
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Whose each name a service tool's ``inputSchema`` offers is: its fields, its own, its lookup's.

    Read off everything ``build_service_tool_input_schema`` advertises, built
    without the ``QueryParam`` and ``UrlKwarg`` declarations, because each is
    advertised under its own name and would hide the input it shadows, or, for a
    ``UrlKwarg`` no input takes, count its own name as the service's
    (``test_a_url_kwarg_no_input_takes_registers``). Leaving the ``UrlKwarg``
    declarations out also keeps a lookup parameter one of them defaults in the
    lookup's group, since the reflection would otherwise drop it as filled and
    the name would fall to the service's own (the ``defaulted`` rows of
    ``test_a_target_lookup_parameter_a_url_kwarg_takes_is_served``).

    The three groups are the ``input_serializer``'s fields, which validate the
    arguments left once the split has run; the service's own parameters, where
    a spreading binding with no serializer advertises them, less what its
    ``kwargs=`` provider ``filled``; and the target lookup's parameters, which
    the schema merges in beside them. Shared by ``validate_query_param_inputs``
    and ``validate_url_kwarg_inputs``, so the two refusals cannot disagree about
    whose a name is.
    """
    advertised = frozenset(
        build_service_tool_input_schema(
            dataclasses.replace(binding, query_params=(), url_kwargs=()), pool_seeds=pool_seeds
        ).get("properties", {})
    )
    # The two reads the schema builder merges, made as it makes them. Each is
    # narrowed to what the builder advertised, so they only say whose a name
    # is: a ``many=True`` item's fields travel inside the list, and a lookup key
    # the server owns is left out of the schema. A name both the serializer and
    # the lookup take is the serializer's, as its property is
    # (``test_a_name_the_serializer_and_the_lookup_both_take_is_named_as_the_serializers``).
    fields = advertised & frozenset(
        build_input_schema(binding.spec.input_serializer).get("properties", {})
    )
    lookup = target_lookup(binding.spec)
    reflected: dict[str, Any] = (
        selector_inputs(lookup, url_kwargs=(), pool_seeds=pool_seeds)[0]
        if lookup is not None
        else {}
    )
    looked_up = (advertised & frozenset(reflected.get("properties", {}))) - fields
    # What is left is a spreading service's own parameters, the only names its
    # ``kwargs=`` provider feeds.
    own = advertised - fields - looked_up - filled
    return fields, own, looked_up


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
       on a ``LIST`` tool the pipeline consumes them before the callable runs,
       and on any other they are arguments like the rest.

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

    The first half of ``schema.utils.laid_back_inputs``, the one reader of what
    an input lays back, which states the rule and names the test holding each
    of its conditions. Read here for the pagination exemption above and by
    ``call_tool``'s strip of ``page`` / ``limit`` (``handlers.call_spec_tool``).
    """
    return laid_back_inputs(input_serializer)[0]


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
      inside ``data`` and their names never reach the callable as kwargs. For
      a selector, also every name the input lays back
      (``schema.utils.laid_back_inputs``), which the schema reads too: a field
      a ``DataclassSerializer`` generates, and a dataclass field filled by its
      own default. Its declared fields alone missed both, so a selector
      requiring one was refused although dispatch fills it on every call
      (``test_registration_the_schema_and_dispatch_agree_on_what_an_input_lays_back``).
      Not for a service, which is never handed a dataclass input spread:
      drf-services passes the instance a bare ``@dataclass`` or a
      ``DataclassSerializer`` validates into as ``data`` alone, so **no field of
      a dataclass input is a service's source**, generated
      (``test_a_service_counts_no_field_a_dataclass_serializer_generates``) or
      declared. Counting the declared ones registered a service that raised
      ``TypeError`` on every call; each shape is a case of
      ``test_a_service_counts_no_field_of_a_dataclass_input``, which holds the
      two halves of ``_validates_into_a_dataclass``.
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
                overlaid, fills = laid_back_inputs(input_serializer)
                fields = (fields | overlaid | frozenset(fills)) - _NEVER_HANDED_TO_A_SELECTOR
            elif _validates_into_a_dataclass(input_serializer):
                # drf-services hands a service the instance as ``data`` alone
                # and spreads none of its fields.
                fields = frozenset()
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
            f"{_missing_seed_hint(missing, is_selector=is_selector, input_serializer=input_serializer)}"
        )


def _missing_seed_hint(
    missing: set[str], *, is_selector: bool, input_serializer: type | None
) -> str:
    """The remedy where the generic one misdirects: a missing seed, or a dataclass input.

    A selector's when it misses ``data`` or ``serializer``, since declaring an
    ``input_serializer`` fills neither for a selector; held on both sides by
    ``test_a_spreading_selector_requiring_data_or_serializer_is_refused`` and
    ``test_the_selector_remedy_accompanies_only_data_or_serializer``. A
    service's when it misses ``data``, as before. And a service's whose input
    validates into a dataclass, where adding the parameter to the
    ``input_serializer`` is the one remedy that cannot work. The service half
    is held by ``test_a_service_counts_no_field_of_a_dataclass_input``, and
    ``not is_selector`` by
    ``test_a_selector_with_a_dataclass_input_is_not_told_its_fields_go_unspread``.
    """
    if is_selector and missing & _NEVER_HANDED_TO_A_SELECTOR:
        return _SELECTOR_HINT
    if "data" in missing:
        return _DATA_HINT
    if not is_selector and _validates_into_a_dataclass(input_serializer):
        return _DATACLASS_INPUT_HINT
    return ""


# The remedy above names the one place a dataclass input's fields cannot help.
_DATACLASS_INPUT_HINT = (
    " The input_serializer validates into a dataclass instance, which drf-services "
    "hands a service as `data` alone and never spreads into its parameters, so no "
    "field of it fills one: take `data` and read the field off the instance."
)


def _validates_into_a_dataclass(input_serializer: type | None) -> bool:
    """Whether ``input_serializer`` validates into a dataclass instance rather than a ``dict``.

    A bare ``@dataclass``, which dispatch wraps in a ``DataclassSerializer``, or
    a ``DataclassSerializer`` itself. The ``or`` is one branch arc, so each half
    is a case of ``test_a_service_counts_no_field_of_a_dataclass_input``.
    """
    return _is_dataclass_type(input_serializer) or _is_class_of(
        input_serializer, DataclassSerializer
    )


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
    "validate_query_param_inputs",
    "validate_query_params",
    "validate_selector_parameter_names",
    "validate_serializer_shapes",
    "validate_url_kwarg_inputs",
    "validate_url_kwargs",
]
