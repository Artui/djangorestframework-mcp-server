"""Selector-tool dispatch — sync + async paths to the read pipeline.

Both shapes run the binding's permission check, rate limit, the spec's own
class-level permission check, ``input_serializer`` validation and then
``dispatch_spec`` (the selector plus queryset shaping and ``filter_set``, with
the object-level check on a resolved row), before diverging on
``binding.kind``:

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

import dataclasses
from itertools import islice
from typing import Any

from django.db.models import Model
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    DEFAULT_PAGE_SIZE,
    OfflineContext,
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
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

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
from rest_framework_mcp.schema.utils import laid_back_inputs

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

    # Off the event loop as a whole: it judges the spec's ``permission_classes``,
    # and a ``has_permission`` that queries raises ``SynchronousOnlyOperation``
    # on the loop.
    drf_request, view, validated, serializer, error = await acall(
        _build_request_and_validate, binding, arguments_raw, context
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
) -> tuple[Any, Any, Any, Any, dict[str, Any] | JsonRpcError | None]:
    """Build the synthesised request + view, judge the spec, validate the ``input_serializer``.

    Returns ``(drf_request, view, validated, serializer, error)``, ``serializer``
    being the bound one that validated, whose fields supply the defaults for URL
    kwargs the call left out (``_url_kwarg_defaults``); ``error`` is non-``None``
    when the call is already answered — ``FORBIDDEN`` for a caller the spec's
    ``permission_classes`` deny, else a ``validation_error`` tool result, for
    a serializer rejection, an unexpected argument under ``REJECT`` or a missing
    required URL kwarg alike.

    The spec's class-level check runs here, against the request and view the
    selector will run with, before the lookup and before anything names a
    missing argument. The binding's wrapped copy of those classes judged a
    stand-in with no ``action`` and the MCP endpoint's own ``request.data``, so
    a ``has_permission`` reading either was judged on this request only by the
    target guard, after the lookup: a denied caller was answered ``-32006`` for
    a row that exists and ``not_found`` for one that does not, and told which
    argument it left out
    (``test_a_denied_caller_is_answered_alike_for_a_row_that_exists_and_one_that_does_not``,
    ``test_a_denied_caller_is_not_told_which_argument_it_left_out``). The guard
    is ``enforce_object_permissions`` for that reason.

    The ``view`` is built **once**, here, and threaded through dispatch and
    rendering: on HTTP a single view instance serves the whole request, so the
    ``view.kwargs`` a spec callable reads must be the ones a context provider
    sees too. Built from the URL kwargs as delivered, which are the ones the
    call runs with whenever it is not refused for a missing one.

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
    # URL kwargs route through ``view.kwargs`` (from where drf-services spreads
    # them, authoritative over params), never as selector params. Split without
    # refusing a missing one, so the spec judges the route first.
    _spec_params, url_kwarg_values = split_url_kwargs(
        arguments_raw, binding.url_kwargs, refuse_missing=False
    )
    view = OfflineServiceView(request=drf_request, action=binding.name, kwargs=url_kwarg_values)
    try:
        enforce_permissions(
            binding.spec,
            OfflineContext(user=context.token.user, request=drf_request, view=view),
        )
    except PermissionDenied:
        return (
            drf_request,
            view,
            None,
            None,
            JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission"),
        )
    try:
        # Only for its refusal: the values are the ones split above.
        split_url_kwargs(arguments_raw, binding.url_kwargs)
    except drf_serializers.ValidationError as exc:
        # A missing ``required=True`` URL kwarg, refused in the shape a missing
        # selector parameter is.
        return (
            drf_request,
            view,
            None,
            None,
            validation_error_result(
                exc, arguments_raw, config=context.config, conventions=context.conventions
            ).to_dict(),
        )
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
    # ``project_pk``, and a ``source="*"`` field, a ``validate`` or the
    # namesake's own coercion write there too. Every such value is dropped, so
    # the selector reads the route the permission judged. Left in,
    # ``SPREAD_CALLER_WINS`` ranked it above a kwarg the call sent, which then
    # reached the selector other than as sent
    # (``test_a_url_kwarg_the_call_sent_reaches_the_selector_as_sent``), and
    # under either spreading binding it stood in for a kwarg the call left out,
    # on a route judged as naming none
    # (``test_a_url_kwarg_the_call_left_out_reaches_the_selector_only_as_a_namesake_default``).
    # A sent kwarg reaches the selector through ``view.kwargs``; a left-out
    # one, only as the default a field of its name declares.
    route_names = {url_kwarg.name for url_kwarg in binding.url_kwargs}
    params = {
        name: value
        for name, value in _selector_dispatch_params(spec_params, validated).items()
        if name not in route_names
    }
    params.update(_url_kwarg_defaults(serializer, route_names - url_kwarg_values.keys()))
    # Evaluated inside both siblings' dispatch ``try``, after the permission and
    # rate-limit answers and the ``input_serializer``: a missing argument is the
    # same ``validation_error`` result a refused one is. Checked against what
    # reaches the selector -- the params with the validated values laid over
    # them, plus the ``UrlKwarg`` values -- so a null ``UrlKwarg``, which the
    # split drops, counts as missing (``test_a_null_url_kwarg_is_a_missing_argument``).
    # A name the input fills when the caller sends nothing, a dataclass
    # default included, is never required in the first place
    # (``schema.utils.laid_back_inputs``, which this route counts because it
    # runs the serializer, unlike ``call_tool``), and the overlay that fills it
    # is what the check reads: it describes the call the selector receives.
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
        # enforced over HTTP and not here: the class-level check says nothing
        # about the *row* a RETRIEVE resolved. Only the object-level half, since
        # ``_build_request_and_validate`` already ran the class-level one
        # against this request and view; nothing for a LIST, whose target is a
        # queryset rather than a model.
        "on_target_resolved": enforce_object_permissions,
        # The server's registered seeds: resolved into the selector's pool, and
        # reserved, so a client argument of the same name is stripped from the
        # spread rather than outranking the project's value. A selector has no
        # validator in front of that spread, so without them the name would be
        # client-controlled.
        "pool_seeds": context.pool_seeds,
    }


def _url_kwarg_defaults(serializer: Any, left_out: set[str]) -> dict[str, Any]:
    """The defaults the ``input_serializer`` declares for URL kwargs the call left out.

    Left out as the split counts it, a null included. The route the permission
    judged names none of them, so the selector gets nothing under those names
    from the overlay, whatever it holds; the default declared under the name
    supplies it, which is the author's value rather than the caller's. Those
    are the names ``schema.utils.laid_back_inputs`` says the input fills when
    the caller sends nothing, read through it with the bound ``serializer``, so
    the route the selector reads agrees with what registration counted and the
    schema did not require, and a selector requiring the name runs rather than
    raising ``TypeError`` (``[namesake-default]``, ``[default-beside-alias]``
    and ``[dataclass-default]`` of
    ``test_a_url_kwarg_the_call_left_out_reaches_the_selector_only_as_a_namesake_default``).
    A dataclass input's own default counts as well as its serializer's
    (``test_a_dataclass_inputs_route_is_the_kwarg_sent_or_the_namesake_default``,
    whose alias case validates the caller's 8 onto the left-out name, which the
    selector never reads). A default reading the serializer's context reads
    this call's, since the fields are the bound ones.

    The reader decides which names are filled; each of its conditions is held
    by a test named on it. A name it does not fill stays out:
    ``test_a_serializer_field_sourcing_a_url_kwarg_left_out_does_not_fill_the_route``
    (no field of the name), ``[read-only-default]`` and
    ``[no-default-then-validate-moves]``, where ``validate`` moved 8 onto it.
    ``serializer`` is ``None`` for a tool with no ``input_serializer``, which
    fills nothing.
    """
    _overlaid, fills = laid_back_inputs(serializer)
    return {name: fills[name]() for name in left_out if name in fills}


def _selector_dispatch_params(
    arguments_raw: dict[str, Any], validated: Any, *, strip_post_fetch_keys: bool = True
) -> dict[str, Any]:
    """Build a params mapping ``dispatch_spec`` receives for a selector.

    Called twice, for the two pools ``dispatch_spec`` keeps separate: ``params``
    (the selector's kwarg spread) with the strip on, ``filter_data`` (the
    ``FilterSet``'s input) with it off. The strip is about the *callable*:
    ``page`` / ``limit`` (``RESERVED_POST_FETCH_KEYS``) belong to the MCP read
    pipeline's pagination, so a selector taking ``**kwargs`` must not receive
    them, whereas a ``FilterSet`` reads only the fields it declares, as it does
    on HTTP. Ordering is not stripped: an ``OrderingFilter`` on the
    ``filter_set`` reads it, and a selector may declare a sort parameter of its
    own.

    The validated ``input_serializer`` values overlay the raw args either way, so
    a typed selector arg reaches the callable coerced while filter-set args
    (which bypass the serializer) keep the raw form the ``FilterSet`` wants.
    Validated values arrive in two shapes, and both are laid back: a plain DRF
    ``Serializer``'s ``dict``, and the dataclass instance a bare ``@dataclass``
    or a ``DataclassSerializer`` validates into, whose every field is laid back
    under its own name. The second was once skipped, so such an input coerced
    and defaulted for nothing and ``page=3`` reached the selector as its default
    (``test_a_dataclass_inputs_validated_values_reach_the_selector``).
    """
    core: dict[str, Any] = {
        k: v
        for k, v in arguments_raw.items()
        if not strip_post_fetch_keys or k not in RESERVED_POST_FETCH_KEYS
    }
    core.update(_validated_values(validated))
    return core


def _validated_values(validated: Any) -> dict[str, Any]:
    """The values an ``input_serializer`` validated, by name, whatever their shape.

    A ``dict`` as it is; a dataclass instance as its fields, shallowly, so a
    nested dataclass reaches the selector as the instance it validated into;
    nothing for ``None``, a tool with no ``input_serializer``.
    """
    if isinstance(validated, dict):
        return validated
    if dataclasses.is_dataclass(validated):
        return {
            field.name: getattr(validated, field.name) for field in dataclasses.fields(validated)
        }
    return {}


def enforce_object_permissions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any],
    context: OfflineContext,
    *,
    instance: Any = None,
) -> None:
    """The target guard for a call whose class-level check has already run.

    A ``TargetGuard`` for ``on_target_resolved``. drf-services' own
    ``enforce_permissions`` runs every class's ``has_permission`` before its
    ``has_object_permission``, so as the guard of a call that judged the classes
    up front it ran ``has_permission`` once more per call
    (``test_the_class_level_check_is_not_run_again_on_the_resolved_row``). This
    is its object-level half, re-implemented because drf-services exports no
    such half: the same classes, instantiated per check; the same rule that
    only a ``Model`` is judged, so a LIST's queryset, a create's ``None`` and a
    non-model value pass untouched; and the same ``PermissionDenied`` carrying
    the class's ``message`` / ``code``. It leaves out ``enforce_permissions``'
    translation of a missing request, since every caller of this hands it the
    request the call built.

    Used by the selector tools here, the service-tool handlers and
    ``call_spec_tool``, each of which runs ``enforce_permissions`` first; it
    lives in this module rather than ``handlers.utils`` only for ownership of
    the change that introduced it. The two conditions are one branch arc, so
    each is held by a test: ``permission_classes=None`` (no permission
    configured) by ``test_a_spec_with_no_permission_classes_is_guarded_by_nothing``,
    which raises ``TypeError`` without it, and the ``Model`` test by
    ``test_a_list_target_is_not_judged_row_by_row``. drf-services' translation of
    a permission reading a missing request is not mirrored: every route here
    dispatches with the request it built, so the context always carries one.
    """
    if spec.permission_classes is None or not isinstance(instance, Model):
        return
    # DRF types ``view`` as ``APIView``; ``OfflineServiceView`` is the structural
    # stand-in, as drf-services' own check treats it.
    view: Any = context.view
    for permission_class in spec.permission_classes:
        permission = permission_class()
        if not permission.has_object_permission(context.request, view, instance):
            raise PermissionDenied(
                detail=getattr(permission, "message", None),
                code=getattr(permission, "code", None),
            )


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


__all__ = [
    "dispatch_selector_tool",
    "dispatch_selector_tool_async",
    "enforce_object_permissions",
]
