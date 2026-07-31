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

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework_services import (
    OfflineServiceView,
    adispatch_spec,
    base_serializer_context,
    build_offline_context,
    dispatch_spec,
    is_queryset,
    render_spec_output,
    spec_to_json_schema,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.dispatch_result import DispatchResult
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import (
    RESERVED_POST_FETCH_KEYS,
    JsonRpcErrorCode,
    OutputFormat,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    effective_rate_limits,
    resolve_bound,
    services_dispatch_policies,
    split_query_params,
    split_url_kwargs,
    validate_input_against_serializer,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


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

    drf_request, view, validated, error = _build_request_and_validate(
        binding, arguments_raw, context
    )
    if error is not None:
        return error

    try:
        result = dispatch_spec(
            binding.spec,
            **_dispatch_kwargs(binding, validated, drf_request, view, arguments_raw, context),
        )
    except ServiceValidationError as exc:
        # Tool-level failure → ``isError`` result the model can read and
        # self-correct from; JSON-RPC errors stay reserved for protocol
        # faults (bad params shape, unknown tool, auth, rate limits).
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail=validation_error_data(
                exc.detail, arguments_raw, include_value=context.config.include_validation_value
            ),
        ).to_dict()
    except ServiceError as exc:
        if context.config.record_service_exceptions:
            otel_span.record_exception(exc)
        return build_error_tool_result(exc.message, error_type="service_error").to_dict()

    return _post_fetch_and_render(
        binding, result, drf_request, view, arguments_raw, params, context.config
    )


async def _post_fetch_and_render_async(
    binding: SelectorToolBinding,
    result: Any,
    drf_request: Any,
    view: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
    config: MCPConfig,
) -> dict[str, Any]:
    """Bridge the sync post-fetch pipeline through ``sync_to_async``.

    Django querysets evaluate against the DB on ``count()`` / slicing, and
    Django blocks sync DB I/O from an async context unless we route it
    through a thread. The pipeline itself is unchanged — only the
    async-context boundary differs.
    """
    return await acall(
        _post_fetch_and_render, binding, result, drf_request, view, arguments_raw, params, config
    )


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

    drf_request, view, validated, error = _build_request_and_validate(
        binding, arguments_raw, context
    )
    if error is not None:
        return error

    try:
        result = await adispatch_spec(
            binding.spec,
            **_dispatch_kwargs(binding, validated, drf_request, view, arguments_raw, context),
            # Async path only: the sync sibling has no stream to report on, and
            # ``_dispatch_kwargs`` is shared between the two.
            progress=context.progress,
        )
    except ServiceValidationError as exc:
        # See the sync sibling for the protocol-vs-tool error boundary.
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail=validation_error_data(
                exc.detail, arguments_raw, include_value=context.config.include_validation_value
            ),
        ).to_dict()
    except ServiceError as exc:
        if context.config.record_service_exceptions:
            otel_span.record_exception(exc)
        return build_error_tool_result(exc.message, error_type="service_error").to_dict()

    return await _post_fetch_and_render_async(
        binding, result, drf_request, view, arguments_raw, params, context.config
    )


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
        effective_rate_limits(binding, context), context.http_request, context.token
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
) -> tuple[Any, Any, Any, dict[str, Any] | JsonRpcError | None]:
    """Build the synthesised request + view, and validate the ``input_serializer``.

    Returns ``(drf_request, view, validated, error)``; ``error`` is non-``None``
    when the call is already answered — a JSON-RPC ``INVALID_PARAMS`` envelope
    for a serializer rejection, an ``isError`` tool result for a missing
    required URL kwarg (a tool-level failure the model can self-correct from).

    The ``view`` is built **once**, here, and threaded through dispatch and
    rendering: on HTTP a single view instance serves the whole request, so the
    ``view.kwargs`` a spec callable reads must be the ones a context provider
    sees too.

    Filter / ordering / pagination args bypass ``input_serializer``
    validation — they're shape-checked at runtime by the FilterSet, the
    ordering enum at dispatch, and ``int(...)`` coercion respectively.
    Their names are passed in as ``additional_known_keys`` so the
    unknown-argument policy doesn't flag them as unrecognised.

    The serializer is given DRF's baseline context, as it has over HTTP — a
    validator reading ``self.context["request"]`` (a user-scoped
    ``PrimaryKeyRelatedField`` queryset, an ownership check) behaves the same
    over both transports.
    """
    # Split first: the value has to be in hand before the request is built, and
    # unlike the URL-kwarg split this one cannot fail (a ``QueryParam`` has no
    # ``required``).
    _qp_params, query_param_values = split_query_params(arguments_raw, binding.query_params)
    drf_request = build_offline_context(
        context.token.user,
        arguments_raw,
        http_request=context.http_request,
        # Always passed, empty or not: this *replaces* the wrapped request's
        # ``GET``, so the MCP endpoint's own query string can never reach a
        # serializer reading ``request.query_params``.
        query_params=query_param_values,
    ).request
    try:
        # URL kwargs route through ``view.kwargs`` (from where drf-services
        # spreads them, authoritative over params), never as selector params.
        _spec_params, url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
    except ServiceValidationError as exc:
        return (
            drf_request,
            None,
            None,
            build_error_tool_result(
                exc.message,
                error_type="validation_error",
                detail=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
            ).to_dict(),
        )
    view = OfflineServiceView(request=drf_request, action=binding.name, kwargs=url_kwarg_values)
    try:
        validated = validate_input_against_serializer(
            arguments_raw,
            binding.input_serializer,
            unknown_arguments=binding.unknown_arguments,
            additional_known_keys=_selector_tool_additional_known_keys(binding),
            context=base_serializer_context(view=view, request=drf_request),
        )
    except drf_serializers.ValidationError as exc:
        return (
            drf_request,
            view,
            None,
            JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
            ),
        )
    return drf_request, view, validated, None


def _selector_tool_additional_known_keys(binding: SelectorToolBinding) -> frozenset[str]:
    """Compute the keys a selector tool's pipeline knobs claim from ``arguments``.

    The reflected selector shape (callable parameters, an ``**extras:
    Unpack[TypedDict]`` expanded per key, and the ``filter_set`` fields) and
    the post-fetch pipeline knobs (order / paginate) read their inputs directly
    from ``arguments`` rather than going through ``input_serializer``. Surfacing
    them here lets the unknown-argument policy treat them as "known" without
    forcing every selector binding to restate them on its serializer. The
    reflected names come from the *same* ``spec_to_json_schema`` call that drives
    ``build_selector_tool_input_schema``, so the validation-side known set and
    the wire-side advertised schema never drift.

    Returns a ``frozenset`` for cheap union with the serializer's
    declared fields.
    """
    known: set[str] = set()
    # ``phase="input"`` never returns ``None``; ``or {}`` only narrows the type.
    reflected: dict[str, Any] = spec_to_json_schema(binding.spec, phase="input") or {}
    known.update(reflected.get("properties", {}).keys())
    if binding.ordering_fields:
        known.add("ordering")
    if binding.paginate:
        known.add("page")
        known.add("limit")
    # Registered URL kwargs are advertised in the inputSchema and popped into
    # ``view.kwargs`` at dispatch — known, not "unknown", to the arg check.
    known.update(url_kwarg.name for url_kwarg in binding.url_kwargs)
    # Same for registered query params — advertised in the schema and popped into
    # ``request.query_params``. Without this, ``unknown_arguments=REJECT`` would
    # flag a legitimate read-shaping argument as unrecognised.
    known.update(query_param.name for query_param in binding.query_params)
    return frozenset(known)


def _post_fetch_and_render(
    binding: SelectorToolBinding,
    result: DispatchResult,
    drf_request: Any,
    view: Any,
    arguments_raw: dict[str, Any],
    params: dict[str, Any],
    config: MCPConfig,
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
        default_output_schema=config.include_output_schema,
        default_structured_content=config.include_structured_content,
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
                content_kind=binding.content_kind,
                content_mime_type=binding.content_mime_type,
                binding_name=binding.name,
            ).to_dict()
        payload: Any = render_spec_output(
            binding.spec,
            instance,
            many=False,
            view=view,
            request=drf_request,
            extras={"instance": instance},
        )
        return build_tool_result(
            payload,
            output_format=output_format,
            include_structured_content=emit_structured_content,
            content_kind=binding.content_kind,
            content_mime_type=binding.content_mime_type,
            binding_name=binding.name,
        ).to_dict()

    qs: Any = result.value

    # Ordering — only on QS-shapes that support ``.order_by()``.
    if binding.ordering_fields and is_queryset(qs):
        ordering: Any = arguments_raw.get("ordering")
        if isinstance(ordering, str) and _is_valid_ordering(ordering, binding.ordering_fields):
            qs = qs.order_by(ordering)

    # Rendering happens *after* the page is materialised, so a provider
    # declaring ``page`` receives the exact objects being serialised — and the
    # same object the renderer iterates, so an id-keyed batched query reuses the
    # queryset's result cache instead of issuing a second query.
    #
    # Pagination — wraps the response in ``{items, page, totalPages, hasNext}``.
    if binding.paginate:
        page_no, limit, page_items, total = _slice_for_pagination(
            qs, arguments_raw, resolve_bound(binding.max_page_size, config.max_page_size)
        )
        rendered_items = render_spec_output(
            binding.spec,
            page_items,
            many=True,
            view=view,
            request=drf_request,
            extras={"page": page_items},
        )
        total_pages: int = max(1, -(-total // limit))  # ceil divide
        payload = {
            "items": rendered_items,
            "page": page_no,
            "totalPages": total_pages,
            "hasNext": page_no < total_pages,
        }
    else:
        payload = render_spec_output(
            binding.spec, qs, many=True, view=view, request=drf_request, extras={"page": qs}
        )
    return build_tool_result(
        payload,
        output_format=output_format,
        include_structured_content=emit_structured_content,
        content_kind=binding.content_kind,
        content_mime_type=binding.content_mime_type,
        binding_name=binding.name,
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
    view: Any,
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any]:
    """Keyword args for ``dispatch_spec`` / ``adispatch_spec`` on a selector tool."""
    argument_binding, unknown_arguments = services_dispatch_policies(binding)
    # URL kwargs already rode onto ``view.kwargs`` (from where drf-services
    # spreads them, authoritative over params); strip them from the params so a
    # value never also reaches the selector as an ordinary input. The split
    # cannot fail here — ``_build_request_and_validate`` ran it first.
    spec_params, _url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
    # Query params rode onto ``request.query_params``; strip them here for the
    # same reason as the URL kwargs above — one value, one channel.
    spec_params, _query_param_values = split_query_params(spec_params, binding.query_params)
    return {
        "user": context.token.user,
        "params": _selector_dispatch_params(spec_params, validated),
        "request": drf_request,
        "view": view,
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


def _slice_for_pagination(
    qs: Any, arguments_raw: dict[str, Any], max_page_size: int | None
) -> tuple[int, int, Any, int]:
    """Return ``(page, limit, page_slice, total)``.

    ``total`` uses ``.count()`` for QuerySet shapes and ``len(...)`` for
    plain sequences (lists / tuples). ``page`` / ``limit`` default to 1 /
    100; non-positive values are clamped to 1, and ``limit`` is clamped
    *down* to ``max_page_size`` when one is configured.

    The upper clamp is silent by design, and safe here in a way truncating an
    unpaginated result would not be: the response carries ``totalPages`` /
    ``hasNext`` computed from the clamped ``limit``, so a model that asked for
    500 rows and got 100 is told there are more pages rather than being left to
    assume it saw everything.

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
    if max_page_size is not None:
        limit = min(limit, max_page_size)
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


__all__ = ["dispatch_selector_tool", "dispatch_selector_tool_async"]
