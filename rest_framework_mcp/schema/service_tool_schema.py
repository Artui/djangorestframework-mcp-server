from __future__ import annotations

from typing import Any

from rest_framework_services import provider_keys, server_owned_keys, spec_to_json_schema
from rest_framework_services.types.pool_seeds import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.schema.input_schema import build_input_schema
from rest_framework_mcp.schema.utils import selector_inputs, target_lookup


def build_service_tool_input_schema(
    binding: ToolBinding, *, pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS
) -> dict[str, Any]:
    """Build the JSON Schema for a service tool's ``inputSchema``.

    The properties are the arguments drf-services' dispatch admits for the spec
    under the binding's ``argument_binding`` (its ``declared_input_keys``), so a
    schema ``tools/list`` closes lists every name a call may carry:

    - With an ``input_serializer``, its schema verbatim (``spec.partial is True``
      drops ``required``, mirroring the dispatch-time partial-validation
      contract).
    - Without one, drf-services' ``spec_to_json_schema`` given the binding. Under
      ``BUNDLE`` nothing reads the caller's input but the target lookup, so the
      service adds no property; under a ``SPREAD_*`` binding the service's own
      parameters are its input and are listed, less every name the server
      fills: drf-services' seeds, the ones this server registers, and every
      key a callable in the call marks ``NotClientInput``. A parameter without a
      default is required, unless the spec's ``kwargs=`` provider may fill it,
      as drf-services' ``provider_keys`` reads the provider; one whose keys
      cannot be read may fill any. A bare ``**kwargs`` lists what the service
      names, and the set it opens is left to ``additionalProperties``. That
      reflection merges ``metadata["json_schema"]["input"]`` on top, as the
      ``many=True`` shape below does; the serializer's shape above does not
      read it.

    Registered [`UrlKwarg`][rest_framework_mcp.registry.types.url_kwarg.UrlKwarg]
    and ``QueryParam`` properties are merged over either. A
    ``UrlKwarg(required=True)`` joins ``required`` and ``spec.partial`` does
    **not** relax it: partial validation is about the *payload* the serializer
    checks, and a URL kwarg is routed to the off-HTTP ``view.kwargs`` at
    dispatch rather than into that payload.

    A ``many=True`` spec validates a list, and ``arguments`` is always an object,
    so the list travels under the one argument ``spec.many_argument`` names. That
    shape is drf-services' ``spec_to_json_schema`` taken whole rather than wrapped
    around ``build_input_schema`` here: the ``minItems`` / ``maxItems`` a list
    serializer declares in ``many_init`` are only visible to the reflection that
    builds that list serializer, and a wrapper assembled here would advertise an
    unbounded list. The item inside is the same ``serializer_to_json_schema`` call
    either way. The reflection also merges ``metadata["json_schema"]["input"]``
    on top. It is given the binding too, which it does not read for ``many``,
    so the two shapes cannot be handed different bindings.

    **A single-item spec also advertises its target lookup.** drf-services hands
    the same ``params`` it validates against the input serializer to the one
    target lookup dispatch calls, and its unknown-argument check admits what
    that lookup declares (``declared_input_keys``), so ``{"pk": 1, "title": ...}``
    is served. That is the ``collection_selector_spec`` when one is declared,
    else the ``instance_selector_spec`` (``schema.utils.target_lookup``); a spec
    declaring a lookup dispatch would not call is refused when it is built. The
    lookup is described by the same ``spec_to_json_schema``
    reflection a selector tool advertises its own parameters with
    (``schema.utils.selector_inputs``): the selector's signature, minus the
    ``request`` / ``user`` / ``view`` seeds and every name this server fills --
    a registered seed, a ``UrlKwarg`` with a default, a key the lookup's own
    ``TypedDict``-annotated ``kwargs=`` provider returns -- plus a
    ``filter_set``'s fields. Every other parameter without a default is
    required, so ``get_invoice(*, pk)`` asks for ``pk``, and a call without it
    is refused before the lookup runs. An input-serializer field of the same
    name keeps its property, as the more precise declaration, and so does a
    ``UrlKwarg``, which is the channel the value actually arrives by.
    Requiredness is the union, deduplicated: the lookup reads the raw
    arguments rather than the validated ones, so it cannot run without a
    parameter it requires whatever the serializer says about the name. A
    ``many=True`` spec never reads a target selector, so its object stays the
    list alone and an item offers only the serializer's fields
    (``test_a_list_payload_item_advertises_only_the_keys_the_bind_admits``).

    Args:
        binding: The service tool binding to describe.
        pool_seeds: The server's registered seeds, which fill a lookup or service
            parameter of the same name, so it is not asked of the client.
    """
    spec = binding.spec
    if spec.many:
        # ``phase="input"`` never answers ``None`` (only the output phase is
        # nullable), so ``or {}`` narrows the type and never substitutes.
        schema: dict[str, Any] = (
            spec_to_json_schema(spec, phase="input", argument_binding=binding.argument_binding)
            or {}
        )
    else:
        schema = _with_target_lookups(
            spec,
            _declared_input(binding, pool_seeds=pool_seeds),
            url_kwargs=binding.url_kwargs,
            pool_seeds=pool_seeds,
        )
    if not binding.url_kwargs and not binding.query_params:
        return schema
    properties: dict[str, Any] = dict(schema.get("properties", {}))
    required: list[str] = list(schema.get("required", []))
    for url_kwarg in binding.url_kwargs:
        properties[url_kwarg.name] = url_kwarg.json_schema()
        if url_kwarg.required and url_kwarg.name not in required:
            required.append(url_kwarg.name)
    # Query params never join ``required``: a read-shaping param the spec runs
    # fine without cannot be required, which is why ``QueryParam`` carries no
    # such flag in the first place.
    for query_param in binding.query_params:
        properties[query_param.name] = query_param.json_schema()
    merged: dict[str, Any] = {**schema, "type": "object", "properties": properties}
    if required:
        merged["required"] = required
    return merged


def _declared_input(binding: ToolBinding, *, pool_seeds: PoolSeeds) -> dict[str, Any]:
    """The input ``binding``'s spec declares itself, before its target lookup is merged in.

    An ``input_serializer``'s schema, or, without one, drf-services' reflection
    under the binding dispatch runs, which lists a spreading service's own
    parameters. ``supplied=`` is the reserved seeds, the built-in ones and the
    ones this server registers, because dispatch subtracts exactly those from
    what it declares (``reserved=pool_seeds.reserved``): one left out would be
    listed and then stripped from the call, and passing any is what makes a
    parameter without a default required (``supplied=None`` would leave every
    parameter optional: ``test_a_spread_service_lists_its_own_parameters``;
    the built-in seeds alone would list a registered one:
    ``test_a_registered_seed_is_not_listed_as_a_service_parameter``). A name
    the spec's provider fills, or may decline, stays listed, since dispatch
    admits it, and is only not required; an untyped provider may fill any
    name, so none is. Each of the three readings is a row of
    ``test_a_name_the_services_provider_fills_is_offered_but_not_required``.
    """
    spec = binding.spec
    if spec.input_serializer is not None:
        return build_input_schema(spec.input_serializer, partial=spec.partial is True)
    schema: dict[str, Any] = (
        spec_to_json_schema(
            spec,
            phase="input",
            argument_binding=binding.argument_binding,
            supplied=pool_seeds.reserved,
        )
        or {}
    )
    keys = provider_keys(spec.kwargs)
    required: list[str] = (
        []
        if keys is None
        else [n for n in schema.get("required", []) if n not in keys.filled | keys.declinable]
    )
    trimmed = {key: value for key, value in schema.items() if key != "required"}
    if required:
        trimmed["required"] = required
    return trimmed


def _with_target_lookups(
    spec: ServiceSpec[Any, Any, Any],
    schema: dict[str, Any],
    *,
    url_kwargs: tuple[UrlKwarg, ...],
    pool_seeds: PoolSeeds,
) -> dict[str, Any]:
    """``schema`` with the reflected inputs of the target lookup dispatch calls merged under it.

    That lookup (``schema.utils.target_lookup``) is the one drf-services'
    ``declared_input_keys`` reads to decide which keys the bind admits:
    ``test_the_advertised_lookups_are_the_keys_the_bind_admits`` holds the two
    sides together. A key the serializer already declares keeps the
    serializer's schema, so the reflected one is written first and overlaid
    (``test_a_serializer_field_keeps_its_property_and_the_lookup_its_requiredness``
    holds both halves), and a lookup asking nothing leaves ``schema`` as it came
    (``test_a_lookup_that_asks_nothing_leaves_the_schema_as_the_serializer_built_it``).
    The lookup is reflected with its own ``kwargs=``
    provider, which is the one drf-services resolves the target with, and
    author-wins whatever the binding says, because drf-services applies that
    provider last under every binding
    (``test_a_target_lookups_provider_outranks_the_caller_under_every_binding``).

    **A key the service or one of its preconditions marks ``NotClientInput`` is
    not advertised**, though the lookup names it plainly: drf-services owns it
    for the whole call (``server_owned_keys``), so dispatch drops the caller's
    value before the lookup reads it and ``REJECT`` refuses it, and asking for
    it would invite a value nobody receives. The reflection of the lookup alone
    cannot see a marker on another callable, so the set is asked of the spec
    dispatch runs. A property ``schema`` brought, which for a serializer is one
    of its fields, is not subtracted: drf-services keeps a field of an owned
    name as the caller's input and validates it into ``data``. Each half is
    held by one of
    ``test_a_lookup_key_a_precondition_hides_is_not_advertised_but_a_field_of_that_name_is``'s
    two schemas: the subtraction, from the properties and from what the
    lookup requires, and the field names left out of it.
    """
    lookup = target_lookup(spec)
    if lookup is None:
        return schema
    reflected, _required = selector_inputs(lookup, url_kwargs=url_kwargs, pool_seeds=pool_seeds)
    properties: dict[str, Any] = dict(reflected.get("properties", {}))
    if not properties:
        return schema
    own: dict[str, Any] = schema.get("properties", {})
    properties.update(own)
    hidden = server_owned_keys(spec) - own.keys()
    properties = {key: value for key, value in properties.items() if key not in hidden}
    required: list[str] = [
        key
        for key in (*reflected.get("required", []), *schema.get("required", []))
        if key not in hidden
    ]
    merged: dict[str, Any] = {**schema, "type": "object", "properties": properties}
    if required:
        merged["required"] = list(dict.fromkeys(required))
    return merged


__all__ = ["build_service_tool_input_schema"]
