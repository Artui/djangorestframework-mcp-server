from __future__ import annotations

import dataclasses
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services._compat.run_service import run_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
    validation_error_data,
)
from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.server.mcp_service_view import MCPServiceView


def _validate_input(arguments: dict[str, Any], spec: ServiceSpec) -> Any:
    """Validate ``arguments`` against ``spec.input_serializer``.

    Mirrors what ``rest_framework_services.views.mutation.utils.validate_input``
    does, but without requiring a DRF ``Request`` — we already have a dict.
    Returns either:
      - the dataclass instance produced by a ``DataclassSerializer``,
      - the validated dict for a plain ``Serializer``,
      - ``None`` if no ``input_serializer`` is declared.

    Raises :class:`drf_serializers.ValidationError` on invalid input.
    """
    if spec.input_serializer is None:
        return None
    target: type = spec.input_serializer
    if dataclasses.is_dataclass(target) and not isinstance(target, type):  # pragma: no cover
        raise TypeError("input_serializer must be a class")
    if isinstance(target, type) and dataclasses.is_dataclass(target):
        # Bare dataclass: wrap in DataclassSerializer transparently.
        wrapper_cls: type[drf_serializers.Serializer] = type(
            f"{target.__name__}Serializer",
            (DataclassSerializer,),
            {"Meta": type("Meta", (), {"dataclass": target})},
        )
        serializer = wrapper_cls(data=arguments)
    else:
        serializer = target(data=arguments)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _render_output(result: Any, spec: ServiceSpec) -> Any:
    """Convert ``result`` to a JSON-shaped payload using ``spec.output_serializer``.

    Falls back to the raw value if no output serializer is declared. ``None``
    becomes ``{}`` so MCP clients always receive a JSON object as
    ``structuredContent``.
    """
    if spec.output_serializer is None:
        return {} if result is None else result
    return spec.output_serializer(result).data


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

        try:
            validated: Any = _validate_input(arguments_raw, binding.spec)
        except drf_serializers.ValidationError as exc:
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(exc.detail, arguments_raw),
            )

        pool: dict[str, Any] = {
            "request": drf_request,
            "user": context.token.user,
            "data": validated,
        }
        # Per-spec kwargs provider (sister repo 0.6+): the spec author may declare
        # extra kwargs that get merged into the pool. Provider receives a
        # ``ServiceView``-shaped object — we synthesise one because MCP doesn't
        # have a real DRF view.
        if binding.spec.kwargs is not None:
            view = MCPServiceView(request=drf_request, action=binding.name)
            pool.update(binding.spec.kwargs(view, drf_request))

        try:
            kwargs: dict[str, Any] = resolve_callable_kwargs(binding.spec.service, pool)
            result: Any = run_service(binding.spec.service, kwargs, atomic=binding.spec.atomic)
        except ServiceValidationError as exc:
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                exc.message,
                data=validation_error_data(exc.detail, arguments_raw),
            )
        except ServiceError as exc:
            # ``ServiceError`` is the "real failure" channel — record it on
            # the active span when the consumer has opted in. ``ServiceValidationError``
            # is deliberately not recorded; it's input-shape feedback, not a
            # server fault, and would clutter alerting pipelines.
            if get_setting("RECORD_SERVICE_EXCEPTIONS"):
                otel_span.record_exception(exc)
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, exc.message)

        if binding.spec.output_selector is not None:
            sel_pool: dict[str, Any] = {
                "request": drf_request,
                "user": context.token.user,
                "instance": result,
                "result": result,
            }
            sel_kwargs: dict[str, Any] = resolve_callable_kwargs(
                binding.spec.output_selector, sel_pool
            )
            result = run_selector(binding.spec.output_selector, sel_kwargs)

        payload: Any = _render_output(result, binding.spec)
        output_format: OutputFormat = OutputFormat.coerce(
            params.get("outputFormat") or binding.output_format
        )
        return build_tool_result(payload, output_format=output_format).to_dict()


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
