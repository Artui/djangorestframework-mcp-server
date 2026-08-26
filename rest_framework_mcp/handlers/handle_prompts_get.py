from __future__ import annotations

from typing import Any

from rest_framework_services import build_offline_context, resolve_callable_kwargs

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


def handle_prompts_get(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Render a registered prompt for the supplied arguments.

    Checks that required arguments are present, runs the binding's permission stack,
    then dispatches the render callable via ``resolve_callable_kwargs`` so it can
    declare any subset of ``request`` / ``user`` plus its per-prompt arguments. Whatever
    it returns is normalised into a list of
    [`PromptMessage`][rest_framework_mcp.protocol.types.prompt_message.PromptMessage].

    The ``request`` / ``user`` seeds outrank the client's arguments, so a prompt
    argument named after one of them cannot stand in for the authenticated
    identity, and the finished result is held to ``MAX_RESULT_BYTES`` like every
    other result surface.
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
        # The prompts spec is explicit: "Invalid prompt name: -32602". An
        # unknown name is a fault in the *params*, and ``-32002`` is reserved
        # for ``resources/read``.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")

    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    # Not ``or {}``: that folds every falsy value into the default, so a ``[]``,
    # ``""``, ``0`` or ``False`` would be accepted as "no arguments" by the very
    # line below that exists to reject a non-object.
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

        drf_request = build_offline_context(
            context.token.user,
            None,
            http_request=context.http_request,
            # A prompt renders messages, so nothing here reads
            # ``request.query_params``. Passed anyway so the endpoint's own
            # query string can never reach one if that changes.
            query_params={},
        ).request
        # The transport's seeds are applied *after* the client-supplied
        # arguments, so an argument named ``user`` or ``request`` shadows
        # nothing — the precedence ``RESERVED_POOL_SEEDS`` enforces on the
        # dispatch path, and the ordering ``completion/complete`` already uses.
        # A render callable declaring ``user`` therefore always receives the
        # authenticated principal, never a value the caller chose.
        pool: dict[str, Any] = {
            **arguments_raw,
            "request": drf_request,
            "user": context.token.user,
        }
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.render, pool)
        raw: Any = binding.render(**kwargs)

        try:
            messages = normalize_render_result(raw)
        except TypeError as exc:
            return JsonRpcError(JsonRpcErrorCode.INTERNAL_ERROR, str(exc))

        result: dict[str, Any] = GetPromptResult(
            messages=messages, description=binding.description
        ).to_dict()
        # The same outbound ceiling ``tools/call`` and ``resources/read``
        # apply. A rendered prompt is a result surface like any other — its
        # body is whatever a consumer's ``render`` interpolated — so an
        # operator who set the ceiling gets it here too. No ``isError``
        # envelope exists on this method, so an over-ceiling render is a
        # protocol error carrying the same remedy-naming message.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Prompt {binding.name!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_prompts_get"]
