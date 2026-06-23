from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import adispatch_spec, build_offline_context, enforce_permissions
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool_async
from rest_framework_mcp.handlers.handle_tools_call import _render, _span_attrs
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    services_dispatch_policies,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


async def handle_tools_call_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of :func:`handle_tools_call`.

    Service tools dispatch through :func:`~rest_framework_services.adispatch_spec`,
    which awaits async-native callables and bridges sync ones (validation,
    instance resolution, the ``enforce_permissions`` guard) off the event loop.
    The transport shell — MCP permissions / rate limits, output format,
    ``structuredContent`` — stays here; output rendering runs through
    :func:`acall` so a lazy list result is materialised off-loop.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "tools/call params must be an object")

    tool_name: Any = params.get("name")
    if not isinstance(tool_name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.tools.get(tool_name)
    if binding is None:
        return JsonRpcError(JsonRpcErrorCode.TOOL_NOT_FOUND, f"Unknown tool: {tool_name!r}")

    arguments_raw: Any = params.get("arguments") or {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    with span("mcp.tools.call", attributes=_span_attrs(binding.name, context)) as otel_span:
        # Chain tools run an ordered sequence of specs; read-shaped tools route
        # through the selector-tool dispatch helper (filter / order / paginate).
        # Mutation tools fall through to the service-tool path below.
        if isinstance(binding, ChainToolBinding):
            return await dispatch_chain_tool_async(
                binding, params, arguments_raw, context, otel_span
            )
        if isinstance(binding, SelectorToolBinding):
            return await dispatch_selector_tool_async(
                binding, params, arguments_raw, context, otel_span
            )

        allowed, required_scopes = await acall(
            check_permissions, binding.permissions, context.http_request, context.token
        )
        if not allowed:
            return JsonRpcError(
                JsonRpcErrorCode.FORBIDDEN,
                "Insufficient permission",
                data={"requiredScopes": required_scopes} if required_scopes else None,
            )

        retry_after: int | None = await acall(
            consume_rate_limits, binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        offline = build_offline_context(
            context.token.user,
            arguments_raw,
            http_request=context.http_request,
            action=binding.name,
        )
        argument_binding, unknown_arguments = services_dispatch_policies(binding)
        try:
            result = await adispatch_spec(
                binding.spec,
                user=context.token.user,
                params=arguments_raw,
                request=offline.request,
                view=offline.view,
                argument_binding=argument_binding,
                unknown_arguments=unknown_arguments,
                on_target_resolved=enforce_permissions,
            )
        except drf_serializers.ValidationError as exc:
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(exc.detail, arguments_raw),
            )
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        except ServiceValidationError as exc:
            return build_error_tool_result(
                exc.message,
                error_type="validation_error",
                detail=validation_error_data(exc.detail, arguments_raw),
            ).to_dict()
        except ServiceError as exc:
            if get_setting("RECORD_SERVICE_EXCEPTIONS"):
                otel_span.record_exception(exc)
            return build_error_tool_result(exc.message, error_type="service_error").to_dict()

        if result.kind == "not_found":
            return build_error_tool_result(
                f"{binding.name}: no matching instance found", error_type="not_found"
            ).to_dict()

        # Rendering may evaluate a lazy list queryset → run it off the event loop.
        payload: Any = await acall(_render, binding, result, offline)
        output_format: OutputFormat = OutputFormat.coerce(
            params.get("outputFormat") or binding.output_format
        )
        _emit_output_schema, emit_structured_content = resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
        )
        return build_tool_result(
            payload,
            output_format=output_format,
            include_structured_content=emit_structured_content,
        ).to_dict()


__all__ = ["handle_tools_call_async"]
