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
    build_validated_input_serializer,
    check_permissions,
    consume_rate_limits,
    effective_rate_limits,
    read_shaping_error_result,
    refuse_missing_arguments,
    resolve_bound,
    service_error_result,
    services_dispatch_policies,
    split_query_params,
    split_url_kwargs,
    validation_error_result,
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
    early = _check_auth_and_rate_limits(binding, arguments_raw, context)
    if early is not None:
        return early

    drf_request, view, validated, serializer, error = _build_request_and_validate(
        binding, arguments_raw, context
    )
    if error is not None:
        return error

    try:
        result = dispatch_spec(
            binding.spec,
            **_dispatch_kwargs(
                binding, validated, serializer, drf_request, view, arguments_raw, context
            ),
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
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # Tool-level failure, so an ``isError`` result the model can read and
        # self-correct from. JSON-RPC errors stay reserved for protocol faults.
        # DRF's error arrives here from queryset shaping: a value the spec's
        # ``FilterSet`` refuses (an ``ordering`` outside its choices, say),
        # which escaped every arm and was served as an HTTP 500 / ``-32603``.
        return validation_error_result(
            exc, arguments_raw, config=context.config, conventions=context.conventions
        ).to_dict()
    except ServiceError as exc:
        if context.config.record_service_exceptions:
            otel_span.record_exception(exc)
        return service_error_result(exc).to_dict()

    # Rendering is where a read-shaping ``QueryParam`` is actually read — a
    # field selection, say, is applied by the output serializer, per row — so
    # a bad one fails here, after every arm above has already been passed. Both
    # siblings wrap the post-fetch call as a whole: it holds all three renders
    # (a retrieve, a page's items, an unpaginated list), and the rest of it —
    # ``paginate_output`` and building the result — raises no validation error
    # of its own. Only what the caller shaped is theirs to fix;
    # ``read_shaping_error_result`` re-raises anything else unchanged.
    try:
        return _post_fetch_and_render(
            binding, result, drf_request, view, arguments_raw, params, context.config
        )
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        return read_shaping_error_result(
            exc,
            query_params=binding.query_params,
            arguments=arguments_raw,
            paginated=binding.paginate,
            config=context.config,
            conventions=context.conventions,
        ).to_dict()


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
    early = await acall(_check_auth_and_rate_limits, binding, arguments_raw, context)
    if early is not None:
        return early

    drf_request, view, validated, serializer, error = _build_request_and_validate(
        binding, arguments_raw, context
    )
    if error is not None:
        return error

    try:
        result = await adispatch_spec(
            binding.spec,
            **_dispatch_kwargs(
                binding, validated, serializer, drf_request, view, arguments_raw, context
            ),
            # Passed explicitly rather than through ``_dispatch_kwargs``, which
            # is shared between the two siblings.
            progress=context.progress,
        )
    except PermissionDenied:
        # See the sync sibling: the object-permission guard's denial.
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # See the sync sibling for the protocol-vs-tool error boundary.
        return validation_error_result(
            exc, arguments_raw, config=context.config, conventions=context.conventions
        ).to_dict()
    except ServiceError as exc:
        if context.config.record_service_exceptions:
            otel_span.record_exception(exc)
        return service_error_result(exc).to_dict()

    # See the sync sibling. ``acall`` re-raises the worker thread's exception
    # as-is, so the same two types arrive here.
    try:
        return await _post_fetch_and_render_async(
            binding, result, drf_request, view, arguments_raw, params, context.config
        )
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        return read_shaping_error_result(
            exc,
            query_params=binding.query_params,
            arguments=arguments_raw,
            paginated=binding.paginate,
            config=context.config,
            conventions=context.conventions,
        ).to_dict()


# ---------- helpers shared between sync + async ----------


def _check_auth_and_rate_limits(
    binding: SelectorToolBinding, arguments_raw: dict[str, Any], context: MCPCallContext
) -> JsonRpcError | None:
    """Answer a call its permissions deny or its rate limits refuse, else ``None``.

    The spec's permission classes judge the route the call names: the URL
    kwargs it delivered are split out for them first, as ``call_spec_tool``
    splits them, where a permission reading ``view.kwargs`` was judged against
    ``{}`` (``test_a_spec_permission_sees_the_url_kwargs_the_call_delivers``).
    ``refuse_missing=False`` keeps this split from being the one that refuses:
    ``_build_request_and_validate`` still answers a missing required kwarg, and
    after the permission, so a caller it denies is not told which argument it
    left out
    (``test_a_permission_denying_the_delivered_route_answers_before_the_missing_argument``).
    """
    _, delivered_url_kwargs = split_url_kwargs(
        arguments_raw, binding.url_kwargs, refuse_missing=False
    )
    allowed, required_scopes = check_permissions(
        binding.permissions,
        context.http_request,
        context.token,
        view_kwargs=delivered_url_kwargs,
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
) -> tuple[Any, Any, Any, Any, dict[str, Any] | None]:
    """Build the synthesised request + view, and validate the ``input_serializer``.

    Returns ``(drf_request, view, validated, serializer, error)``, ``serializer``
    being the bound one that validated, whose fields say which URL kwarg names
    the overlay may lay back (``_route_kwargs_a_namesake_owns``); ``error`` is
    non-``None`` when the call is already answered — a ``validation_error`` tool result, for a
    serializer rejection, an unexpected argument under ``REJECT`` or a missing
    required URL kwarg alike.

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
    except drf_serializers.ValidationError as exc:
        # A missing ``required=True`` URL kwarg, refused in the shape a missing
        # selector parameter is.
        return (
            drf_request,
            None,
            None,
            None,
            validation_error_result(
                exc, arguments_raw, config=context.config, conventions=context.conventions
            ).to_dict(),
        )
    view = OfflineServiceView(request=drf_request, action=binding.name, kwargs=url_kwarg_values)
    try:
        validated, serializer = build_validated_input_serializer(
            arguments_raw,
            binding.input_serializer,
            unknown_arguments=binding.unknown_arguments,
            additional_known_keys=_selector_tool_additional_known_keys(binding),
            context=base_serializer_context(view=view, request=drf_request),
        )
    except drf_serializers.ValidationError as exc:
        # An unexpected argument or a serializer rejection: input validation,
        # which the MCP spec reports as an ``isError`` result, not ``-32602``.
        return (
            drf_request,
            view,
            None,
            None,
            validation_error_result(
                exc, arguments_raw, config=context.config, conventions=context.conventions
            ).to_dict(),
        )
    return drf_request, view, validated, serializer, None


def _selector_tool_additional_known_keys(binding: SelectorToolBinding) -> frozenset[str]:
    """Compute the keys a selector tool's pipeline knobs claim from ``arguments``.

    The reflected selector shape and the post-fetch knobs read their inputs
    straight from ``arguments`` rather than through ``input_serializer``, so the
    unknown-argument policy has to be told they are known — otherwise ``REJECT``
    flags a legitimate read-shaping argument. The reflected names come from the
    ``spec_to_json_schema`` reflection that drives
    ``build_selector_tool_input_schema``, so the validation-side known set and
    the wire-side advertised schema cannot drift.

    Read *without* ``supplied``, so it is a superset of what is advertised: a
    name the server fills is admitted when the client sends it anyway, rather
    than refused as unknown. Which value the selector then reads depends on the
    source. A seed's always, because dispatch strips a reserved name from the
    client's spread under every binding. A provider key's under
    ``SPREAD_AUTHOR_WINS``, the default, where the provider is applied last.
    Under ``SPREAD_CALLER_WINS`` the client's spread is applied last, so its
    value outranks the provider's, which is why the schema offers the
    provider's keys there instead of hiding them
    (``test_under_caller_wins_a_provider_filled_name_is_not_refused``).
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
    serializer: Any,
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
    spec_params, url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
    spec_params, _query_param_values = split_query_params(spec_params, binding.query_params)
    # The overlay can put a URL kwarg's name back: a field bound with
    # ``source="project_pk"`` validates the argument ``project`` into
    # ``project_pk``. Dropped after it, so the selector reads the route the
    # permission judged, from ``view.kwargs``, under every binding. Left in,
    # ``SPREAD_CALLER_WINS`` ranked it above the route
    # (``test_a_serializer_field_sourcing_a_url_kwarg_does_not_move_the_route``),
    # and under either spreading binding it stood in for a kwarg the call left
    # out, on a route judged as naming none
    # (``test_a_serializer_field_sourcing_a_url_kwarg_left_out_does_not_fill_the_route``).
    # Kept where the value can only be the route's own namesake field's.
    route_names = {url_kwarg.name for url_kwarg in binding.url_kwargs}
    dropped = route_names - _route_kwargs_a_namesake_owns(serializer, route_names)
    params = {
        name: value
        for name, value in _selector_dispatch_params(spec_params, validated).items()
        if name not in dropped
    }
    # Evaluated inside both siblings' dispatch ``try``, after the permission and
    # rate-limit answers and the ``input_serializer``: a missing argument is the
    # same ``validation_error`` result a refused one is. Checked against what
    # reaches the selector -- the params with the validated values laid over
    # them, plus the ``UrlKwarg`` values -- so a null ``UrlKwarg``, which the
    # split drops, counts as missing (``test_a_null_url_kwarg_is_a_missing_argument``).
    # The overlay agrees with the raw params on every binding registration
    # admits, since a name the serializer defaults is never required in the
    # first place (``schema.utils._serializer_fills``, which this route counts
    # because it runs the serializer, unlike ``call_tool``); it is read anyway so the
    # check describes the call the selector receives.
    refuse_missing_arguments(binding, (*params, *url_kwarg_values), pool_seeds=context.pool_seeds)
    return {
        "user": context.token.user,
        "params": params,
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
        # The server's registered seeds: resolved into the selector's pool, and
        # reserved, so a client argument of the same name is stripped from the
        # spread rather than outranking the project's value. A selector has no
        # validator in front of that spread, so without them the name would be
        # client-controlled.
        "pool_seeds": context.pool_seeds,
    }


def _route_kwargs_a_namesake_owns(serializer: Any, route_names: set[str]) -> frozenset[str]:
    """The URL kwargs whose laid-back value only a field of the kwarg's own name writes.

    Such a field reads the argument under the kwarg's name, which is the value
    the split routed into ``view.kwargs`` and the permission judged, so what it
    lays back is that value as the author's field coerced it, or the author's
    default for a route that left an optional kwarg out. Neither is a value the
    caller chose apart from the route, so the name stays in the selector's
    params: under ``SPREAD_CALLER_WINS`` the selector reads ``7`` for the
    route's ``"7"``
    (``test_a_field_named_after_a_url_kwarg_lays_back_its_coercion_of_the_route``),
    and a selector requiring the name gets the default rather than raising
    ``TypeError`` (``test_a_field_named_after_a_url_kwarg_defaults_a_route_left_out``).

    Owned only when the namesake is the name's sole writer among the declared
    fields. A writable field writes the name its ``source`` starts with, and a
    ``source="*"`` field merges a mapping into the top level, which can carry
    any name. The chain is one branch arc, so each condition is held by a case
    of ``test_a_value_only_a_namesake_did_not_write_does_not_move_the_route``,
    or of the coercion test above:

    - a field of the kwarg's name, where none means whatever sits under the
      name came from an alias or from the serializer's own ``validate``
      (``[validate-without-namesake]``, and
      ``test_a_serializer_field_sourcing_a_url_kwarg_does_not_move_the_route``);
    - no other field whose ``source`` names it (``[alias-beside-namesake]``);
    - no ``source="*"`` field (``[star-beside-namesake]``);
    - read-only fields set aside, since they write nothing into the validated
      values, ``SerializerMethodField`` among them with its ``source="*"``
      (``[namesake-beside-read-only-caller-wins]`` of the coercion test).

    A ``validate`` or ``to_internal_value`` override can still write the name
    beside its namesake. That is the author's own code choosing the value, which
    no declaration shows, and it is what a field's ``validate_<name>`` hook is
    for in any case.
    """
    if serializer is None:
        return frozenset()
    fields = serializer.fields
    owned: set[str] = set()
    for name in route_names:
        writers = [
            field
            for field in fields.values()
            if not field.read_only and field.source_attrs[:1] in ([name], [])
        ]
        if writers == [fields.get(name)]:
            owned.add(name)
    return frozenset(owned)


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
