"""Selector-tool dispatch — sync + async paths to the read pipeline.

Both shapes run permission check, rate limit, ``input_serializer`` validation
and then ``dispatch_spec`` (the selector plus queryset shaping and
``filter_set``), before diverging on ``binding.kind``:

- ``LIST`` paginates when ``paginate=True`` and renders ``many=True``. The
  effective page ceiling bounds the rows either way: a page clamps to it and
  says so in its envelope, an unpaginated result refuses rather than truncate.
  Ordering is not part of this shell — an ``OrderingFilter`` on the
  ``filter_set`` declares it and ``dispatch_spec`` has already applied it.
- ``RETRIEVE`` takes ``.first()`` and renders ``many=False``; the binding
  rejects the pagination knob at construction.

That post-fetch pipeline is the differentiator from service-tool dispatch and is
owned by the tool layer, not the selector: selectors return raw, unscoped data.
"""

from __future__ import annotations

from itertools import islice
from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    DEFAULT_PAGE_SIZE,
    OfflineServiceView,
    adispatch_spec,
    base_serializer_context,
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
    is_queryset,
    paginate_output,
    render_for_audience,
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
from rest_framework_mcp.observability import get_logger
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding

logger = get_logger(__name__)


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
            # A task worker runs the sync path and its reporter writes to the
            # task record, so progress is live here too, not only in the async
            # sibling. ``None`` on an ordinary request.
            progress=context.progress,
        )
    except PermissionDenied:
        # Raised by the ``on_target_resolved`` guard against the resolved row.
        # A protocol-level FORBIDDEN, matching the service-tool path — an
        # ``isError`` result would tell the model to retry an authorization
        # decision that will not change.
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
    except ServiceValidationError as exc:
        # Tool-level failure, so an ``isError`` result the model can read and
        # self-correct from. JSON-RPC errors stay reserved for protocol faults.
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

    Querysets evaluate against the DB on ``count()`` / slicing, and Django blocks
    sync DB I/O from an async context. Only the boundary differs.
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
    """Async sibling — bridges sync collaborators via ``acall``."""
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
            # Passed explicitly rather than through ``_dispatch_kwargs``, which
            # is shared between the two siblings.
            progress=context.progress,
        )
    except PermissionDenied:
        # See the sync sibling: the object-permission guard's denial.
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
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
    for a serializer rejection, an ``isError`` tool result for a missing required
    URL kwarg.

    The ``view`` is built **once**, here, and threaded through dispatch and
    rendering: on HTTP a single view instance serves the whole request, so the
    ``view.kwargs`` a spec callable reads must be the ones a context provider
    sees too.

    Filter (ordering included) / pagination args bypass ``input_serializer``
    validation — they are shape-checked by the FilterSet and by ``int(...)``
    coercion respectively — so their names go in as ``additional_known_keys``.
    The serializer gets DRF's baseline context, as it has over HTTP.
    """
    # Split first: the value has to be in hand before the request is built, and
    # unlike the URL-kwarg split this one cannot fail.
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

    The reflected selector shape and the post-fetch knobs read their inputs
    straight from ``arguments`` rather than through ``input_serializer``, so the
    unknown-argument policy has to be told they are known — otherwise ``REJECT``
    flags a legitimate read-shaping argument. The reflected names come from the
    *same* ``spec_to_json_schema`` call that drives
    ``build_selector_tool_input_schema``, so the validation-side known set and
    the wire-side advertised schema cannot drift.
    """
    known: set[str] = set()
    # ``phase="input"`` never returns ``None``; ``or {}`` only narrows the type.
    reflected: dict[str, Any] = spec_to_json_schema(binding.spec, phase="input") or {}
    # ``ordering`` needs no entry of its own: a ``FilterSet`` declaring an
    # ``OrderingFilter`` reflects it as a property like any other filter field.
    known.update(reflected.get("properties", {}).keys())
    if binding.paginate:
        known.add("page")
        known.add("limit")
    known.update(url_kwarg.name for url_kwarg in binding.url_kwargs)
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
    """Paginate and render the shaped value ``dispatch_spec`` returned.

    ``dispatch_spec`` already ran the selector, applied queryset shaping +
    ``filter_set`` (ordering included, when the filter declares an
    ``OrderingFilter``) and, for ``RETRIEVE``, materialized via ``.first()``.
    This is the MCP-only read shell on top.
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
        # A missing row arrives as ``not_found``, or — under the spec's
        # ``allow_none`` contract — as a ``None`` value rendered as a successful
        # ``null``, the MCP analogue of HTTP's 200-with-null body.
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
        payload: Any = render_for_audience(
            binding.spec,
            instance,
            projection=binding.audience_projection,
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

    # Already ordered if it was going to be: an ``OrderingFilter`` on the spec's
    # ``filter_set`` is applied inside ``dispatch_spec``, alongside the rest of
    # the filtering, so nothing below re-orders.
    qs: Any = result.value

    # One ceiling covers both arms below: it is the most rows a selector tool
    # puts in a single result, paged or not. Only the enforcement differs — a
    # page clamps to it, an unpaginated result refuses. See
    # ``_bound_unpaginated_rows``.
    row_ceiling: int | None = resolve_bound(binding.max_page_size, config.max_page_size)

    # Rendering happens *after* the page is materialised, so a provider
    # declaring ``page`` receives the exact objects being serialised — and the
    # same object the renderer iterates, so an id-keyed batched query reuses the
    # queryset's result cache instead of issuing a second query.
    if binding.paginate:
        # The clamps, the count-before-slice and the envelope arithmetic are all
        # ``paginate_output``'s — this transport contributes only the coercion of
        # two untyped JSON arguments into the ints it takes. What a page *is* has
        # one implementation for every transport; how a malformed argument is
        # answered is the part that legitimately differs, and that is what
        # ``_coerce_int`` keeps here.
        page = paginate_output(
            qs,
            page=_coerce_int(arguments_raw.get("page"), default=1),
            limit=_coerce_int(arguments_raw.get("limit"), default=DEFAULT_PAGE_SIZE),
            max_page_size=row_ceiling,
        )
        # The projection lands on the *items*, not on the envelope that
        # wraps them: ``page`` / ``totalPages`` / ``hasNext`` are this
        # transport's own keys and belong to no serializer.
        rendered_items = render_for_audience(
            binding.spec,
            page.items,
            projection=binding.audience_projection,
            many=True,
            view=view,
            request=drf_request,
            extras={"page": page.items},
        )
        payload = page.envelope(rendered_items)
    else:
        rows, exceeded = _bound_unpaginated_rows(qs, row_ceiling)
        if exceeded is not None:
            return _render_over_row_ceiling(binding, exceeded)
        payload = render_for_audience(
            binding.spec,
            rows,
            projection=binding.audience_projection,
            many=True,
            view=view,
            request=drf_request,
            extras={"page": rows},
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

    Reached only when the spec did *not* opt into the ``allow_none`` nullable
    contract, which yields an instance with a ``None`` value instead.
    """
    return build_error_tool_result(
        f"{binding.name}: no matching instance found",
        error_type="not_found",
    ).to_dict()


def _bound_unpaginated_rows(qs: Any, max_rows: int | None) -> tuple[Any, int | None]:
    """Take at most ``max_rows`` rows off an unpaginated LIST result.

    Returns ``(rows, exceeded)``; ``exceeded`` is the ceiling when there were
    more rows than it allows, and ``None`` when the whole result fits. ``None``
    for ``max_rows`` is *no ceiling* — the value passes through untouched, which
    is what keeps a deliberately unbounded tool unbounded.

    One row past the ceiling is read, so "exactly at the ceiling" is
    distinguishable from "over it", and it is read as a **slice** so a QuerySet
    bounds the fetch in SQL. That is the point of doing this before rendering
    rather than leaning on the byte ceiling: ``MAX_RESULT_BYTES`` measures a
    payload that has already been fetched and serialised in full, so the whole
    table is in memory by the time it fires.

    Over-ceiling is a refusal rather than a silent clamp, unlike the paginated
    arm: nothing in an unpaginated payload could say that rows were dropped, so
    a truncated one reads as complete to the model reasoning from it.
    """
    if max_rows is None or not hasattr(qs, "__iter__"):
        # No ceiling, or a scalar: a selector may return one value for a LIST
        # spec, which the renderer passes through on the same ``__iter__``
        # predicate. One value is bounded already, and ``iter(None)`` is not.
        return qs, None
    # ``qs[:n]`` on a QuerySet is a LIMIT; ``len()`` then evaluates it once and
    # fills the result cache the renderer iterates. Any other iterable — a list
    # from a non-ORM selector, or a generator — is windowed with ``islice``.
    window: Any = qs[: max_rows + 1] if is_queryset(qs) else list(islice(iter(qs), max_rows + 1))
    if len(window) > max_rows:
        return window, max_rows
    return window, None


def _render_over_row_ceiling(binding: SelectorToolBinding, max_rows: int) -> dict[str, Any]:
    """Refuse an unpaginated result that would carry more rows than the ceiling."""
    # The caller is told by the result; the operator only by this. A bound that
    # fires invisibly reads to everyone else as "the tool is broken".
    logger.warning(
        "Row bound exceeded: unpaginated tool %r resolved more than the %d row ceiling",
        binding.name,
        max_rows,
    )
    return build_error_tool_result(
        f"Tool {binding.name!r} is unpaginated and resolved more than this server's "
        f"{max_rows} row ceiling. Narrow the request — add or tighten a filter — and "
        "call again. The result was not truncated: an unpaginated payload carries "
        "nothing to say rows were dropped, so a partial one would look complete. "
        "Registering the tool with paginate=True lets it be read a page at a time.",
        error_type="result_too_large",
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
    # URL kwargs already rode onto ``view.kwargs`` and query params onto
    # ``request.query_params``; strip both from the params so no value reaches
    # the selector through two channels. The split cannot fail here —
    # ``_build_request_and_validate`` ran it first.
    spec_params, _url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
    spec_params, _query_param_values = split_query_params(spec_params, binding.query_params)
    return {
        "user": context.token.user,
        "params": _selector_dispatch_params(spec_params, validated),
        # Unstripped, which is what lets a spec's ``OrderingFilter`` work at
        # all: sharing one stripped mapping made the inputSchema advertise an
        # ordering that dispatch then silently discarded.
        "filter_data": _selector_dispatch_params(
            spec_params, validated, strip_post_fetch_keys=False
        ),
        "request": drf_request,
        "view": view,
        "argument_binding": argument_binding,
        "unknown_arguments": unknown_arguments,
        # The object-permission hook, as on the service-tool path. Without it a
        # spec whose ownership test lives in ``has_object_permission`` is
        # enforced over HTTP and not here: the class-level check the binding's
        # wrapped permissions run says nothing about the *row* a RETRIEVE
        # resolved. The guard runs class-level only for a LIST, whose target is
        # a queryset rather than a model.
        "on_target_resolved": enforce_permissions,
    }


def _selector_dispatch_params(
    arguments_raw: dict[str, Any], validated: Any, *, strip_post_fetch_keys: bool = True
) -> dict[str, Any]:
    """Build a params mapping ``dispatch_spec`` receives for a selector.

    Called twice, for the two pools ``dispatch_spec`` keeps separate: ``params``
    (the selector's kwarg spread) with the strip on, ``filter_data`` (the
    ``FilterSet``'s input) with it off. The strip is about the *callable* —
    ``ordering`` / ``page`` / ``limit`` belong to the MCP read pipeline, so a
    selector taking ``**kwargs`` must not receive them, whereas a ``FilterSet``
    reads only the fields it declares, as it does on HTTP.

    The validated ``input_serializer`` values overlay the raw args either way, so
    a typed selector arg reaches the callable coerced while filter-set args
    (which bypass the serializer) keep the raw form the ``FilterSet`` wants.
    """
    core: dict[str, Any] = {
        k: v
        for k, v in arguments_raw.items()
        if not strip_post_fetch_keys or k not in RESERVED_POST_FETCH_KEYS
    }
    if isinstance(validated, dict):
        core.update(validated)
    return core


def _coerce_int(value: Any, *, default: int) -> int:
    """Best-effort int coercion, falling back to ``default``.

    Pagination args come from JSON, which gives ints — but string-shaped clients
    exist, and clamping is friendlier than 400-ing the whole call.

    Deliberately *not* upstream, and the one part of pagination that stays here.
    ``paginate_output`` takes ``page`` / ``limit`` already parsed because turning
    an untyped argument into an integer is where transports legitimately differ:
    a public MCP endpoint answers a malformed value with a clamped page, while an
    in-process toolset can hand the model its mistake back and ask again. That is
    a policy about bad input, not a statement about what a page is.
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
