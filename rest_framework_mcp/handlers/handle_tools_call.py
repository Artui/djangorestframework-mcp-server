from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services._compat.run_service import run_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat, UnknownArguments
from rest_framework_mcp.handlers.build_call_pool import build_call_pool
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    build_validated_input_serializer,
    check_permissions,
    consume_rate_limits,
    invoke_context_provider,
    resolve_spec_instance,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView


def _validate_input(
    arguments: dict[str, Any],
    spec: ServiceSpec,
    *,
    context: Mapping[str, Any] | None = None,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    instance: Any = None,
) -> tuple[Any, Any]:
    """Validate against ``spec.input_serializer``; return ``(validated, serializer)``.

    The actual logic lives in
    :func:`rest_framework_mcp.handlers.utils.build_validated_input_serializer`
    so selector-tool dispatch can share it without a circular import.
    ``context`` carries ``spec.input_serializer_context``'s output when set;
    ``unknown_arguments`` carries the binding's unknown-key policy.

    Sister-repo 0.16 adoption: ``spec.partial`` drives partial validation
    (``None`` keeps the historical non-partial behaviour — MCP has no HTTP
    method to derive the flag from), and ``instance`` (the row resolved by
    ``spec.instance_selector_spec``) is threaded into the serializer so
    instance-dependent validation works identically over MCP and HTTP.
    """
    return build_validated_input_serializer(
        arguments,
        spec.input_serializer,
        context=context,
        unknown_arguments=unknown_arguments,
        partial=spec.partial or False,
        instance=instance,
    )


def _render_output(
    result: Any,
    spec: ServiceSpec,
    *,
    context: Mapping[str, Any] | None = None,
) -> Any:
    """Convert ``result`` to a JSON-shaped payload using the spec's output serializer.

    Sister-repo 0.13+ collapsed the previously-flat output fields
    (``output_serializer`` / ``output_selector`` /
    ``output_serializer_context``) into a single nested
    ``output_selector_spec: SelectorSpec | None``. We read the
    serializer from there; an absent or serializer-less nested spec
    falls back to passthrough. ``None`` becomes ``{}`` so MCP clients
    always receive a JSON object as ``structuredContent``. ``context``
    is forwarded to the serializer when set so sister-repo's
    ``output_serializer_context`` participates in field rendering.
    """
    output_serializer = (
        spec.output_selector_spec.output_serializer if spec.output_selector_spec else None
    )
    if output_serializer is None:
        return {} if result is None else result
    if context is None:
        return output_serializer(result).data
    return output_serializer(result, context=dict(context)).data


def handle_tools_call(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Invoke a registered tool by name.

    The dispatch flow mirrors what an HTTP mutation does — validate input →
    run service → optional output selector → render output — but without any
    view/viewset machinery. The kwarg pool is built locally and resolved via
    :func:`resolve_callable_kwargs`.
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
        # tools fall through to the existing mutation-shaped path below.
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

        drf_request = build_internal_drf_request(
            context.http_request, user=context.token.user, data=arguments_raw
        )

        # Sister-repo 0.12+ ``input_serializer_context``: a per-spec callable
        # producing extra serializer context. Resolved here (before validation)
        # so the synthesised view/request are already on hand.
        input_context: Mapping[str, Any] | None = None
        if binding.spec.input_serializer_context is not None:
            input_context_view = MCPServiceView(request=drf_request, action=binding.name)
            input_context = binding.spec.input_serializer_context(input_context_view, drf_request)

        # Sister-repo 0.16 ``instance_selector_spec``: resolve the mutation
        # target *before* validation (the serializer sees ``self.instance``).
        # The raw arguments fill the role URL kwargs play on HTTP. A missing
        # row is a tool-level failure — the model can read it and correct
        # the identifier — not a protocol error.
        instance: Any = None
        instance_spec = binding.spec.instance_selector_spec
        if instance_spec is not None and instance_spec.selector is not None:
            found, instance = resolve_spec_instance(
                binding.spec,
                drf_request=drf_request,
                user=context.token.user,
                arguments_raw=arguments_raw,
                binding_name=binding.name,
            )
            if not found:
                return build_error_tool_result(
                    f"{binding.name}: no matching instance found",
                    error_type="not_found",
                ).to_dict()

        try:
            validated, serializer = _validate_input(
                arguments_raw,
                binding.spec,
                context=input_context,
                unknown_arguments=binding.unknown_arguments,
                instance=instance,
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
        # Reserved seeds (clients can't reach these names through the
        # spread): the resolved mutation target and the bound, validated
        # serializer — sister-repo 0.16's opt-in declare-to-receive kwargs.
        if instance is not None:
            pool["instance"] = instance
        if serializer is not None:
            pool["serializer"] = serializer

        try:
            kwargs: dict[str, Any] = resolve_callable_kwargs(binding.spec.service, pool)
            result: Any = run_service(binding.spec.service, kwargs, atomic=binding.spec.atomic)
        except ServiceValidationError as exc:
            # Business validation on well-shaped input is a *tool-level*
            # failure per the MCP spec — return an ``isError`` result the
            # model can read and self-correct from. JSON-RPC errors are
            # reserved for protocol faults (the serializer rejecting the
            # arguments *shape* above stays ``-32602``).
            return build_error_tool_result(
                exc.message,
                error_type="validation_error",
                detail=validation_error_data(exc.detail, arguments_raw),
            ).to_dict()
        except ServiceError as exc:
            # ``ServiceError`` is the "real failure" channel — record it on
            # the active span when the consumer has opted in. ``ServiceValidationError``
            # is deliberately not recorded; it's input-shape feedback, not a
            # server fault, and would clutter alerting pipelines.
            if get_setting("RECORD_SERVICE_EXCEPTIONS"):
                otel_span.record_exception(exc)
            return build_error_tool_result(exc.message, error_type="service_error").to_dict()

        # Sister-repo 0.13+ moved the output pipeline under
        # ``spec.output_selector_spec``: an optional nested ``SelectorSpec``
        # whose ``.selector`` (if set) re-fetches the just-mutated row and
        # whose ``.output_serializer_context`` callable (if set) contributes
        # serializer context. The flat fields no longer exist.
        out_spec = binding.spec.output_selector_spec
        if out_spec is not None and out_spec.selector is not None:
            sel_pool: dict[str, Any] = {
                "request": drf_request,
                "user": context.token.user,
                "instance": result,
                "result": result,
            }
            sel_kwargs: dict[str, Any] = resolve_callable_kwargs(out_spec.selector, sel_pool)
            result = run_selector(out_spec.selector, sel_kwargs)

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
