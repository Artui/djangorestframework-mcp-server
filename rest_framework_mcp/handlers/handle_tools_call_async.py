from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp._compat.utils import (
    arun_selector_sync_safe,
    arun_service_sync_safe,
)
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.build_call_pool import build_call_pool
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool_async
from rest_framework_mcp.handlers.handle_tools_call import (
    _render_output,
    _span_attrs,
    _validate_input,
)
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
    invoke_context_provider,
    validation_error_data,
)
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView


async def handle_tools_call_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of :func:`handle_tools_call`.

    Same dispatch flow — input validation, permission check, service
    invocation, optional output selector, output rendering — but routes the
    service / selector calls through :mod:`rest_framework_mcp._compat.utils`
    so genuinely async callables run native (no thread hop) while sync
    callables are bridged via ``sync_to_async``.

    Validation, schema introspection, and output rendering are CPU-only and
    run inline; they don't block the event loop noticeably.
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
        # Chain tools run an ordered sequence of specs; read-shaped tools
        # route through the selector-tool dispatch helper (filter / order /
        # paginate). Mutation tools fall through to the service-tool path
        # below.
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

        drf_request = build_internal_drf_request(
            context.http_request, user=context.token.user, data=arguments_raw
        )

        input_context: Mapping[str, Any] | None = None
        if binding.spec.input_serializer_context is not None:
            input_context_view = MCPServiceView(request=drf_request, action=binding.name)
            input_context = binding.spec.input_serializer_context(input_context_view, drf_request)

        try:
            validated: Any = _validate_input(
                arguments_raw,
                binding.spec,
                context=input_context,
                unknown_arguments=binding.unknown_arguments,
            )
        except drf_serializers.ValidationError as exc:
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(exc.detail, arguments_raw),
            )

        pool: dict[str, Any] = build_call_pool(
            binding,
            drf_request=drf_request,
            user=context.token.user,
            validated=validated,
            arguments_raw=arguments_raw,
        )

        try:
            kwargs: dict[str, Any] = resolve_callable_kwargs(binding.spec.service, pool)
            result: Any = await arun_service_sync_safe(
                binding.spec.service, kwargs, atomic=binding.spec.atomic
            )
        except ServiceValidationError as exc:
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                exc.message,
                data=validation_error_data(exc.detail, arguments_raw),
            )
        except ServiceError as exc:
            # See sync sibling — recording is opt-in via
            # ``RECORD_SERVICE_EXCEPTIONS`` so routine business-rule denials
            # don't flood error pipelines.
            if get_setting("RECORD_SERVICE_EXCEPTIONS"):
                otel_span.record_exception(exc)
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, exc.message)

        # Sister-repo 0.13+ moved the output pipeline under
        # ``spec.output_selector_spec`` — see the sync sibling for the
        # rationale; this branch mirrors that shape.
        out_spec = binding.spec.output_selector_spec
        if out_spec is not None and out_spec.selector is not None:
            sel_pool: dict[str, Any] = {
                "request": drf_request,
                "user": context.token.user,
                "instance": result,
                "result": result,
            }
            sel_kwargs: dict[str, Any] = resolve_callable_kwargs(out_spec.selector, sel_pool)
            result = await arun_selector_sync_safe(out_spec.selector, sel_kwargs)

        output_context: Mapping[str, Any] | None = None
        if out_spec is not None and out_spec.output_serializer_context is not None:
            output_context_view = MCPServiceView(request=drf_request, action=binding.name)
            # Forward the final (post-output-selector) ``result`` so a
            # provider declaring it can run a single batched query against the
            # exact value being serialized — sister-repo 0.15 parity.
            output_context = invoke_context_provider(
                out_spec.output_serializer_context,
                output_context_view,
                drf_request,
                extras={"result": result},
            )
        payload: Any = _render_output(result, binding.spec, context=output_context)
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
