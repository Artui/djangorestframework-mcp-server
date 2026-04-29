from __future__ import annotations

from typing import Any

from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.registry.selector_tool_binding import SelectorToolBinding


def selector_spec_to_tool(
    *,
    name: str,
    spec: SelectorSpec,
    description: str | None = None,
    title: str | None = None,
    input_serializer: type | None = None,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    filter_set: Any | None = None,
    ordering_fields: tuple[str, ...] = (),
    paginate: bool = False,
) -> SelectorToolBinding:
    """Lift a ``SelectorSpec`` into a :class:`SelectorToolBinding`.

    Sister of :func:`service_spec_to_tool` for the read-shaped pipeline.
    Validation of the spec (must have a concrete ``selector`` callable)
    happens here so registration fails loudly rather than at first call.

    ``input_serializer`` is a tool-layer parameter — the spec doesn't
    carry it because the sister repo's read-views derive their args from
    the URL/query, not a request body. MCP tools always pass arguments
    as a JSON dict, so we surface the serializer on the binding instead.
    """
    if spec.selector is None:
        raise ValueError(
            f"SelectorSpec for selector tool {name!r} has selector=None — MCP needs a "
            "concrete callable to dispatch to."
        )
    return SelectorToolBinding(
        name=name,
        description=description,
        title=title,
        spec=spec,
        input_serializer=input_serializer,
        output_format=output_format,
        permissions=permissions,
        rate_limits=rate_limits,
        annotations=annotations or {},
        filter_set=filter_set,
        ordering_fields=ordering_fields,
        paginate=paginate,
    )


__all__ = ["selector_spec_to_tool"]
