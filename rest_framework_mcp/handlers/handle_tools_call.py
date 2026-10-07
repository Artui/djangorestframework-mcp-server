from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    dispatch_spec,
    enforce_permissions,
    render_for_audience,
)
from rest_framework_services.exceptions.additional_input_required import AdditionalInputRequired
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.elicitation.types.resolved_input import ResolvedInput
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool
from rest_framework_mcp.handlers.input_dispatch import (
    ask_for_input,
    refusal_result,
    resolve_prior_input,
)
from rest_framework_mcp.handlers.invalidation_dispatch import announce_invalidations
from rest_framework_mcp.handlers.selector_tool_dispatch import (
    dispatch_selector_tool,
    enforce_object_permissions,
)
from rest_framework_mcp.handlers.task_dispatch import maybe_create_task
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    consume_rate_limits,
    dispatch_shape,
    effective_rate_limits,
    enforce_result_ceiling,
    judge_tool_permissions,
    read_shaping_error_result,
    refuse_missing_arguments,
    resolve_bound,
    same_route,
    service_error_result,
    services_dispatch_policies,
    split_url_kwargs,
    validate_output_format,
    validation_error_result,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


def handle_tools_call(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Invoke a registered tool by name.

    Service tools dispatch through drf-services' transport-neutral
    ``dispatch_spec``: it owns instance resolution,
    ``input_serializer`` validation, the kwarg pool (per the binding's
    ``argument_binding`` / ``unknown_arguments`` policies), the service run, and
    the output-selector re-fetch. This layer keeps only the transport shell —
    MCP permissions / rate limits, the ``enforce_permissions`` object-permission
    hook, output format, and ``structuredContent``.
    """
    if not isinstance(params, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "tools/call params must be an object")

    tool_name: Any = params.get("name")
    if not isinstance(tool_name, str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'name' is required and must be a string"
        )

    binding = context.tools.get(tool_name)
    if binding is None:
        # The tools spec's own worked example of a protocol error: an unknown
        # tool is a bad param, so ``-32602`` rather than an ``isError`` result.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

    # Read once: only a missing key (or an explicit ``null``) means "no
    # arguments". Collapsing every falsy value into ``{}`` — as ``or {}`` did —
    # let ``[]``, ``0``, ``""`` and ``false`` past the guard on the next line,
    # so a malformed ``arguments`` ran the tool with no arguments instead of
    # being named as the fault.
    arguments_raw: Any = params.get("arguments")
    if arguments_raw is None:
        arguments_raw = {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    # Before dispatch, so an unrenderable format is a ``-32602`` the caller can
    # act on rather than a 500 raised once the tool has already run. This is
    # what lets the coercions further down the dispatch paths stay bare.
    bad_format: JsonRpcError | None = validate_output_format(params)
    if bad_format is not None:
        return bad_format

    # Ordering: after argument-shape validation so a malformed call is rejected
    # outright rather than queued, and before dispatch because a task exists
    # precisely so the dispatch does not happen here.
    as_task: dict[str, Any] | JsonRpcError | None = maybe_create_task(
        binding, arguments_raw, context
    )
    if as_task is not None:
        return as_task

    # The ceiling is applied once, here, rather than at the three dispatch
    # paths' several ``build_tool_result`` sites, so it measures the finished
    # result (both copies of the payload) rather than a renderer's intermediate.
    dispatched, ran_with = _dispatch_tool_call(binding, params, arguments_raw, context)
    result: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        dispatched,
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    # After the ceiling, so a result too large to return does not announce a
    # change the client cannot then read back. With the arguments the tool ran
    # with, a retry's answers merged in: announced with the arguments as sent,
    # an answer moving the route named the route it moved from
    # (``test_the_invalidation_names_the_route_an_answer_moved_to``).
    announce_invalidations(binding, result, ran_with, context)
    return result


def _dispatch_tool_call(
    binding: Any,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> tuple[dict[str, Any] | JsonRpcError, dict[str, Any]]:
    """Route a resolved binding to its dispatch path; return the raw result and its arguments.

    Split out of ``handle_tools_call`` so the size ceiling wraps every
    return — including the ones the chain and selector helpers make — at a
    single point. The async sibling splits the same way, plus the deadline.

    The arguments returned are the ones the tool ran with, which on a service
    tool's retry carry the answers merged over the arguments as sent, for the
    invalidation the caller announces. The chain and selector paths merge no
    answers, so theirs are the arguments as sent.
    """
    # Scoped to the dispatch portion, after binding resolution, so cheap
    # validation rejections don't generate noise. A no-op without
    # ``opentelemetry-api``.
    with span(
        "mcp.tools.call",
        attributes=_span_attrs(binding.name, context),
    ) as otel_span:
        # Chain and selector tools have their own dispatch helpers; service
        # tools fall through to the mutation-shaped path below. Neither merges
        # a retry's answers, so each ran with the arguments as sent.
        if isinstance(binding, ChainToolBinding):
            return (
                dispatch_chain_tool(binding, params, arguments_raw, context, otel_span),
                arguments_raw,
            )
        if isinstance(binding, SelectorToolBinding):
            return (
                dispatch_selector_tool(binding, params, arguments_raw, context, otel_span),
                arguments_raw,
            )

        # The spec's permission classes judge the call as its dispatch view
        # will carry it: the route in ``view.kwargs``, the routed query values
        # in ``request.query_params``, the rest of the arguments in
        # ``request.data`` and the tool's name in ``view.action``
        # (``test_the_stand_in_sees_what_the_dispatch_view_sees``). A
        # permission scoping by ``view.kwargs["project_pk"]`` was judged against
        # ``{}`` and denied a caller it admits
        # (``test_a_spec_permission_sees_the_url_kwargs_the_call_delivers``),
        # and one reading ``request.data`` raised and made the call a 500. The
        # permission answers before a missing argument does
        # (``test_a_permission_denying_the_delivered_route_answers_before_the_missing_argument``),
        # and the strict split in ``_run_service_tool`` still refuses it after.
        # The arguments as sent, before a retry's answers are merged in below,
        # since a denied caller is told so before its answers are read; an
        # answer naming another route is judged again once they are.
        _, delivered_url_kwargs = split_url_kwargs(
            arguments_raw, binding.url_kwargs, refuse_missing=False
        )
        allowed, required_scopes = judge_tool_permissions(binding, arguments_raw, context)
        if not allowed:
            return _forbidden(required_scopes), arguments_raw

        # On a retry of a call that asked the user something, the answer arrives
        # as ``inputResponses`` and becomes an ordinary argument here — the
        # whole of what the service ever sees of the exchange. A declined or
        # cancelled answer merges nothing, and is answered after the rate
        # limit below, as any call the service does not run is.
        prior: ResolvedInput = resolve_prior_input(params, binding.name, arguments_raw, context)
        arguments_raw = prior.arguments

        # An answer is merged over the arguments with no limit on its keys, so
        # it can name a URL kwarg the call sent or left out, and the route the
        # service runs with is then not the one judged above. Judged again on
        # that route, before the target is looked up: the target guard reads
        # only the spec's own classes, so a per-binding adapter answered with
        # ``project_pk: 8`` ran the service on a project the caller holds no
        # grant on
        # (``test_an_answer_naming_another_route_is_refused_by_a_per_binding_permission``),
        # and a spec class judged only there told an existing target from a
        # missing one (``test_an_answer_cannot_tell_an_existing_target_from_a_missing_one``).
        # Only when the route moved, so an ordinary retry is judged once
        # (``test_an_answer_leaving_the_route_unchanged_is_not_judged_again``),
        # and moved means not ``same_route``, which ``==`` is not: an answer of
        # ``True`` or ``1.0`` for ``1`` is equal and names another row
        # (``test_an_answer_equal_to_the_route_but_naming_another_row_is_judged_again``).
        # An answer filling a kwarg the call left out is a move
        # (``test_an_answer_filling_a_route_kwarg_left_out_is_judged_on_the_filled_route``),
        # as is one clearing a kwarg it sent
        # (``test_an_answer_clearing_a_route_kwarg_is_judged_on_the_route_it_leaves``).
        # Not refusing a missing kwarg, as the split above does not, since the
        # strict split in ``_run_service_tool`` is where that is answered
        # (``test_an_answer_leaving_a_required_route_kwarg_missing_is_told_which``).
        # Judged on the arguments the answers produced, so the stand-in
        # carries them as the dispatch view will.
        _, answered_url_kwargs = split_url_kwargs(
            arguments_raw, binding.url_kwargs, refuse_missing=False
        )
        if not same_route(answered_url_kwargs, delivered_url_kwargs):
            allowed, required_scopes = judge_tool_permissions(binding, arguments_raw, context)
            if not allowed:
                return _forbidden(required_scopes), arguments_raw

        # After both checks, so a caller either one denies is never charged:
        # charged between them, a caller refused on the route its answer names
        # had spent a unit, and against a spent quota was told ``RATE_LIMITED``
        # rather than that the route is not its to name
        # (``test_a_caller_refused_on_the_route_an_answer_names_is_not_charged``).
        retry_after: int | None = consume_rate_limits(
            effective_rate_limits(binding, context), context.http_request, context.token
        )
        if retry_after is not None:
            return (
                JsonRpcError(
                    JsonRpcErrorCode.RATE_LIMITED,
                    "Rate limit exceeded",
                    data={"retryAfter": retry_after},
                ),
                arguments_raw,
            )

        # Charged, as before: a declined answer is a call the client made
        # (``test_a_caller_admitted_on_the_answered_route_is_charged_once``).
        if prior.refused_with is not None:
            return refusal_result(prior.refused_with), arguments_raw

        result = _run_service_tool(binding, params, arguments_raw, prior, context, otel_span)
        return result, arguments_raw


def _forbidden(required_scopes: list[str]) -> JsonRpcError:
    """The answer to a caller the binding's permissions deny, on either route.

    Shared by the check on the route as sent and the one on the route a retry's
    answers produce, which answer alike; the async sibling uses it too.
    """
    return JsonRpcError(
        JsonRpcErrorCode.FORBIDDEN,
        "Insufficient permission",
        data={"requiredScopes": required_scopes} if required_scopes else None,
    )


def _run_service_tool(
    binding: Any,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    prior: ResolvedInput,
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Dispatch an admitted service-tool call and render its result.

    Split out of ``_dispatch_tool_call`` once both permission checks and the
    rate limit have answered, so that function can return the arguments the
    tool ran with beside every result. ``arguments_raw`` carries a retry's
    answers; ``prior`` is what ``ask_for_input`` needs to carry them into a
    further round.
    """
    argument_binding, unknown_arguments = services_dispatch_policies(binding)
    # Inside the ``try``: the strict ``split_url_kwargs`` raises DRF's
    # ``ValidationError`` for an omitted ``required=True`` kwarg, and that
    # must reach the same ``isError`` mapping as any other dispatch-time
    # validation failure rather than escaping as a 500, while the spec's
    # ``PermissionDenied`` reaches the ``FORBIDDEN`` arm. Still after the
    # permission and rate-limit answers ``_dispatch_tool_call`` gave.
    try:
        # Built from the same shape the binding's stand-in was, so the two
        # checks judge one request. Not refusing a missing kwarg yet: the spec
        # judges the route first.
        shape = dispatch_shape(binding, arguments_raw)
        spec_params, url_kwarg_values = shape.data, shape.kwargs
        offline = shape.build(
            user=context.token.user, auth=context.token.raw, http_request=context.http_request
        )
        # The spec's class-level check, against the request and view the call
        # runs with, before the target is looked up and before a missing
        # argument is named, as ``call_spec_tool`` judges it. Kept beside the
        # stand-in's, which judges the same shape: a ``has_permission`` whose
        # answer changes between the two is still answered before the lookup,
        # not by the target guard after it, which told a denied caller
        # ``-32006`` for a row that exists and ``not_found`` for one that does
        # not
        # (``test_the_dispatch_view_judges_before_the_lookup_when_the_stand_in_admits``),
        # and the name of an argument left out
        # (``test_a_denied_caller_is_not_told_which_argument_it_left_out``).
        enforce_permissions(binding.spec, offline)
        # Only for its refusal of a missing ``required=True`` kwarg: the values
        # are the ones split above.
        split_url_kwargs(arguments_raw, binding.url_kwargs)
        refuse_missing_arguments(
            binding, (*spec_params, *url_kwarg_values), pool_seeds=context.pool_seeds
        )
        result = dispatch_spec(
            binding.spec,
            user=context.token.user,
            params=spec_params,
            request=offline.request,
            view=offline.view,
            argument_binding=argument_binding,
            unknown_arguments=unknown_arguments,
            # Object-level only: the class-level half ran above, against the
            # same request and view.
            on_target_resolved=enforce_object_permissions,
            # Not dead on the sync path despite there being no stream: a
            # task worker runs *this* function and its reporter writes to
            # the task record. ``None`` for an ordinary sync request.
            progress=context.progress,
            # The server's ``pool_seeds=``, resolved into the pool and
            # reserved against client input, as ``dispatch_spec`` defines.
            pool_seeds=context.pool_seeds,
            # ``arguments`` is always an object, so a ``many=True`` spec's list
            # travels under ``spec.many_argument``; a no-op for any other spec.
            many_as_argument=True,
        )
    except PermissionDenied:
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # Refused input -- an unexpected argument, a serializer rejection, a
        # service's own validation -- is a *tool-level* failure per the MCP
        # spec: an ``isError`` result the model can correct from, not a
        # protocol error. Before the ``ServiceError`` arm, which would
        # otherwise take ``ServiceValidationError`` as a plain failure.
        return validation_error_result(
            exc, arguments_raw, config=context.config, conventions=context.conventions
        ).to_dict()
    except AdditionalInputRequired as exc:
        # **Must precede the ``ServiceError`` arm below** — this is a
        # subclass of it, so the generic handler would otherwise swallow the
        # request for input and report it as a plain failure.
        return ask_for_input(exc, prior, context)
    except ServiceError as exc:
        # The real-failure channel, recorded on the span when the consumer
        # opted in. ``ServiceValidationError`` deliberately is not — that is
        # input-shape feedback, not a server fault.
        if context.config.record_service_exceptions:
            otel_span.record_exception(exc)
        return service_error_result(exc).to_dict()

    if result.kind == "not_found":
        return build_error_tool_result(
            f"{binding.name}: no matching instance found", error_type="not_found"
        ).to_dict()

    # Outside the ``try`` above on purpose: its ``ValidationError`` arm
    # treats every refusal as the caller's to fix, and a render-time
    # refusal is the caller's only when they supplied a value that shaped
    # the render. A read-shaping ``QueryParam`` is read here, by the
    # output serializer, so a bad value fails after dispatch succeeded;
    # ``read_shaping_error_result`` makes that the caller's ``isError`` when
    # they supplied one and re-raises it otherwise. The async handler shares
    # ``_render`` and wraps it the same way, and a task worker reaches this
    # line through ``handle_tools_call``, so a task stores the result.
    try:
        payload = _render(binding, result, offline)
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # A service tool's result is never a page, so no envelope to explain.
        return read_shaping_error_result(
            exc,
            query_params=binding.query_params,
            arguments=arguments_raw,
            paginated=False,
            config=context.config,
            conventions=context.conventions,
        ).to_dict()
    output_format: OutputFormat = OutputFormat.coerce(
        params.get("outputFormat") or binding.output_format
    )
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
        default_output_schema=context.config.include_output_schema,
        default_structured_content=context.config.include_structured_content,
    )
    return build_tool_result(
        payload,
        output_format=output_format,
        include_structured_content=emit_structured_content,
        content_kind=binding.content_kind,
        content_mime_type=binding.content_mime_type,
        binding_name=binding.name,
    ).to_dict()


def _render(binding: Any, result: Any, offline: Any) -> Any:
    """Render a service ``DispatchResult`` through the spec's output serializer.

    ``render_for_audience`` reads the output serializer off
    ``spec.output_selector_spec`` and resolves its ``output_serializer_context``
    provider with the extras it declares (``page`` for a list result,
    ``instance`` / ``result`` for a single one).
    """
    many: bool = result.kind == "list"
    extras: dict[str, Any] = (
        {"page": result.value} if many else {"instance": result.value, "result": result.value}
    )
    # ``render_for_audience`` passes a ``None`` result through; MCP's object
    # requirement is met where every tool kind meets it, in ``build_tool_result``.
    return render_for_audience(
        binding.spec,
        result.value,
        projection=binding.audience_projection,
        many=many,
        view=offline.view,
        request=offline.request,
        extras=extras,
    )


def _span_attrs(binding_name: str, context: MCPCallContext) -> dict[str, Any]:
    """Common span attributes — kept here so the six dispatch handlers stay in sync."""
    attrs: dict[str, Any] = {
        "mcp.binding.name": binding_name,
        "mcp.protocol.version": context.protocol_version,
    }
    if context.session_id:
        attrs["mcp.session.id"] = context.session_id
    return attrs


__all__ = ["handle_tools_call"]
