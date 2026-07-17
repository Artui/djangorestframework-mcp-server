"""``call_spec_tool`` — transport-neutral invocation of a spec-backed MCP tool.

The blessed surface this introduces: drive a ``ServiceSpec`` / ``SelectorSpec``
tool through the sister repo's transport-neutral ``dispatch_spec`` +
``render_spec_output`` + ``enforce_permissions`` (off the HTTP / JSON-RPC path),
returning the same :class:`ToolResult` the wire handlers build. A programmatic
caller — the django-ag-ui bridge, a Pydantic-AI toolset, a management command —
gets a tool result without reaching into handler internals or re-implementing
dispatch.

This is deliberately the **spec core**: it resolves the instance, validates the
spec's ``input_serializer``, runs the service / selector, re-fetches through the
output selector, shapes + filters the queryset, and renders — honouring the
binding's ``argument_binding`` / ``unknown_arguments`` policies (mapped onto
``dispatch_spec``'s) and its ``permission_classes`` in two layers: an upfront
``enforce_permissions`` call for the class-level ``has_permission`` check, plus
the ``on_target_resolved=enforce_permissions`` hook for object-level checks on
the resolved target.
It does *not* layer on the read-shaped transport extras — pagination, ordering,
and a selector binding's MCP-only ``input_serializer`` stay with the wire
handlers. The transport-level MCP permissions / rate limits are a wire concern
and are not consulted here.
"""

from __future__ import annotations

from typing import Any

from rest_framework_services import (
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
    render_spec_output,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.handlers.utils import services_dispatch_policies, validation_error_data
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.tool_result import ToolResult
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding


def call_spec_tool(
    binding: ToolBinding | SelectorToolBinding | ChainToolBinding,
    arguments: dict[str, Any],
    *,
    user: Any,
    request: Any = None,
    config: MCPConfig,
) -> ToolResult:
    """Invoke a spec-backed tool through the transport-neutral dispatch core.

    Enforces the spec's ``permission_classes`` against a synthetic off-HTTP
    context, dispatches via ``dispatch_spec``, and renders via
    ``render_spec_output``. Business-rule failures the service raises
    (``ServiceValidationError`` / ``ServiceError``) and a missing required
    instance come back as ``isError`` tool results the model can self-correct
    from; a denied permission raises ``PermissionDenied`` and a malformed input
    payload raises DRF's ``ValidationError`` — protocol faults the caller maps to
    its own wire.

    Chain tools have no single ``dispatch_spec`` target (they orchestrate several
    specs) and are rejected with :class:`TypeError`.
    """
    if isinstance(binding, ChainToolBinding):
        raise TypeError(
            f"call_tool does not support chain tool {binding.name!r}: a chain "
            "orchestrates several specs and has no single dispatch target. Call it "
            "over the HTTP / JSON-RPC transport instead."
        )
    spec = binding.spec
    context = build_offline_context(user, arguments, http_request=request, action=binding.name)
    # Class-level ``permission_classes``, enforced upfront and unconditionally.
    # ``dispatch_spec`` never consults ``permission_classes`` (authz is the
    # caller's job), and the ``on_target_resolved`` hook only adds *object-level*
    # checks on a resolved target — and, before drf-services 0.21, never fired on
    # the selector paths at all. So a spec whose ``has_permission`` denies (e.g.
    # a ``DenyAll`` selector) would otherwise leak its payload through this
    # in-process surface. The hook below still covers object-level checks.
    enforce_permissions(spec, context)
    argument_binding, unknown_arguments = services_dispatch_policies(binding)
    try:
        result = dispatch_spec(
            spec,
            user=user,
            params=arguments,
            request=context.request,
            view=context.view,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            on_target_resolved=enforce_permissions,
        )
    except ServiceValidationError as exc:
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail=validation_error_data(
                exc.detail, arguments, include_value=config.include_validation_value
            ),
        )
    except ServiceError as exc:
        return build_error_tool_result(exc.message, error_type="service_error")

    if result.kind == "not_found":
        return build_error_tool_result(
            f"{binding.name}: no matching instance found", error_type="not_found"
        )

    many: bool = result.kind == "list"
    # The output-serializer-context provider receives only the resolved-data
    # extras it declares; pass the kind-appropriate name(s) so both a list's
    # ``page`` provider and a retrieve/mutation's ``instance`` / ``result``
    # provider keep working off this surface.
    extras: dict[str, Any] = (
        {"page": result.value} if many else {"instance": result.value, "result": result.value}
    )
    payload: Any = render_spec_output(
        spec, result.value, many=many, view=context.view, request=context.request, extras=extras
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
    )


__all__ = ["call_spec_tool"]
