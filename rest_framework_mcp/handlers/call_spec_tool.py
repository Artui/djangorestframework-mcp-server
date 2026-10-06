"""``call_spec_tool`` — transport-neutral invocation of a spec-backed MCP tool.

Drives a ``ServiceSpec`` / ``SelectorSpec`` tool through the sister repo's
transport-neutral ``dispatch_spec`` + ``render_for_audience`` + ``enforce_permissions``,
off the HTTP / JSON-RPC path, returning the same
[`ToolResult`][rest_framework_mcp.protocol.types.tool_result.ToolResult] the wire
handlers build. A programmatic caller — the django-ag-ui bridge, a Pydantic-AI toolset,
a management command — gets a tool result without reaching into handler internals or
re-implementing dispatch.

Deliberately the **spec core**: instance resolution, ``input_serializer``
validation, the service / selector run, the output-selector re-fetch, queryset
shaping and rendering, honouring the binding's ``argument_binding`` /
``unknown_arguments`` policies and its ``permission_classes`` in two layers.
It does *not* layer on the read-shaped transport extras — pagination and a
selector binding's MCP-only ``input_serializer`` stay with the wire handlers —
and the transport-level MCP permissions / rate limits are a wire concern not
consulted here. Ordering is **not** among the extras: it is declared as an
``OrderingFilter`` on the spec's ``filter_set``, so it rides in with the
filtering ``dispatch_spec`` already applies.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services import (
    DEFAULT_POOL_SEEDS,
    OfflineContext,
    PoolSeeds,
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
    render_for_audience,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.handlers.utils import (
    read_shaping_error_result,
    refuse_missing_arguments,
    service_error_result,
    services_dispatch_policies,
    split_query_params,
    split_url_kwargs,
    validation_error_result,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.tool_result import ToolResult
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.types.agent_conventions import AgentConventions


def call_spec_tool(
    binding: ToolBinding | SelectorToolBinding | ChainToolBinding,
    arguments: dict[str, Any],
    *,
    user: Any,
    request: Any = None,
    config: MCPConfig,
    conventions: AgentConventions,
    pool_seeds: PoolSeeds = DEFAULT_POOL_SEEDS,
) -> ToolResult:
    """Invoke a spec-backed tool through the transport-neutral dispatch core.

    Enforces the spec's ``permission_classes`` against a synthetic off-HTTP
    context, dispatches via ``dispatch_spec`` and renders via
    ``render_for_audience``.

    Refused input — DRF's ``ValidationError`` (an unexpected argument, an
    ``input_serializer`` rejection, a refused filter value) and
    ``ServiceValidationError`` alike — comes back as the ``validation_error``
    result the wire handlers serve, as do a ``ServiceError``, a missing required
    instance, and a validation error raised while rendering when the caller
    supplied a read-shaping ``QueryParam``: all ``isError`` tool results the model
    can self-correct from. A denied permission raises ``PermissionDenied``, a
    protocol fault the caller maps to its own wire.
    A chain tool orchestrates several specs, has no single dispatch target, and
    is rejected with ``TypeError``.

    ``pool_seeds`` is the owning server's, handed to ``dispatch_spec`` as the
    wire handlers hand it, so a spec reading a registered seed runs the same
    in-process as over HTTP.
    ``conventions`` is the owning server's too, so a refused argument is worded
    here as the wire words it.
    """
    if isinstance(binding, ChainToolBinding):
        raise TypeError(
            f"call_tool does not support chain tool {binding.name!r}: a chain "
            "orchestrates several specs and has no single dispatch target. Call it "
            "over the HTTP / JSON-RPC transport instead."
        )
    spec = binding.spec
    # Split here rather than in the ``dispatch_spec`` try below: the offline
    # context it feeds is also what ``enforce_permissions`` needs, so moving it
    # down would drag the permission check into a block that turns exceptions
    # into tool results. Its own try is because a missing ``required=True`` URL
    # kwarg must surface as an ``isError`` result here as it does over the wire.
    try:
        spec_params, url_kwarg_values = split_url_kwargs(arguments, binding.url_kwargs)
    except drf_serializers.ValidationError as exc:
        # The permission answers first, as it does on the wire and through
        # ``acall_tool``: a caller the listing hides the tool from must not
        # learn which argument it left out. Checked against the context the
        # call would have run with, built from what it did deliver
        # (``test_call_tool_refuses_a_denied_caller_before_a_missing_url_kwarg``).
        delivered, delivered_url_kwargs = split_url_kwargs(
            arguments, binding.url_kwargs, refuse_missing=False
        )
        enforce_permissions(
            spec,
            _offline_context(binding, delivered, delivered_url_kwargs, user=user, request=request)[
                1
            ],
        )
        return validation_error_result(exc, arguments, config=config, conventions=conventions)
    spec_params, context = _offline_context(
        binding, spec_params, url_kwarg_values, user=user, request=request
    )
    # Class-level ``permission_classes``, enforced upfront and unconditionally:
    # ``dispatch_spec`` never consults them (authz is the caller's job) and the
    # ``on_target_resolved`` hook below only adds *object-level* checks on a
    # resolved target, so without this a spec whose ``has_permission`` denies
    # would leak its payload through this in-process surface.
    enforce_permissions(spec, context)
    argument_binding, unknown_arguments = services_dispatch_policies(binding)
    try:
        # After ``enforce_permissions``, as on the wire: a denied caller is told
        # so before it is told which argument it left out. Without the
        # ``input_serializer`` counted, because this route does not run a
        # selector tool's, so its defaults fill nothing here
        # (``test_call_tool_refuses_a_name_only_the_input_serializer_it_skips_would_fill``).
        refuse_missing_arguments(
            binding,
            (*spec_params, *url_kwarg_values),
            pool_seeds=pool_seeds,
            input_serializer_runs=False,
        )
        result = dispatch_spec(
            spec,
            user=user,
            params=spec_params,
            request=context.request,
            view=context.view,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=enforce_permissions,
            pool_seeds=pool_seeds,
            # A ``many=True`` spec's list arrives under ``spec.many_argument``, as
            # tool arguments are always an object; a no-op for any other spec.
            many_as_argument=True,
        )
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # DRF's error included, as on the wire: an unexpected argument, an
        # ``input_serializer`` rejection or a refused filter value is input the
        # model can correct, not a fault for the caller to catch. It was
        # raised out of here until the wire stopped answering it ``-32602``.
        return validation_error_result(exc, arguments, config=config, conventions=conventions)
    except ServiceError as exc:
        return service_error_result(exc)

    if result.kind == "not_found":
        return build_error_tool_result(
            f"{binding.name}: no matching instance found", error_type="not_found"
        )

    many: bool = result.kind == "list"
    # The output-serializer-context provider receives only the extras it
    # declares, so pass the kind-appropriate names.
    extras: dict[str, Any] = (
        {"page": result.value} if many else {"instance": result.value, "result": result.value}
    )
    # The render is where a read-shaping ``QueryParam`` is read, so a value the
    # serializer refuses fails here rather than in the ``dispatch_spec`` try
    # above. The same classification as the wire handlers: the caller's
    # ``isError`` when they supplied one, re-raised when they did not.
    try:
        payload: Any = render_for_audience(
            spec,
            result.value,
            projection=binding.audience_projection,
            many=many,
            view=context.view,
            request=context.request,
            extras=extras,
        )
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        return read_shaping_error_result(
            exc,
            query_params=binding.query_params,
            arguments=arguments,
            # Never a page here, even for a ``paginate=True`` selector binding:
            # pagination is one of the transport extras this entry point leaves
            # to the wire handlers, so the result has no envelope to explain.
            paginated=False,
            config=config,
            conventions=conventions,
        )
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
        default_output_schema=config.include_output_schema,
        default_structured_content=config.include_structured_content,
    )
    return build_tool_result(
        payload,
        output_format=binding.output_format,
        include_structured_content=emit_structured_content,
        content_kind=binding.content_kind,
        content_mime_type=binding.content_mime_type,
        binding_name=binding.name,
    )


def _offline_context(
    binding: ToolBinding | SelectorToolBinding,
    spec_params: dict[str, Any],
    url_kwarg_values: dict[str, Any],
    *,
    user: Any,
    request: Any,
) -> tuple[dict[str, Any], OfflineContext]:
    """The params left for dispatch, and the off-HTTP context the call runs in.

    One build for both of ``call_spec_tool``'s permission checks, so the one
    answering a call refused for a missing URL kwarg reads the same request and
    view the call would have: the URL kwargs it delivered in ``view.kwargs`` and
    out of ``request.data``
    (``test_call_tool_checks_a_missing_url_kwargs_permission_against_the_delivered_route``).
    """
    spec_params, query_param_values = split_query_params(spec_params, binding.query_params)
    context = build_offline_context(
        user,
        spec_params,
        http_request=request,
        action=binding.name,
        kwargs=url_kwarg_values or None,
        query_params=query_param_values,
    )
    return spec_params, context


__all__ = ["call_spec_tool"]
