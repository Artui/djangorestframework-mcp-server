from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import UNSET, FieldMarking, UnsetType
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.adapters.utils import (
    merge_meta,
    merge_tool_annotations,
    validate_input_serializer_against_callable,
    validate_query_params,
    validate_serializer_shapes,
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
    field_audiences: Mapping[str, FieldMarking] | None = None,
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

    A ``many=True`` spec takes its list under the one argument
    ``spec.many_argument`` names; ``_validate_list_payload`` refuses what would stop
    that list reaching dispatch.
    """
    _validate_list_payload(
        name,
        spec,
        argument_binding=argument_binding,
        url_kwargs=url_kwargs,
        query_params=query_params,
    )
    validate_serializer_shapes(
        label=f"service tool {name!r}",
        input_serializer=spec.input_serializer,
        output_serializer=(
            spec.output_selector_spec.output_serializer if spec.output_selector_spec else None
        ),
    )
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


def _validate_list_payload(
    name: str,
    spec: ServiceSpec,
    *,
    argument_binding: ArgumentBinding,
    url_kwargs: tuple[UrlKwarg, ...],
    query_params: tuple[QueryParam, ...],
) -> None:
    """Refuse a ``many=True`` spec declared in a way no ``tools/call`` could serve.

    MCP ``arguments`` is always an object, so the list travels under the one
    argument ``spec.many_argument`` names, and every dispatch passes drf-services
    ``many_as_argument=True`` to read it from there. Three declarations beside it
    would fail every call rather than any one of them:

    - A ``UrlKwarg`` or ``QueryParam`` of the same name. Both channels pop their
      name out of the arguments before dispatch, so the list would be routed to
      ``view.kwargs`` or the query string and dispatch would answer every call as
      if the argument were missing.
    - A ``SPREAD_*`` argument binding. The service receives the whole list as one
      ``data``, so drf-services raises ``ValueError`` on each dispatch rather than
      at registration.
    - A ``collection_selector_spec``. The list-payload dispatch never resolves a
      target, so the selector would be declared and never run. drf-services' own
      views refuse the pair in ``validate_service_spec`` for the same reason; this
      transport mounts no view, so the check is made here.
    """
    if not spec.many:
        return
    argument: str = spec.many_argument
    taken: list[str] = [
        f"{kind} {argument!r}"
        for kind, names in (
            ("UrlKwarg", {url_kwarg.name for url_kwarg in url_kwargs}),
            ("QueryParam", {query_param.name for query_param in query_params}),
        )
        if argument in names
    ]
    if taken:
        raise ImproperlyConfigured(
            f"Service tool {name!r}: {' and '.join(taken)} takes the name the spec's "
            f"list travels under (ServiceSpec.many_argument={argument!r}). The value "
            "would be routed out of the arguments before dispatch and the list would "
            "never arrive. Rename the channel, or set many_argument on the spec."
        )
    if argument_binding is not ArgumentBinding.BUNDLE:
        raise ImproperlyConfigured(
            f"Service tool {name!r}: argument_binding={argument_binding.name} cannot "
            "apply to a spec declaring many=True, whose service receives the whole "
            "list as one `data` argument, so there is nothing to spread. Leave "
            "argument_binding at its BUNDLE default."
        )
    if spec.collection_selector_spec is not None:
        raise ImproperlyConfigured(
            f"Service tool {name!r}: the spec declares both many=True and a "
            "collection_selector_spec. A list payload and a collection target are "
            "different bulk shapes, and the list-payload dispatch never resolves the "
            "collection. Declare one of them."
        )


__all__ = ["service_spec_to_tool"]
