from __future__ import annotations

from typing import Any

from rest_framework_services import spec_to_json_schema

from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_selector_tool_input_schema(
    binding: SelectorToolBinding, *, max_page_size: int | None = None
) -> dict[str, Any]:
    """Build the JSON Schema for a selector tool's ``inputSchema``.

    Merges five sources, in order of precedence (later sources override earlier
    ones on key collision):

    1. **Reflected ``spec`` shape** — the selector callable's own parameters (an
       ``**extras: Unpack[TypedDict]`` expanded into one property per key, its
       required keys populating ``required``, the ``request`` / ``user`` /
       ``view`` transport seeds skipped) plus the ``filter_set`` fields, via
       drf-services' :func:`spec_to_json_schema`. This is the *same* reflection
       the Pydantic-AI ``SpecToolset`` consumes, so both transports advertise
       the same shape: a nested route's ``parent_pk`` read from ``extras`` is
       discoverable without an explicit ``UrlKwarg``, and a ``FilterSet``'s
       ``OrderingFilter`` advertises ``ordering`` with nothing else declared.
    2. **``spec.input_serializer``** — tool-specific args that aren't reflected
       selector params. A ``SelectorSpec`` carries no input serializer, so this
       is MCP-only; its curated fields win over a reflected param of the same
       name, and required-marked fields stay required.
    3. **``ordering_fields``** — adds an ``ordering`` enum of ``"<field>"`` and
       ``"-<field>"`` values. **Deprecated** in favour of source 1: these are
       raw ORM paths, a second vocabulary for the same key. Declaring it
       alongside a filter-provided ordering is refused at construction, so it
       can never overwrite the reflected enum here.
    4. **``paginate=True``** — adds optional ``page`` and ``limit`` positive
       integers. ``limit`` carries a ``maximum`` when ``max_page_size`` is
       supplied, so the model sees the ceiling dispatch will clamp to.
    5. **``url_kwargs``** — each registered :class:`UrlKwarg`'s advertised
       schema, winning over a reflected key of the same name.

    Args:
        binding: The selector tool binding to describe.
        max_page_size: The effective page ceiling — the binding's override, else
            the server's. ``None`` advertises no ``maximum``.

    Returns:
        An object schema carrying ``properties``, and ``required`` only when at
        least one required field exists.
    """
    # ``spec_to_json_schema(phase="input")`` always returns a dict (only the
    # output phase is nullable), so ``or {}`` only narrows the type — it never
    # substitutes at runtime.
    reflected: dict[str, Any] = spec_to_json_schema(binding.spec, phase="input") or {}
    properties: dict[str, Any] = dict(reflected.get("properties", {}))
    required: list[str] = list(reflected.get("required", []))

    base: dict[str, Any] = build_input_schema(binding.input_serializer)
    properties.update(base.get("properties", {}))
    required.extend(name for name in base.get("required", []) if name not in required)

    if binding.ordering_fields:
        ordering_values: list[str] = []
        for field in binding.ordering_fields:
            ordering_values.append(field)
            ordering_values.append(f"-{field}")
        properties["ordering"] = {"enum": ordering_values}

    if binding.paginate:
        properties["page"] = {"type": "integer", "minimum": 1}
        limit_schema: dict[str, Any] = {"type": "integer", "minimum": 1}
        # Advertising and clamping are both needed and fail differently: no
        # ``maximum`` invites a request for 100 000 rows, and nothing obliges a
        # model to honour a schema anyway.
        if max_page_size is not None:
            limit_schema["maximum"] = max_page_size
        properties["limit"] = limit_schema

    for url_kwarg in binding.url_kwargs:
        # Model-supplied but seeded into ``view.kwargs`` at dispatch, never a
        # selector param.
        properties[url_kwarg.name] = url_kwarg.json_schema()
        if url_kwarg.required and url_kwarg.name not in required:
            required.append(url_kwarg.name)

    # Routed to ``request.query_params`` at dispatch, and never required — see
    # ``build_service_tool_input_schema``.
    for query_param in binding.query_params:
        properties[query_param.name] = query_param.json_schema()

    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


__all__ = ["build_selector_tool_input_schema"]
