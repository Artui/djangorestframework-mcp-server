from __future__ import annotations

from typing import Any

from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    UNSET,
    base_pool,
    resolve_callable_kwargs,
    run_selector,
)

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.auth.permissions.utils import DispatchShape
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.guard_resource_object import guard_resource_object
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    resolve_bound,
    resource_cache_hints,
    resource_not_found_code,
    resource_shape,
)
from rest_framework_mcp.output.build_resource_contents import build_resource_contents
from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


def handle_resources_read(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Read a resource (or templated-resource instance) by URI.

    Resolves the URI through the registry, builds a kwarg pool from the template
    variables and request context, runs the selector via ``run_selector``
    (which bridges async selectors), guards the resolved value with the
    resource's object-level permissions, and returns one ``ResourceContents``
    block rendered and encoded by ``build_resource_contents``.

    **This method does not route through ``dispatch_spec``.** A
    [`ResourceBinding`][rest_framework_mcp.registry.types.resource_binding.ResourceBinding]
    holds a bare selector callable, not the spec it was lifted from, so there
    is no spec to dispatch. What ``dispatch_spec`` would have contributed is
    named explicitly here instead: the reserved seeds outrank the client's
    URI-template variables, the ``kwargs`` provider honours its ``UNSET``
    decline contract, and
    [`guard_resource_object`][rest_framework_mcp.handlers.guard_resource_object.guard_resource_object]
    stands in for ``on_target_resolved``. Queryset shaping and the spec's
    ``preconditions`` are **not** reproduced — they never survive registration.
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
        # ``-32002`` with the URI echoed in ``data`` is the spec's own worked
        # example, so a client special-casing resource not-found finds both
        # halves where it expects them.
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
        # The check and the view the read dispatches with are built from one
        # shape, so they judge one request: the URI's variables in
        # ``view.kwargs``, which a permission scoping by
        # ``view.kwargs["project_pk"]`` once saw as ``{}``
        # (``test_a_resource_permission_sees_the_uri_variables_of_the_read``),
        # the resource's name in ``view.action`` and an empty query string
        # (``test_a_resource_permission_reads_one_request_in_both_checks``).
        shape: DispatchShape = resource_shape(binding, vars_)
        allowed, required_scopes = check_permissions(
            binding.permissions, context.http_request, context.token, shape=shape
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

        # URI-template variables ride on ``view.kwargs`` so a provider (and
        # the output serializer's context) reads them without re-parsing the
        # URI. ``auth`` beside ``user``, as on a tool's dispatch view: reading
        # ``request.auth`` on a view without it reset the caller to
        # ``AnonymousUser``, so a ``TokenHasScope``-style permission the check
        # admitted refused every caller at the guard.
        offline = shape.build(
            user=context.token.user, auth=context.token.raw, http_request=context.http_request
        )
        drf_request = offline.request
        view = offline.view

        # The transport's seeds land *after* the URI-template variables, so a
        # template variable named ``user`` or ``request`` -- or after a seed the
        # server's ``pool_seeds=`` registers -- cannot stand in for the value
        # the transport resolved. ``register_resource`` already refuses such a
        # template; this is the second lock, for a binding registered straight
        # onto the registry. Built through ``base_pool``, as drf-services asks
        # of every adapter assembling its own pool, so a selector reading a
        # registered seed receives it here as it does on a selector tool.
        pool: dict[str, Any] = {
            **vars_,
            **base_pool(user=context.token.user, request=drf_request, seeds=context.pool_seeds),
        }
        if binding.kwargs_provider is not None:
            # ``SelectorSpec.kwargs``, invoked through the keyword pool exactly
            # as drf-services invokes it on the HTTP path: by name, so
            # ``def kwargs(request): ...`` works here too — including its
            # decline contract, so a provider that cannot resolve a key
            # off-HTTP returns ``UNSET`` and leaves the URI's own value
            # standing, rather than overwriting it with the sentinel.
            provider_pool: dict[str, Any] = {"view": view, "request": drf_request}
            provided: dict[str, Any] = binding.kwargs_provider(
                **resolve_callable_kwargs(binding.kwargs_provider, provider_pool)
            )
            pool.update({key: value for key, value in provided.items() if value is not UNSET})
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
        raw: Any = run_selector(binding.selector, kwargs)

        # Object-level permissions, on the row the selector actually resolved.
        # The class-level pass above cannot see it, and this method has no
        # ``dispatch_spec`` to carry ``on_target_resolved`` for it.
        try:
            guard_resource_object(binding, raw, offline)
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")

        contents = build_resource_contents(
            binding=binding, uri=uri, raw=raw, view=view, request=drf_request
        )
        if isinstance(contents, JsonRpcError):
            return contents
        result: dict[str, Any] = {
            "contents": [contents.to_dict()],
            **resource_cache_hints(
                resolve_bound(binding.cache_ttl_ms, context.config.resource_cache_ttl_ms)
            ),
        }
        # Same outbound ceiling as a tool result, different envelope: a resource
        # read has no ``isError`` shape to carry the explanation, so this is a
        # protocol error carrying the same remedy-naming message.
        oversize: str | None = enforce_result_bytes(
            result, context.config.max_result_bytes, label=f"Resource {uri!r}"
        )
        if oversize is not None:
            return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, oversize)
        return result


__all__ = ["handle_resources_read"]
