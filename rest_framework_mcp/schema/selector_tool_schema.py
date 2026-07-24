from __future__ import annotations

from typing import Any

from rest_framework_services import spec_to_json_schema

from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.input_schema import build_input_schema


def build_selector_tool_input_schema(binding: SelectorToolBinding) -> dict[str, Any]:
    """Build the JSON Schema for a selector tool's ``inputSchema``.

    Merges five sources, in order of precedence (later sources override
    earlier ones on key collision):

    1. **Reflected ``spec`` shape** — the selector callable's own parameters
       (an ``**extras: Unpack[TypedDict]`` expanded into one property per key,
       the TypedDict's required keys populating ``required``, the
       ``request`` / ``user`` / ``view`` transport seeds skipped) plus the
       ``filter_set`` fields — via drf-services' :func:`spec_to_json_schema`,
       the *same* reflection the Pydantic-AI ``SpecToolset`` consumes, so both
       transports advertise the same shape. This is what makes a URL kwarg a
       selector reads from its ``extras`` (a nested route's ``parent_pk``)
       discoverable over MCP without an explicit ``UrlKwarg``.
    2. **``spec.input_serializer``** — any explicit input shape declared by the
       consumer (tool-specific args that aren't reflected selector params). A
       ``SelectorSpec`` carries no input serializer, so this is MCP-only; its
       curated fields win over a reflected param of the same name, and all
       required-marked fields stay required.
    3. **``ordering_fields``** — adds an ``ordering`` property as an enum
       of ``"<field>"`` and ``"-<field>"`` values. Optional.
    4. **``paginate=True``** — adds optional ``page`` (positive integer)
       and ``limit`` (positive integer) properties.
    5. **``url_kwargs``** — each registered :class:`UrlKwarg`'s advertised
       schema; wins over a reflected key of the same name (it is the
       intentional, authoritative declaration).

    The final schema is always an object with ``"type": "object"``,
    ``"properties": {...}``, and ``"required": [...]`` only when at
    least one required field exists.
    """
    # ``spec_to_json_schema(phase="input")`` always returns a dict (only the
    # output phase is nullable), so ``or {}`` only narrows the type — it never
    # substitutes at runtime.
    reflected: dict[str, Any] = spec_to_json_schema(binding.spec, phase="input") or {}
    properties: dict[str, Any] = dict(reflected.get("properties", {}))
    required: list[str] = list(reflected.get("required", []))

    # Explicit ``input_serializer`` args override reflected params of the same
    # name (the curated declaration wins); its required fields join the set.
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
        properties["limit"] = {"type": "integer", "minimum": 1}

    for url_kwarg in binding.url_kwargs:
        # URL-derived args — model-supplied, seeded into ``view.kwargs`` at
        # dispatch (never a selector param). Optional, like filter args.
        properties[url_kwarg.name] = url_kwarg.json_schema()

    out: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        out["required"] = required
    return out


__all__ = ["build_selector_tool_input_schema"]
