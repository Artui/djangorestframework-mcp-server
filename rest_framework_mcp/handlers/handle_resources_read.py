from __future__ import annotations

from typing import Any

from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
)
from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.resource_contents import ResourceContents
from rest_framework_mcp.server.mcp_service_view import MCPServiceView


def handle_resources_read(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Read a resource (or templated-resource instance) by URI.

    Resolves the URI through the registry, builds a kwarg pool from the
    template variables and request context, runs the selector via
    :func:`run_selector` (transparently bridging async selectors), and
    returns one ``ResourceContents`` block. Output is rendered through
    ``output_serializer`` if declared, then JSON-encoded.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "resources/read params must be an object"
        )
    uri: Any = params.get("uri")
    if not isinstance(uri, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'uri' is required and must be a string"
        )

    resolved = context.resources.resolve(uri)
    if resolved is None:
        return JsonRpcError(JsonRpcErrorCode.RESOURCE_NOT_FOUND, f"Unknown resource: {uri!r}")
    binding, vars_ = resolved

    with span(
        "mcp.resources.read",
        attributes={**_span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
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
            **vars_,
        }
        if binding.kwargs_provider is not None:
            # Per-spec kwargs provider from ``SelectorSpec.kwargs``: URI-template
            # variables are exposed via ``view.kwargs`` so providers can inspect
            # them without parsing the URI again.
            view = MCPServiceView(request=drf_request, action=binding.name, kwargs=dict(vars_))
            pool.update(binding.kwargs_provider(view, drf_request))
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
        raw: Any = run_selector(binding.selector, kwargs)

        payload: Any
        if binding.output_serializer is not None:
            payload = binding.output_serializer(raw, many=isinstance(raw, list)).data
        else:
            payload = raw

        contents = ResourceContents(
            uri=uri,
            mime_type=binding.mime_type,
            text=encode_json(payload),
        )
        return {"contents": [contents.to_dict()]}


__all__ = ["handle_resources_read"]
