from __future__ import annotations

from typing import Any

from rest_framework_services import build_offline_context, resolve_callable_kwargs

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.render_prompt_messages import normalize_render_result
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
)
from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes
from rest_framework_mcp.protocol.types.get_prompt_result import GetPromptResult
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


async def handle_prompts_get_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of ``handle_prompts_get``.

    The render callable is dispatched via ``acall``, so an async render
    function awaits directly and a sync one runs in a thread.
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
        # See the sync sibling: the prompts spec names ``-32602`` for an
        # invalid prompt name.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")

    arguments_raw: Any = params.get("arguments") or {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    missing: list[str] = [
        arg.name for arg in binding.arguments if arg.required and arg.name not in arguments_raw
    ]
    if missing:
        data: dict[str, Any] = {"missing": missing}
        if context.config.include_validation_value:
            data["value"] = arguments_raw
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "Missing required prompt arguments",
            data=data,
        )

    with span("mcp.prompts.get", attributes=_span_attrs(binding.name, context)):
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

        drf_request = build_offline_context(
            context.token.user,
            None,
            http_request=context.http_request,
            # See the sync sibling: nothing here reads ``request.query_params``,
            # and the empty mapping keeps the endpoint's own out if that changes.
            query_params={},
        ).request
        # See the sync sibling: the transport's seeds land *after* the
        # client-supplied arguments, so an argument named ``user`` or
        # ``request`` shadows nothing.
        pool: dict[str, Any] = {
            **arguments_raw,
            "request": drf_request,
            "user": context.token.user,
        }
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.render, pool)
        raw: Any = await acall(binding.render, **kwargs)

        try:
            messages = normalize_render_result(raw)
        except TypeError as exc:
            return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

        result: dict[str, Any] = GetPromptResult(
            messages=messages, description=binding.description
        ).to_dict()
        # See the sync sibling: a rendered prompt is bounded by
        # ``MAX_RESULT_BYTES`` like every other result surface.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Prompt {binding.name!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_prompts_get_async"]
