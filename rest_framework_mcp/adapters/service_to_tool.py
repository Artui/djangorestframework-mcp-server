from __future__ import annotations

from typing import Any

from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.utils import validate_input_serializer_against_callable
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import ArgumentBinding, OutputFormat, UnknownArguments
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


def service_spec_to_tool(
    *,
    name: str,
    spec: ServiceSpec,
    description: str | None = None,
    title: str | None = None,
    display_name: str | None = None,
    display_description: str | None = None,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    argument_binding: ArgumentBinding = ArgumentBinding.DATA_ONLY,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    always_listed: bool = False,
    spec_kwargs_provides: tuple[str, ...] = (),
) -> ToolBinding:
    """Lift a ``ServiceSpec`` into a :class:`ToolBinding`.

    Pure projection — no side effects on the spec or its callable. The
    handler layer (``handlers/handle_tools_call.py``) is what eventually
    invokes ``spec.service``.

    ``spec.permission_classes`` (sister-repo 0.12+) is honored: each DRF
    ``BasePermission`` class is wrapped in :class:`DRFPermissionAdapter` and
    prepended to the per-binding ``permissions`` tuple. Author-declared
    contracts on the spec run before transport-level ``MCPPermission``
    instances, AND-combined.
    """
    validate_input_serializer_against_callable(
        label=f"service tool {name!r}",
        input_serializer=spec.input_serializer,
        callable_=spec.service,
        argument_binding=argument_binding,
        spec_kwargs_provides=frozenset(spec_kwargs_provides),
    )
    spec_perms: tuple[Any, ...] = wrap_spec_permissions(spec.permission_classes, label=name)
    effective_perms: tuple[Any, ...] = spec_perms + tuple(permissions)
    return ToolBinding(
        name=name,
        description=description,
        title=title,
        display_name=display_name,
        display_description=display_description,
        spec=spec,
        output_format=output_format,
        permissions=effective_perms,
        rate_limits=rate_limits,
        annotations=annotations or {},
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        argument_binding=argument_binding,
        unknown_arguments=unknown_arguments,
        always_listed=always_listed,
    )


__all__ = ["service_spec_to_tool"]
