from __future__ import annotations

import asyncio
from typing import Any

from asgiref.sync import sync_to_async
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import adispatch_spec, build_offline_context, enforce_permissions
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.chain_tool_dispatch import dispatch_chain_tool_async
from rest_framework_mcp.handlers.handle_tools_call import _render, _span_attrs
from rest_framework_mcp.handlers.invalidation_dispatch import announce_invalidations_async
from rest_framework_mcp.handlers.selector_tool_dispatch import dispatch_selector_tool_async
from rest_framework_mcp.handlers.task_dispatch import maybe_create_task
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    check_permissions,
    consume_rate_limits,
    effective_rate_limits,
    enforce_result_ceiling,
    resolve_bound,
    run_with_deadline,
    services_dispatch_policies,
    split_query_params,
    split_url_kwargs,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding


async def handle_tools_call_async(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling of :func:`handle_tools_call`.

    Service tools dispatch through :func:`~rest_framework_services.adispatch_spec`,
    which awaits async-native callables and bridges sync ones (validation,
    instance resolution, the ``enforce_permissions`` guard) off the event loop.
    The transport shell — MCP permissions / rate limits, output format,
    ``structuredContent`` — stays here; output rendering runs through
    :func:`acall` so a lazy list result is materialised off-loop.
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
        # See the sync sibling: the tools spec answers an unknown tool with
        # ``-32602``.
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown tool: {tool_name!r}")

    arguments_raw: Any = params.get("arguments") or {}
    if not isinstance(arguments_raw, dict):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'arguments' must be an object")

    # See the sync sibling: the task branch sits after argument-shape
    # validation and before dispatch, and is deliberately *outside* the
    # deadline — creating a task is a store write and a queue hand-off, not the
    # work, and timing it out would abandon a task that had already been
    # durably created.
    # ⚠ Through the thread-sensitive executor, not called directly. It reads
    # the cache and runs the binding's permissions, and both reach code Django
    # marks async-unsafe — a database-backed cache raises outright, and
    # ``DjangoPermRequired`` runs an ORM query. The same hop
    # ``completion/complete`` takes, for the same reason.
    as_task: dict[str, Any] | JsonRpcError | None = await sync_to_async(
        maybe_create_task, thread_sensitive=True
    )(binding, arguments_raw, context)
    if as_task is not None:
        return as_task

    try:
        result: dict[str, Any] | JsonRpcError = await run_with_deadline(
            _dispatch_tool_call_async(binding, params, arguments_raw, context),
            resolve_bound(binding.dispatch_timeout, context.config.dispatch_timeout),
        )
    except asyncio.TimeoutError:  # noqa: UP041 — 3.10 keeps this distinct from builtins
        # A timeout is a tool *execution* failure, not a malformed request, so
        # it comes back as an ``isError`` result: the model can respond to it
        # (narrow the query, try a smaller page) where it can only surface a
        # JSON-RPC error. ⚠ The dispatch itself keeps running — see
        # ``run_with_deadline`` — so this ends the client's wait, not the work.
        return build_error_tool_result(
            f"Tool {binding.name!r} exceeded this server's dispatch deadline and was "
            "abandoned. It may still be running. Narrow the request — tighten a "
            "filter, lower 'limit' — and try again.",
            error_type="timeout",
        ).to_dict()
    bounded: dict[str, Any] | JsonRpcError = enforce_result_ceiling(
        result,
        max_result_bytes=resolve_bound(binding.max_result_bytes, context.config.max_result_bytes),
        label=f"Tool {binding.name!r}",
    )
    # See the sync sibling: after the ceiling, so an oversized result does not
    # announce a change the client cannot read back.
    await announce_invalidations_async(binding, bounded, arguments_raw, context)
    return bounded


async def _dispatch_tool_call_async(
    binding: Any,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Route a resolved binding to its async dispatch path.

    Split out of :func:`handle_tools_call_async` so one deadline covers the
    whole dispatch — permissions, rate limits, the spec run and rendering — and
    one size check sees the finished result, whichever of the three paths
    produced it.
    """
    with span("mcp.tools.call", attributes=_span_attrs(binding.name, context)) as otel_span:
        # Chain tools run an ordered sequence of specs; read-shaped tools route
        # through the selector-tool dispatch helper (filter / order / paginate).
        # Mutation tools fall through to the service-tool path below.
        if isinstance(binding, ChainToolBinding):
            return await dispatch_chain_tool_async(
                binding, params, arguments_raw, context, otel_span
            )
        if isinstance(binding, SelectorToolBinding):
            return await dispatch_selector_tool_async(
                binding, params, arguments_raw, context, otel_span
            )

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
            consume_rate_limits,
            effective_rate_limits(binding, context),
            context.http_request,
            context.token,
        )
        if retry_after is not None:
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        # See the sync sibling: URL kwargs route through the view, not the params,
        # and the split runs inside the ``try`` so a missing ``required=True``
        # kwarg reaches the ``isError`` mapping instead of escaping.
        argument_binding, unknown_arguments = services_dispatch_policies(binding)
        try:
            spec_params, url_kwarg_values = split_url_kwargs(arguments_raw, binding.url_kwargs)
            # Read-shaping params leave the spec params and land in the
            # synthetic request's ``GET`` instead. Always passed — an empty
            # mapping still *replaces* whatever query string the client hung off
            # the MCP endpoint URL, so ``request.query_params`` is this
            # package's value rather than the caller's.
            spec_params, query_param_values = split_query_params(spec_params, binding.query_params)
            offline = build_offline_context(
                context.token.user,
                spec_params,
                http_request=context.http_request,
                action=binding.name,
                kwargs=url_kwarg_values or None,
                query_params=query_param_values,
            )
            result = await adispatch_spec(
                binding.spec,
                user=context.token.user,
                params=spec_params,
                request=offline.request,
                view=offline.view,
                argument_binding=argument_binding,
                unknown_arguments=unknown_arguments,
                on_target_resolved=enforce_permissions,
                # ``None`` unless the client asked for progress; drf-services
                # substitutes its no-op, so the service body is unchanged.
                progress=context.progress,
            )
        except drf_serializers.ValidationError as exc:
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

        if result.kind == "not_found":
            return build_error_tool_result(
                f"{binding.name}: no matching instance found", error_type="not_found"
            ).to_dict()

        # Rendering may evaluate a lazy list queryset → run it off the event loop.
        payload: Any = await acall(_render, binding, result, offline)
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


__all__ = ["handle_tools_call_async"]
