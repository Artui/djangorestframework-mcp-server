"""Selector-tool dispatch — sync + async paths to the read pipeline.

The pipeline (any subset of which is optional, gated by binding flags):

.. code-block:: text

    arguments → permission check → rate limit → validate(input_serializer)
              → run_selector
              → FilterSet(data=filter_args, queryset=qs).qs    if filter_set
              → qs.order_by(<field>)                            if ordering_fields
              → paginate                                        if paginate=True
              → output_serializer(many=True)
              → ToolResult

The post-fetch pipeline (filter / order / paginate) is the differentiator
from service-tool dispatch and is owned by the **tool layer**, not the
selector. Selectors return raw, unscoped querysets.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.selectors.utils import run_selector
from rest_framework_services.views.utils import resolve_callable_kwargs

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.utils import arun_selector_sync_safe
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.handlers.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
    validate_input_against_serializer,
    validation_error_data,
)
from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.registry.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.server.mcp_service_view import MCPServiceView


def dispatch_selector_tool(
    binding: SelectorToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Sync dispatch through the selector-tool pipeline."""
    early = _check_auth_and_rate_limits(binding, context)
    if early is not None:
        return early

    drf_request, validated, error = _build_request_and_validate(binding, arguments_raw, context)
    if error is not None:
        return error

    pool: dict[str, Any] = _build_kwarg_pool(binding, drf_request, context, validated)

    try:
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
        result: Any = run_selector(binding.selector, kwargs)
    except ServiceValidationError as exc:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            exc.message,
            data=validation_error_data(exc.detail, arguments_raw),
        )
    except ServiceError as exc:
        if get_setting("RECORD_SERVICE_EXCEPTIONS"):
            otel_span.record_exception(exc)
        return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, exc.message)

    return _post_fetch_and_render(binding, result, arguments_raw, params)


async def _post_fetch_and_render_async(
    binding: SelectorToolBinding,
    result: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Bridge the sync post-fetch pipeline through ``sync_to_async``.

    Django querysets evaluate against the DB on ``count()`` / slicing, and
    Django blocks sync DB I/O from an async context unless we route it
    through a thread. The pipeline itself is unchanged — only the
    async-context boundary differs.
    """
    return await acall(_post_fetch_and_render, binding, result, arguments_raw, params)


async def dispatch_selector_tool_async(
    binding: SelectorToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling — bridges sync collaborators via :func:`acall`."""
    early = await acall(_check_auth_and_rate_limits, binding, context)
    if early is not None:
        return early

    drf_request, validated, error = _build_request_and_validate(binding, arguments_raw, context)
    if error is not None:
        return error

    pool: dict[str, Any] = _build_kwarg_pool(binding, drf_request, context, validated)

    try:
        kwargs: dict[str, Any] = resolve_callable_kwargs(binding.selector, pool)
        result: Any = await arun_selector_sync_safe(binding.selector, kwargs)
    except ServiceValidationError as exc:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            exc.message,
            data=validation_error_data(exc.detail, arguments_raw),
        )
    except ServiceError as exc:
        if get_setting("RECORD_SERVICE_EXCEPTIONS"):
            otel_span.record_exception(exc)
        return JsonRpcError(JsonRpcErrorCode.SERVER_ERROR, exc.message)

    return await _post_fetch_and_render_async(binding, result, arguments_raw, params)


# ---------- helpers shared between sync + async ----------


def _check_auth_and_rate_limits(
    binding: SelectorToolBinding, context: MCPCallContext
) -> JsonRpcError | None:
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
    return None


def _build_request_and_validate(
    binding: SelectorToolBinding,
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> tuple[Any, Any, JsonRpcError | None]:
    """Build a synthesised DRF request + validate the ``input_serializer`` portion.

    Filter / ordering / pagination args bypass ``input_serializer``
    validation — they're shape-checked at runtime by the FilterSet, the
    ordering enum at dispatch, and ``int(...)`` coercion respectively.
    """
    drf_request = build_internal_drf_request(
        context.http_request, user=context.token.user, data=arguments_raw
    )
    try:
        validated = validate_input_against_serializer(arguments_raw, binding.input_serializer)
    except drf_serializers.ValidationError as exc:
        return (
            drf_request,
            None,
            JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(exc.detail, arguments_raw),
            ),
        )
    return drf_request, validated, None


def _build_kwarg_pool(
    binding: SelectorToolBinding,
    drf_request: Any,
    context: MCPCallContext,
    validated: Any,
) -> dict[str, Any]:
    pool: dict[str, Any] = {
        "request": drf_request,
        "user": context.token.user,
    }
    if validated is not None:
        pool["data"] = validated
    if binding.spec.kwargs is not None:
        view = MCPServiceView(request=drf_request, action=binding.name)
        pool.update(binding.spec.kwargs(view, drf_request))
    return pool


def _post_fetch_and_render(
    binding: SelectorToolBinding,
    result: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Apply filter → order → paginate, then render via output_serializer."""
    qs: Any = result

    # Filter — only when both binding and the QS-shape support it. Plain
    # lists / scalars fall through unchanged.
    if binding.filter_set is not None and _is_queryset_like(qs):
        qs = _apply_filter_set(binding.filter_set, qs, arguments_raw)

    # Ordering — only on QS-shapes that support ``.order_by()``.
    if binding.ordering_fields and _is_queryset_like(qs):
        ordering: Any = arguments_raw.get("ordering")
        if isinstance(ordering, str) and _is_valid_ordering(ordering, binding.ordering_fields):
            qs = qs.order_by(ordering)

    # Pagination — wraps the response in ``{items, page, totalPages, hasNext}``.
    if binding.paginate:
        page_no, limit, page_items, total = _slice_for_pagination(qs, arguments_raw)
        rendered_items = _render_collection(page_items, binding)
        total_pages: int = max(1, -(-total // limit))  # ceil divide
        payload: dict[str, Any] = {
            "items": rendered_items,
            "page": page_no,
            "totalPages": total_pages,
            "hasNext": page_no < total_pages,
        }
    else:
        payload = _render_collection(qs, binding)  # type: ignore[assignment]

    output_format: OutputFormat = OutputFormat.coerce(
        params.get("outputFormat") or binding.output_format
    )
    return build_tool_result(payload, output_format=output_format).to_dict()


def _is_queryset_like(value: Any) -> bool:
    """Cheap check for queryset-shape (``.filter``, ``.order_by``, ``.count``).

    Avoids importing Django's ``QuerySet`` directly so that a selector can
    return a list-shaped collection without breaking dispatch.
    """
    return all(hasattr(value, attr) for attr in ("filter", "order_by", "count"))


def _apply_filter_set(filter_set_class: Any, qs: Any, arguments_raw: dict[str, Any]) -> Any:
    """Run the FilterSet against the args and return the filtered queryset.

    Skips reserved keys (``ordering`` / ``page`` / ``limit``) and any
    value that's ``None`` so optional filter args don't accidentally
    narrow the queryset.
    """
    filter_data: dict[str, Any] = {
        k: v for k, v in arguments_raw.items() if k not in _RESERVED_KEYS and v is not None
    }
    fs = filter_set_class(data=filter_data, queryset=qs)
    return fs.qs


def _is_valid_ordering(value: str, allowed: tuple[str, ...]) -> bool:
    """``ordering=created_at`` and ``ordering=-created_at`` both pass."""
    return value.lstrip("-") in allowed


def _slice_for_pagination(qs: Any, arguments_raw: dict[str, Any]) -> tuple[int, int, Any, int]:
    """Return ``(page, limit, page_slice, total)``.

    ``total`` uses ``.count()`` for queryset shapes and ``len(...)`` for
    everything else. ``page`` / ``limit`` default to 1 / 100; non-positive
    values are clamped to 1.
    """
    page_no: int = max(1, _coerce_int(arguments_raw.get("page"), default=1))
    limit: int = max(1, _coerce_int(arguments_raw.get("limit"), default=100))
    total = qs.count() if hasattr(qs, "count") else len(qs)
    start: int = (page_no - 1) * limit
    page_items: Any = qs[start : start + limit]
    return page_no, limit, page_items, total


def _coerce_int(value: Any, *, default: int) -> int:
    """Best-effort int coercion. Falls back to ``default`` on failure.

    Pagination args come from JSON, which already gives us ints — but
    string-shaped clients exist. Failing closed (clamping to ``default``)
    is friendlier than 400-ing the entire call.
    """
    if isinstance(value, bool):  # ``True`` is an ``int`` in Python; reject
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _render_collection(items: Any, binding: SelectorToolBinding) -> Any:
    """Render a collection through the binding's output serializer.

    Falls back to a list/passthrough when no serializer is declared. List
    materialisation happens here because querysets aren't JSON-serialisable
    directly.
    """
    output_serializer: type | None = binding.spec.output_serializer
    if output_serializer is None:
        # No serializer — best-effort serialise. List-coerce so a queryset
        # gets evaluated; clients receive an array of dicts only if the
        # selector itself returns dict-shaped items.
        return list(items) if hasattr(items, "__iter__") else items
    return output_serializer(items, many=True).data


# Keys that selector tools reserve for the post-fetch pipeline. Filter
# args with these names would conflict with the framework, so we surface
# the conflict by simply not forwarding them to the FilterSet (they're
# consumed by ordering / pagination instead).
_RESERVED_KEYS: frozenset[str] = frozenset({"ordering", "page", "limit"})


__all__ = ["dispatch_selector_tool", "dispatch_selector_tool_async"]
