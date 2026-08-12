from __future__ import annotations

from typing import Any

from rest_framework_services import (
    OfflineServiceView,
    build_offline_context,
    resolve_callable_kwargs,
)

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp._compat.utils import arun_selector_sync_safe
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    resolve_bound,
    resource_cache_hints,
    resource_not_found_code,
)
from rest_framework_mcp.output.build_resource_contents import build_resource_contents
from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


async def handle_resources_read_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of :func:`handle_resources_read`.

    Same shape, but nothing that can touch the ORM runs on the event loop: the
    selector goes through :func:`arun_selector_sync_safe` (async selectors run
    native, sync ones are bridged) and the render + encode step through
    :func:`acall`, because a selector returning a queryset returns it **lazy**
    and the serializer is what evaluates it.
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
        # See the sync sibling: ``-32002`` + ``data.uri`` is the spec's shape.
        return JsonRpcError(
            resource_not_found_code(context.protocol_version),
            f"Unknown resource: {uri!r}",
            data={"uri": uri},
        )
    binding, vars_ = resolved

    with span(
        "mcp.resources.read",
        attributes={**_span_attrs(binding.name, context), "mcp.resource.uri": uri},
    ):
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
            # See the sync sibling: a resource URI *is* a locator, so per-call
            # read-shaping belongs in its URI template.
            query_params={},
        ).request

        pool: dict[str, Any] = {
            "request": drf_request,
            "user": context.token.user,
            **vars_,
        }
        # URI-template variables ride on ``view.kwargs`` so a provider (and the
        # output serializer's context) reads them without re-parsing the URI.
        view = OfflineServiceView(request=drf_request, action=binding.name, kwargs=dict(vars_))
        if binding.kwargs_provider is not None:
            # ``SelectorSpec.kwargs``, invoked by name through the keyword pool
            # as on the HTTP path. It is sync — a spec is written once for both
            # transports — and its headline use is a scoping tenant / role
            # query, so it runs off the loop.
            provider_pool: dict[str, Any] = {"view": view, "request": drf_request}
            pool.update(
                await acall(
                    binding.kwargs_provider,
                    **resolve_callable_kwargs(binding.kwargs_provider, provider_pool),
                )
            )
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
        raw: Any = await arun_selector_sync_safe(binding.selector, kwargs)

        # Rendering is ORM work: ``output_serializer(...).data`` iterates the
        # value, evaluating a lazy queryset right here. Off the loop, like the
        # selector above.
        contents = await acall(
            build_resource_contents,
            binding=binding,
            uri=uri,
            raw=raw,
            view=view,
            request=drf_request,
        )
        if isinstance(contents, JsonRpcError):
            return contents
        result: dict[str, Any] = {
            "contents": [contents.to_dict()],
            **resource_cache_hints(
                resolve_bound(binding.cache_ttl_ms, context.config.resource_cache_ttl_ms)
            ),
        }
        # See the sync sibling: an over-ceiling read has no ``isError`` envelope
        # to live in, so it is a protocol error carrying the same message.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Resource {uri!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_resources_read_async"]
