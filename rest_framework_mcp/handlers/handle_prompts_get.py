from __future__ import annotations

from typing import Any

from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.render_prompt_messages import normalize_render_result
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
)
from rest_framework_mcp.protocol.get_prompt_result import GetPromptResult
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode


def handle_prompts_get(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Render a registered prompt for the supplied arguments.

    Validates the spec'd shape — required arguments must be present — runs
    the binding's permission stack, then dispatches the render callable via
    :func:`resolve_callable_kwargs` so render functions can declare any
    subset of ``request`` / ``user`` plus their per-prompt arguments without
    boilerplate. Whatever shape the callable returns is normalised into a
    list of :class:`PromptMessage` instances.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "prompts/get params must be an object")
    name: Any = params.get("name")
    if not isinstance(name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.prompts.get(name)
    if binding is None:
        return JsonRpcError(JsonRpcErrorCode.RESOURCE_NOT_FOUND, f"Unknown prompt: {name!r}")

    arguments_raw: Any = params.get("arguments") or {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    missing: list[str] = [
        arg.name for arg in binding.arguments if arg.required and arg.name not in arguments_raw
    ]
    if missing:
        # Shape stays ``{"missing": [...]}`` for backward compat; only
        # the value-echo augments it.
        data: dict[str, Any] = {"missing": missing}
        if get_setting("INCLUDE_VALIDATION_VALUE"):
            data["value"] = arguments_raw
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "Missing required prompt arguments",
            data=data,
        )

    with span("mcp.prompts.get", attributes=_span_attrs(binding.name, context)):
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
            context.http_request, user=context.token.user, data=None
        )
        pool: dict[str, Any] = {
            "request": drf_request,
            "user": context.token.user,
            **arguments_raw,
        }
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.render, pool)
        raw: Any = binding.render(**kwargs)

        try:
            messages = normalize_render_result(raw)
        except TypeError as exc:
            return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

        return GetPromptResult(messages=messages, description=binding.description).to_dict()


__all__ = ["handle_prompts_get"]
