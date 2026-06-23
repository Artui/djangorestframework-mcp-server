"""Selector-tool dispatch — sync + async paths to the read pipeline.

Two shapes, gated by ``binding.kind``:

``LIST`` (subset of steps optional, gated by binding flags):

.. code-block:: text

    arguments → permission check → rate limit → validate(input_serializer)
              → run_selector
              → FilterSet(data=filter_args, queryset=qs).qs    if filter_set
              → qs.order_by(<field>)                            if ordering_fields
              → paginate                                        if paginate=True
              → output_serializer(many=True)
              → ToolResult

``RETRIEVE`` skips ordering / pagination (the binding rejects those
knobs at construction) but still applies queryset shaping + ``filter_set``
before the single-instance ``.first()``, then renders:

.. code-block:: text

    arguments → permission check → rate limit → validate(input_serializer)
              → run_selector
              → shape + FilterSet(data=…).qs            (if a queryset)
              → .first()
              → output_serializer(many=False)
              → ToolResult

The post-fetch pipeline is the differentiator from service-tool
dispatch and is owned by the **tool layer**, not the selector.
Selectors return raw, unscoped data (queryset for ``LIST``,
single instance for ``RETRIEVE``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services import (
    adispatch_spec,
    dispatch_spec,
    filterset_to_json_schema,
    is_queryset,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import (
    RESERVED_POST_FETCH_KEYS,
    JsonRpcErrorCode,
    OutputFormat,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
    invoke_context_provider,
    services_dispatch_policies,
    validate_input_against_serializer,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView


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

    try:
        result = dispatch_spec(
            binding.spec,
            **_dispatch_kwargs(binding, validated, drf_request, arguments_raw, context),
        )
    except ServiceValidationError as exc:
        # Tool-level failure → ``isError`` result the model can read and
        # self-correct from; JSON-RPC errors stay reserved for protocol
        # faults (bad params shape, unknown tool, auth, rate limits).
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail=validation_error_data(exc.detail, arguments_raw),
        ).to_dict()
    except ServiceError as exc:
        if get_setting("RECORD_SERVICE_EXCEPTIONS"):
            otel_span.record_exception(exc)
        return build_error_tool_result(exc.message, error_type="service_error").to_dict()

    return _post_fetch_and_render(binding, result, drf_request, arguments_raw, params)


async def _post_fetch_and_render_async(
    binding: SelectorToolBinding,
    result: Any,
    drf_request: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Bridge the sync post-fetch pipeline through ``sync_to_async``.

    Django querysets evaluate against the DB on ``count()`` / slicing, and
    Django blocks sync DB I/O from an async context unless we route it
    through a thread. The pipeline itself is unchanged — only the
    async-context boundary differs.
    """
    return await acall(_post_fetch_and_render, binding, result, drf_request, arguments_raw, params)


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

    try:
        result = await adispatch_spec(
            binding.spec,
            **_dispatch_kwargs(binding, validated, drf_request, arguments_raw, context),
        )
    except ServiceValidationError as exc:
        # See the sync sibling for the protocol-vs-tool error boundary.
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail=validation_error_data(exc.detail, arguments_raw),
        ).to_dict()
    except ServiceError as exc:
        if get_setting("RECORD_SERVICE_EXCEPTIONS"):
            otel_span.record_exception(exc)
        return build_error_tool_result(exc.message, error_type="service_error").to_dict()

    return await _post_fetch_and_render_async(binding, result, drf_request, arguments_raw, params)


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
    Their names are passed in as ``additional_known_keys`` so the
    unknown-argument policy doesn't flag them as unrecognised.
    """
    drf_request = build_internal_drf_request(
        context.http_request, user=context.token.user, data=arguments_raw
    )
    try:
        validated = validate_input_against_serializer(
            arguments_raw,
            binding.input_serializer,
            unknown_arguments=binding.unknown_arguments,
            additional_known_keys=_selector_tool_additional_known_keys(binding),
        )
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


def _selector_tool_additional_known_keys(binding: SelectorToolBinding) -> frozenset[str]:
    """Compute the keys a selector tool's pipeline knobs claim from ``arguments``.

    The post-fetch pipeline (filter / order / paginate) reads its inputs
    directly from ``arguments`` rather than going through
    ``input_serializer``. Surfacing them here lets the unknown-argument
    policy treat them as "known" without forcing every selector binding to
    restate them on its serializer. Filter-set property names come from
    ``rest_framework_services.filterset_to_json_schema`` so an arbitrary
    ``django-filter`` shape is supported.

    Returns a ``frozenset`` for cheap union with the serializer's
    declared fields.
    """
    known: set[str] = set()
    if binding.filter_set is not None:
        # Same helper that drives ``inputSchema`` generation, so the
        # validation-side known set and the wire-side advertised schema
        # never drift.
        known.update(filterset_to_json_schema(binding.filter_set).keys())
    if binding.ordering_fields:
        known.add("ordering")
    if binding.paginate:
        known.add("page")
        known.add("limit")
    return frozenset(known)


def _post_fetch_and_render(
    binding: SelectorToolBinding,
    result: DispatchResult,
    drf_request: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    """Order → paginate → render the shaped value ``dispatch_spec`` returned.

    ``dispatch_spec`` already ran the selector, applied queryset shaping +
    ``filter_set``, and (for ``RETRIEVE``) materialized via ``.first()`` /
    resolved the ``allow_none`` contract. This is the MCP-only read shell on
    top: ordering, pagination, and output rendering — owned by the tool layer,
    not the selector.
    """
    output_format: OutputFormat = OutputFormat.coerce(
        params.get("outputFormat") or binding.output_format
    )
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
    )

    if binding.kind is SelectorKind.RETRIEVE:
        # ``dispatch_spec`` routes a missing row to ``not_found`` (or, under the
        # spec's ``allow_none`` contract, to a ``None`` value rendered as a
        # successful ``null`` — the MCP analogue of HTTP's 200-with-null body).
        if result.kind == "not_found":
            return _render_missing_instance(binding)
        instance = result.value
        if instance is None:
            return build_tool_result(
                None,
                output_format=output_format,
                include_structured_content=emit_structured_content,
            ).to_dict()
        context: Mapping[str, Any] | None = _resolve_output_context(
            binding, drf_request, extras={"instance": instance}
        )
        payload: Any = _render_single(instance, binding, context=context)
        return build_tool_result(
            payload,
            output_format=output_format,
            include_structured_content=emit_structured_content,
        ).to_dict()

    qs: Any = result.value

    # Ordering — only on QS-shapes that support ``.order_by()``.
    if binding.ordering_fields and is_queryset(qs):
        ordering: Any = arguments_raw.get("ordering")
        if isinstance(ordering, str) and _is_valid_ordering(ordering, binding.ordering_fields):
            qs = qs.order_by(ordering)

    # Output-serializer context (sister-repo 0.12+; resolved-data extras in
    # 0.15+). Resolved *after* the page is materialised so a provider
    # declaring ``page`` receives the exact objects being serialised — and
    # the same object the renderer iterates, so an id-keyed batched query
    # reuses the queryset's result cache instead of issuing a second query.
    # ``None`` from the spec → no context.
    #
    # Pagination — wraps the response in ``{items, page, totalPages, hasNext}``.
    if binding.paginate:
        page_no, limit, page_items, total = _slice_for_pagination(qs, arguments_raw)
        context = _resolve_output_context(binding, drf_request, extras={"page": page_items})
        rendered_items = _render_collection(page_items, binding, context=context)
        total_pages: int = max(1, -(-total // limit))  # ceil divide
        payload = {
            "items": rendered_items,
            "page": page_no,
            "totalPages": total_pages,
            "hasNext": page_no < total_pages,
        }
    else:
        context = _resolve_output_context(binding, drf_request, extras={"page": qs})
        payload = _render_collection(qs, binding, context=context)
    return build_tool_result(
        payload,
        output_format=output_format,
        include_structured_content=emit_structured_content,
    ).to_dict()


def _render_missing_instance(binding: SelectorToolBinding) -> dict[str, Any]:
    """Render the RETRIEVE not-found case as a tool-level ``isError`` result.

    Reached only when ``dispatch_spec`` returns ``kind="not_found"`` — i.e. the
    spec did **not** opt into the ``allow_none`` nullable-resource contract (that
    contract yields ``kind="instance"`` with a ``None`` value, rendered as a
    successful ``null`` by the caller). A missing row the model can read and
    self-correct from.
    """
    return build_error_tool_result(
        f"{binding.name}: no matching instance found",
        error_type="not_found",
    ).to_dict()


def _dispatch_kwargs(
    binding: SelectorToolBinding,
    validated: Any,
    drf_request: Any,
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any]:
    """Keyword args for ``dispatch_spec`` / ``adispatch_spec`` on a selector tool."""
    argument_binding, unknown_arguments = services_dispatch_policies(binding)
    return {
        "user": context.token.user,
        "params": _selector_dispatch_params(arguments_raw, validated),
        "request": drf_request,
        "view": MCPServiceView(request=drf_request, action=binding.name),
        "argument_binding": argument_binding,
        "unknown_arguments": unknown_arguments,
    }


def _selector_dispatch_params(arguments_raw: dict[str, Any], validated: Any) -> dict[str, Any]:
    """Build the ``params`` ``dispatch_spec`` receives for a selector.

    ``dispatch_spec`` uses one ``params`` mapping for both the selector's kwarg
    spread *and* the ``filter_set`` data, so this folds the two MCP sources into
    one: the post-fetch knobs (``ordering`` / ``page`` / ``limit``) are stripped
    (they're the MCP pipeline's, not the selector's), and the binding's
    validated/coerced ``input_serializer`` values overlay the raw args — so a
    typed selector arg reaches the callable coerced while filter-set args (which
    bypass the serializer) keep their raw form for the ``FilterSet``. A
    well-behaved ``FilterSet`` ignores ``None`` / absent values, so optional
    filters left unset don't narrow the result.
    """
    core: dict[str, Any] = {
        k: v for k, v in arguments_raw.items() if k not in RESERVED_POST_FETCH_KEYS
    }
    if isinstance(validated, dict):
        core.update(validated)
    return core


def _is_valid_ordering(value: str, allowed: tuple[str, ...]) -> bool:
    """``ordering=created_at`` and ``ordering=-created_at`` both pass."""
    return value.lstrip("-") in allowed


def _slice_for_pagination(qs: Any, arguments_raw: dict[str, Any]) -> tuple[int, int, Any, int]:
    """Return ``(page, limit, page_slice, total)``.

    ``total`` uses ``.count()`` for QuerySet shapes and ``len(...)`` for
    plain sequences (lists / tuples). ``page`` / ``limit`` default to 1 /
    100; non-positive values are clamped to 1.

    The shape is discriminated with the sister-repo's :func:`is_queryset`
    predicate, **not** ``hasattr(qs, "count")``: ``list`` / ``tuple`` also
    expose ``.count`` —
    but it's ``.count(value)`` (counts occurrences) and needs an argument,
    so the old guard turned a list-returning paginated selector into an
    opaque ``count() takes exactly one argument (0 given)``. A selector
    that returns neither a QuerySet nor a sized, sliceable sequence (e.g. a
    generator or a scalar) raises a clear error instead.
    """
    page_no: int = max(1, _coerce_int(arguments_raw.get("page"), default=1))
    limit: int = max(1, _coerce_int(arguments_raw.get("limit"), default=100))
    if is_queryset(qs):
        total: int = qs.count()
    elif hasattr(qs, "__len__") and hasattr(qs, "__getitem__"):
        total = len(qs)  # plain sequence — paginate it in-memory
    else:
        raise TypeError(
            "A paginated LIST selector tool must return a QuerySet or a sized, "
            f"sliceable sequence (list / tuple); got {type(qs).__name__}. Set "
            "paginate=False or return a sliceable collection."
        )
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


def _render_collection(
    items: Any,
    binding: SelectorToolBinding,
    *,
    context: Mapping[str, Any] | None = None,
) -> Any:
    """Render a collection through the binding's output serializer.

    Falls back to a list/passthrough when no serializer is declared. List
    materialisation happens here because querysets aren't JSON-serialisable
    directly. ``context`` is forwarded to the serializer when present so
    sister-repo's ``output_serializer_context`` callable participates in
    field rendering (e.g. hyperlinked relations needing the request).
    """
    output_serializer: type | None = binding.spec.output_serializer
    if output_serializer is None:
        # No serializer — best-effort serialise. List-coerce so a queryset
        # gets evaluated; clients receive an array of dicts only if the
        # selector itself returns dict-shaped items.
        return list(items) if hasattr(items, "__iter__") else items
    if context is None:
        return output_serializer(items, many=True).data
    return output_serializer(items, many=True, context=dict(context)).data


def _render_single(
    instance: Any,
    binding: SelectorToolBinding,
    *,
    context: Mapping[str, Any] | None = None,
) -> Any:
    """Render a single instance through the binding's output serializer.

    Used for ``kind=RETRIEVE``. Falls back to passing ``instance``
    through unchanged when no serializer is declared — the JSON encoder
    then handles primitives / dicts directly. ``context`` follows the
    same sister-repo contract as :func:`_render_collection`.
    """
    output_serializer: type | None = binding.spec.output_serializer
    if output_serializer is None:
        return instance
    if context is None:
        return output_serializer(instance, many=False).data
    return output_serializer(instance, many=False, context=dict(context)).data


def _resolve_output_context(
    binding: SelectorToolBinding,
    drf_request: Any,
    *,
    extras: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    """Invoke ``spec.output_serializer_context(view, request[, **extras])`` if declared.

    ``extras`` carries the resolved data about to be serialised — the
    ``instance`` for a RETRIEVE tool, the ``page`` for a LIST tool. The
    provider receives only the names it declares; legacy ``(view, request)``
    providers are unaffected. See
    :func:`rest_framework_mcp.handlers.utils.invoke_context_provider`.
    """
    spec = binding.spec
    if spec.output_serializer_context is None:
        return None
    view = MCPServiceView(request=drf_request, action=binding.name)
    return invoke_context_provider(spec.output_serializer_context, view, drf_request, extras=extras)


__all__ = ["dispatch_selector_tool", "dispatch_selector_tool_async"]
