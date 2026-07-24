from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
    render_spec_output,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    services_dispatch_policies,
    split_url_kwargs,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def handle_tools_call(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Invoke a registered tool by name.

    Service tools dispatch through drf-services' transport-neutral
    :func:`~rest_framework_services.dispatch_spec`: it owns instance resolution,
    ``input_serializer`` validation, the kwarg pool (per the binding's
    ``argument_binding`` / ``unknown_arguments`` policies), the service run, and
    the output-selector re-fetch. This layer keeps only the transport shell —
    MCP permissions / rate limits, the ``enforce_permissions`` object-permission
    hook, output format, and ``structuredContent``.
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

    # OpenTelemetry span: scoped to the dispatch portion (after binding
    # resolution) so cheap validation rejections don't generate noise. The
    # span is a no-op when ``opentelemetry-api`` isn't installed.
    with span(
        "mcp.tools.call",
        attributes=_span_attrs(binding.name, context),
    ) as otel_span:
        # Chain tools run an ordered sequence of specs; selector tools
        # (read-shaped) own the post-fetch pipeline (filter / order /
        # paginate). Both route through dedicated dispatch helpers. Service
        # tools fall through to the mutation-shaped path below.
        if isinstance(binding, ChainToolBinding):
            return dispatch_chain_tool(binding, params, arguments_raw, context, otel_span)
        if isinstance(binding, SelectorToolBinding):
            return dispatch_selector_tool(binding, params, arguments_raw, context, otel_span)

        allowed, required_scopes = check_permissions(
            binding.permissions, context.http_request, context.token
        )
        if not allowed:
            return JsonRpcError(
                JsonRpcErrorCode.FORBIDDEN,
                "Insufficient permission",
                data={"requiredScopes": required_scopes} if required_scopes else None,
            )

        retry_after: int | None = consume_rate_limits(
            binding.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        # Synthesise the off-HTTP request/view a spec's callables expect from the
        # real HTTP request + MCP-supplied arguments, then dispatch through the
        # neutral core. ``enforce_permissions`` is the object-permission hook —
        # it runs ``spec.permission_classes`` against the resolved target.
        # URL kwargs (a scoping ``spec.kwargs`` provider's ``view.kwargs`` inputs)
        # route through the view, not the params — stripped from what the spec
        # validates / spreads, seeded into ``build_offline_context(kwargs=…)``.
        spec_params, url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
        offline = build_offline_context(
            context.token.user,
            spec_params,
            http_request=context.http_request,
            action=binding.name,
            kwargs=url_kwarg_values or None,
        )
        argument_binding, unknown_arguments = services_dispatch_policies(binding)
        try:
            result = dispatch_spec(
                binding.spec,
                user=context.token.user,
                params=spec_params,
                request=offline.request,
                view=offline.view,
                argument_binding=argument_binding,
                unknown_arguments=unknown_arguments,
                on_target_resolved=enforce_permissions,
            )
        except drf_serializers.ValidationError as exc:
            # A malformed input *shape* is a protocol fault (-32602).
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
            )
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        except ServiceValidationError as exc:
            # Business validation on well-shaped input is a *tool-level* failure
            # per the MCP spec — an ``isError`` result the model can self-correct
            # from, not a protocol error.
            return build_error_tool_result(
                exc.message,
                error_type="validation_error",
                detail=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
            ).to_dict()
        except ServiceError as exc:
            # The "real failure" channel — recorded on the active span when the
            # consumer opted in. ``ServiceValidationError`` is deliberately not
            # recorded (input-shape feedback, not a server fault).
            if context.config.record_service_exceptions:
                otel_span.record_exception(exc)
            return build_error_tool_result(exc.message, error_type="service_error").to_dict()

        if result.kind == "not_found":
            return build_error_tool_result(
                f"{binding.name}: no matching instance found", error_type="not_found"
            ).to_dict()

        payload = _render(binding, result, offline)
        output_format: OutputFormat = OutputFormat.coerce(
            params.get("outputFormat") or binding.output_format
        )
        _emit_output_schema, emit_structured_content = resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=context.config.include_output_schema,
            default_structured_content=context.config.include_structured_content,
        )
        return build_tool_result(
            payload,
            output_format=output_format,
            include_structured_content=emit_structured_content,
        ).to_dict()


def _render(binding: Any, result: Any, offline: Any) -> Any:
    """Render a service ``DispatchResult`` through the spec's output serializer.

    ``render_spec_output`` reads the output serializer off
    ``spec.output_selector_spec`` and resolves its ``output_serializer_context``
    provider with the resolved-data extras it declares (``page`` for a list
    result, ``instance`` / ``result`` for a single one).
    """
    many: bool = result.kind == "list"
    extras: dict[str, Any] = (
        {"page": result.value} if many else {"instance": result.value, "result": result.value}
    )
    payload = render_spec_output(
        binding.spec,
        result.value,
        many=many,
        view=offline.view,
        request=offline.request,
        extras=extras,
    )
    # MCP contract: a tool returning ``None`` (no output serializer) must still
    # emit a JSON object as ``structuredContent``. ``render_spec_output`` passes
    # ``None`` through, so coerce it here.
    return {} if payload is None else payload


def _span_attrs(binding_name: str, context: MCPCallContext) -> dict[str, Any]:
    """Common span attributes — kept here so the six dispatch handlers stay in sync."""
    attrs: dict[str, Any] = {
        "mcp.binding.name": binding_name,
        "mcp.protocol.version": context.protocol_version,
    }
    if context.session_id:
        attrs["mcp.session.id"] = context.session_id
    return attrs


__all__ = ["handle_tools_call"]
