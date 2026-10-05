from __future__ import annotations

from typing import Any

from rest_framework_services import spec_to_json_schema
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

    The shape is ``spec.input_serializer`` verbatim (``spec.partial is True`` drops
    ``required``, mirroring the dispatch-time partial-validation contract), plus any
    registered [`UrlKwarg`][rest_framework_services.types.url_kwarg.UrlKwarg] properties
    merged in.

    A ``UrlKwarg(required=True)`` joins ``required`` and ``spec.partial`` does
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
    on top, which the single-item shape above does not read.

    **A single-item spec also advertises its target lookup.** drf-services hands
    the same ``params`` it validates against the input serializer to the one
    target lookup dispatch calls, and its unknown-argument check admits what
    that lookup declares (``declared_input_keys``), so ``{"pk": 1, "title": ...}``
    is served. That is the ``collection_selector_spec`` when one is declared,
    else the ``instance_selector_spec`` (``schema.utils.target_lookup``):
    drf-services never runs an instance lookup beside a collection lookup, and
    ``UnknownArguments.REJECT`` refuses its keys there, so they are not
    advertised. The lookup is described by the same ``spec_to_json_schema``
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
        pool_seeds: The server's registered seeds, which fill a lookup parameter
            of the same name, so it is not asked of the client.
    """
    spec = binding.spec
    if spec.many:
        # ``phase="input"`` never answers ``None`` (only the output phase is
        # nullable), so ``or {}`` narrows the type and never substitutes.
        schema: dict[str, Any] = spec_to_json_schema(spec, phase="input") or {}
    else:
        schema = _with_target_lookups(
            spec,
            build_input_schema(spec.input_serializer, partial=spec.partial is True),
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


def _with_target_lookups(
    spec: ServiceSpec[Any, Any, Any],
    schema: dict[str, Any],
    *,
    url_kwargs: tuple[UrlKwarg, ...],
    pool_seeds: PoolSeeds,
) -> dict[str, Any]:
    """``schema`` with the reflected inputs of the target lookup dispatch calls merged under it.

    That lookup (``schema.utils.target_lookup``) is the one drf-services'
    ``declared_input_keys`` reads to decide which keys the bind admits, so
    beside a collection lookup the instance one is neither advertised nor
    admitted: ``test_the_advertised_lookups_are_the_keys_the_bind_admits``
    holds the two sides together, and
    ``test_beside_a_collection_lookup_only_the_collection_lookup_is_required``
    the schema alone. A key the serializer already declares keeps the
    serializer's schema, so the reflected one is written first and overlaid
    (``test_a_serializer_field_keeps_its_property_and_the_lookup_its_requiredness``
    holds both halves), and a lookup asking nothing leaves ``schema`` as it came
    (``test_a_lookup_that_asks_nothing_leaves_the_schema_as_the_serializer_built_it``).
    The lookup is reflected with its own ``kwargs=``
    provider, which is the one drf-services resolves the target with, and
    author-wins whatever the binding says, because drf-services applies that
    provider last under every binding
    (``test_a_target_lookups_provider_outranks_the_caller_under_every_binding``).
    """
    lookup = target_lookup(spec)
    if lookup is None:
        return schema
    reflected, _required = selector_inputs(lookup, url_kwargs=url_kwargs, pool_seeds=pool_seeds)
    properties: dict[str, Any] = dict(reflected.get("properties", {}))
    if not properties:
        return schema
    properties.update(schema.get("properties", {}))
    required: list[str] = [*reflected.get("required", []), *schema.get("required", [])]
    merged: dict[str, Any] = {**schema, "type": "object", "properties": properties}
    if required:
        merged["required"] = list(dict.fromkeys(required))
    return merged


__all__ = ["build_service_tool_input_schema"]
