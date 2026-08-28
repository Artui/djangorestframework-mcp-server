from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework_services import UNSET, AgentField, UnsetType
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.utils import (
    merge_meta,
    merge_tool_annotations,
    validate_input_serializer_against_callable,
    validate_query_params,
    validate_url_kwargs,
)
from rest_framework_mcp.auth.permissions.wrap_spec_permissions import wrap_spec_permissions
from rest_framework_mcp.constants import (
    ArgumentBinding,
    OutputFormat,
    TaskPolicy,
    ToolContentKind,
    UnknownArguments,
)
from rest_framework_mcp.protocol.types.icon import Icon
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg


def service_spec_to_tool(
    *,
    name: str,
    spec: ServiceSpec,
    description: str | None = None,
    title: str | None = None,
    icons: tuple[Icon, ...] = (),
    content_kind: ToolContentKind = ToolContentKind.TEXT,
    task_policy: TaskPolicy = TaskPolicy.FORBIDDEN,
    invalidates: tuple[str, ...] = (),
    content_mime_type: str | None = None,
    display_name: str | None = None,
    display_description: str | None = None,
    output_format: OutputFormat = OutputFormat.JSON,
    permissions: tuple[Any, ...] = (),
    rate_limits: tuple[Any, ...] = (),
    annotations: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    field_audiences: Mapping[str, AgentField] | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    argument_binding: ArgumentBinding = ArgumentBinding.BUNDLE,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    always_listed: bool = False,
    spec_kwargs_provides: tuple[str, ...] = (),
    url_kwargs: tuple[UrlKwarg, ...] = (),
    query_params: tuple[QueryParam, ...] = (),
    max_result_bytes: int | None | UnsetType = UNSET,
    dispatch_timeout: float | None | UnsetType = UNSET,
) -> ToolBinding:
    """Lift a ``ServiceSpec`` into a
    [`ToolBinding`][rest_framework_mcp.registry.types.tool_binding.ToolBinding].

    Pure projection — no side effects on the spec or its callable. The handler
    layer (``handlers/handle_tools_call.py``) is what invokes ``spec.service``.

    ``spec.permission_classes`` is honoured: each DRF ``BasePermission`` class is
    wrapped in
    [`DRFPermissionAdapter`][rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter]
    and prepended to the per-binding ``permissions`` tuple, so author-declared contracts
    on the spec run before transport-level ``MCPPermission`` instances, AND-combined.

    ``meta`` is the base-protocol ``_meta`` bundle the tool's ``tools/list``
    entry carries. It goes through ``merge_meta`` so a later
    framework-derived contribution slots in at this one call site.
    """
    validate_input_serializer_against_callable(
        label=f"service tool {name!r}",
        input_serializer=spec.input_serializer,
        callable_=spec.service,
        argument_binding=argument_binding,
        spec_kwargs_provides=frozenset(spec_kwargs_provides),
        provides_instance=(
            spec.instance_selector_spec is not None
            and spec.instance_selector_spec.selector is not None
        ),
        provides_collection=(
            spec.collection_selector_spec is not None
            and spec.collection_selector_spec.selector is not None
        ),
    )
    validate_url_kwargs(label=f"service tool {name!r}", url_kwargs=url_kwargs)
    validate_query_params(
        label=f"service tool {name!r}", query_params=query_params, url_kwargs=url_kwargs
    )
    spec_perms: tuple[Any, ...] = wrap_spec_permissions(spec.permission_classes, label=name)
    effective_perms: tuple[Any, ...] = spec_perms + tuple(permissions)
    return ToolBinding(
        name=name,
        field_audiences=field_audiences,
        description=description,
        title=title,
        icons=icons,
        content_kind=content_kind,
        task_policy=task_policy,
        invalidates=invalidates,
        content_mime_type=content_mime_type,
        display_name=display_name,
        display_description=display_description,
        spec=spec,
        output_format=output_format,
        permissions=effective_perms,
        rate_limits=rate_limits,
        annotations=merge_tool_annotations(annotations, read_only=False),
        meta=merge_meta(meta),
        include_structured_content=include_structured_content,
        include_output_schema=include_output_schema,
        argument_binding=argument_binding,
        unknown_arguments=unknown_arguments,
        always_listed=always_listed,
        url_kwargs=url_kwargs,
        query_params=query_params,
        max_result_bytes=max_result_bytes,
        dispatch_timeout=dispatch_timeout,
    )


__all__ = ["service_spec_to_tool"]
