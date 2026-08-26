from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    build_offline_context,
    dispatch_spec,
    enforce_permissions,
    render_for_agent,
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
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool
from rest_framework_mcp.handlers.task_dispatch import maybe_create_task
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    effective_rate_limits,
    enforce_result_ceiling,
    resolve_bound,
    services_dispatch_policies,
    split_query_params,
    split_url_kwargs,
    validate_output_format,
    validation_error_data,
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
    result: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        _dispatch_tool_call(binding, params, arguments_raw, context),
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    # After the ceiling, so a result too large to return does not announce a
    # change the client cannot then read back.
    announce_invalidations(binding, result, arguments_raw, context)
    return result


def _dispatch_tool_call(
    binding: Any,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Route a resolved binding to its dispatch path and return the raw result.

    Split out of ``handle_tools_call`` so the size ceiling wraps every
    return — including the ones the chain and selector helpers make — at a
    single point. The async sibling splits the same way, plus the deadline.
    """
    # Scoped to the dispatch portion, after binding resolution, so cheap
    # validation rejections don't generate noise. A no-op without
    # ``opentelemetry-api``.
    with span(
        "mcp.tools.call",
        attributes=_span_attrs(binding.name, context),
    ) as otel_span:
        # Chain and selector tools have their own dispatch helpers; service
        # tools fall through to the mutation-shaped path below.
        if isinstance(binding, ChainToolBinding):
            return dispatch_chain_tool(binding, params, arguments_raw, context, otel_span)
        if isinstance(binding, SelectorToolBinding):
            return dispatch_selector_tool(binding, params, arguments_raw, context, otel_span)

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

        # On a retry of a call that asked the user something, the answer arrives
        # as ``inputResponses`` and becomes an ordinary argument here — the
        # whole of what the service ever sees of the exchange.
        prior: ResolvedInput = resolve_prior_input(params, binding.name, arguments_raw, context)
        if prior.refused_with is not None:
            return refusal_result(prior.refused_with)
        arguments_raw = prior.arguments

        # ``enforce_permissions`` is the object-permission hook: it runs
        # ``spec.permission_classes`` against the resolved target.
        argument_binding, unknown_arguments = services_dispatch_policies(binding)
        # The split stays inside the ``try``: ``split_url_kwargs`` raises
        # ``ServiceValidationError`` for an omitted ``required=True`` kwarg, and
        # that must reach the same ``isError`` mapping as any other
        # dispatch-time validation failure rather than escaping as a 500.
        try:
            spec_params, url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
            # ``query_params`` is always passed — an empty mapping still
            # *replaces* whatever query string the client hung off the MCP
            # endpoint URL, so ``request.query_params`` is this package's value
            # rather than the caller's.
            spec_params, query_param_values = split_query_params(spec_params, binding.query_params)
            offline = build_offline_context(
                context.token.user,
                spec_params,
                http_request=context.http_request,
                action=binding.name,
                kwargs=url_kwarg_values or None,
                query_params=query_param_values,
            )
            result = dispatch_spec(
                binding.spec,
                user=context.token.user,
                params=spec_params,
                request=offline.request,
                view=offline.view,
                argument_binding=argument_binding,
                unknown_arguments=unknown_arguments,
                on_target_resolved=enforce_permissions,
                # Not dead on the sync path despite there being no stream: a
                # task worker runs *this* function and its reporter writes to
                # the task record. ``None`` for an ordinary sync request.
                progress=context.progress,
            )
        except drf_serializers.ValidationError as exc:
            # A malformed input *shape* is a protocol fault (-32602).
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS,
                "Invalid arguments",
                data=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
            )
        except PermissionDenied:
            return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
        except ServiceValidationError as exc:
            # Business validation on well-shaped input is a *tool-level* failure
            # per the MCP spec: an ``isError`` result, not a protocol error.
            return build_error_tool_result(
                exc.message,
                error_type="validation_error",
                detail=validation_error_data(
                    exc.detail, arguments_raw, include_value=context.config.include_validation_value
                ),
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
            return build_error_tool_result(exc.message, error_type="service_error").to_dict()

        if result.kind == "not_found":
            return build_error_tool_result(
                f"{binding.name}: no matching instance found", error_type="not_found"
            ).to_dict()

        payload = _render(binding, result, offline)
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

    ``render_for_agent`` reads the output serializer off
    ``spec.output_selector_spec`` and resolves its ``output_serializer_context``
    provider with the extras it declares (``page`` for a list result,
    ``instance`` / ``result`` for a single one).
    """
    many: bool = result.kind == "list"
    extras: dict[str, Any] = (
        {"page": result.value} if many else {"instance": result.value, "result": result.value}
    )
    payload = render_for_agent(
        binding.spec,
        result.value,
        projection=binding.agent_projection,
        many=many,
        view=offline.view,
        request=offline.request,
        extras=extras,
    )
    # MCP contract: a tool returning ``None`` must still emit a JSON object as
    # ``structuredContent``, and ``render_spec_output`` passes ``None`` through.
    return {} if payload is None else payload


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
