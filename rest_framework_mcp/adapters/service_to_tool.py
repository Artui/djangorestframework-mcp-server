from __future__ import annotations

from typing import Any

from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.registry.tool_binding import ToolBinding


def service_spec_to_tool(
    *,
    name: str,
    spec: ServiceSpec,
    description: str | None = None,
    title: str | None = None,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
) -> ToolBinding:
    """Lift a ``ServiceSpec`` into a :class:`ToolBinding`.

    Pure projection — no side effects on the spec or its callable. The
    handler layer (``handlers/handle_tools_call.py``) is what eventually
    invokes ``spec.service``.
    """
    return ToolBinding(
        name=name,
        description=description,
        title=title,
        spec=spec,
        output_format=output_format,
        permissions=permissions,
        rate_limits=rate_limits,
        annotations=annotations or {},
    )


__all__ = ["service_spec_to_tool"]
