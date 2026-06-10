"""Dispatch for chain tools — run an ordered sequence of specs as one tool.

Flow (sync; the async sibling bridges the whole thing through ``acall``):

.. code-block:: text

    auth + rate limit
      → validate(arguments, resolved_input_serializer)   → ctx.args
      → for each step:  inputs(ctx) → pool → run service/selector
                        → store result under step.alias
        (the loop runs inside transaction.atomic() when binding.atomic)
      → render the output step (or every step, when output_all)
      → ToolResult

A step raising ``ServiceValidationError`` / ``ServiceError`` is mapped to a
JSON-RPC error carrying ``failedStep``; under an atomic chain the error also
rolls back every prior write (the mapped error is re-raised as a private
abort signal so the surrounding ``transaction.atomic()`` unwinds, then
returned).

Chains deliberately do **not** run the selector post-fetch pipeline
(filter / order / paginate) — that's a single selector-tool concern. A
selector step's result is used as-is (and rendered ``many=True`` for
``kind=LIST``).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from django.db import transaction
from rest_framework import serializers as drf_serializers
from rest_framework_services import resolve_callable_kwargs, run_selector, run_service
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    build_internal_drf_request,
    check_permissions,
    consume_rate_limits,
    invoke_context_provider,
    validate_input_against_serializer,
    validation_error_data,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_context import ChainContext
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView


class _ChainAbort(Exception):
    """Carry a step's mapped tool-level error out of ``transaction.atomic()``.

    Raising forces the surrounding atomic block to roll back; the caller
    catches it and returns the wrapped ``isError`` tool-result dict.
    """

    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__()
        self.error = error


def dispatch_chain_tool(
    binding: ChainToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Sync dispatch through the chain-tool pipeline."""
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

    drf_request = build_internal_drf_request(
        context.http_request, user=context.token.user, data=arguments_raw
    )
    serializer: type | None = binding.resolved_input_serializer
    try:
        validated: Any = validate_input_against_serializer(
            arguments_raw, serializer, unknown_arguments=binding.unknown_arguments
        )
    except drf_serializers.ValidationError as exc:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "Invalid arguments",
            data=validation_error_data(exc.detail, arguments_raw),
        )

    ctx = ChainContext(
        args=validated if serializer is not None else arguments_raw,
        request=drf_request,
        user=context.token.user,
    )

    error = _run_chain(binding, ctx, otel_span)
    if error is not None:
        return error

    payload: Any = _render_chain_output(binding, ctx, drf_request)
    output_format: OutputFormat = OutputFormat.coerce(
        params.get("outputFormat") or binding.output_format
    )
    _emit_output_schema, emit_structured_content = resolve_structured_output(
        include_output_schema_override=binding.include_output_schema,
        include_structured_content_override=binding.include_structured_content,
        binding_name=binding.name,
    )
    return build_tool_result(
        payload,
        output_format=output_format,
        include_structured_content=emit_structured_content,
    ).to_dict()


async def dispatch_chain_tool_async(
    binding: ChainToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling — runs the whole sync chain in a worker thread.

    The chain touches the DB (writes, ``transaction.atomic()``), which
    Django forbids inline in an async context. Bridging the entire sync
    dispatcher through :func:`acall` keeps the pipeline (and any async
    service/selector, which ``run_service`` / ``run_selector`` bridge
    internally) running in a sync-safe thread.
    """
    result: dict[str, Any] | JsonRpcError = await acall(
        dispatch_chain_tool, binding, params, arguments_raw, context, otel_span
    )
    return result


def _run_chain(
    binding: ChainToolBinding, ctx: ChainContext, otel_span: Any
) -> dict[str, Any] | None:
    """Run every step in order, optionally inside one transaction."""
    if binding.atomic:
        try:
            with transaction.atomic():
                error = _run_steps(binding, ctx, otel_span)
                if error is not None:
                    raise _ChainAbort(error)
        except _ChainAbort as abort:
            return abort.error
        return None
    return _run_steps(binding, ctx, otel_span)


def _run_steps(
    binding: ChainToolBinding, ctx: ChainContext, otel_span: Any
) -> dict[str, Any] | None:
    for step in binding.steps:
        error = _run_step(step, ctx, otel_span)
        if error is not None:
            return error
    return None


def _run_step(step: ChainStep, ctx: ChainContext, otel_span: Any) -> dict[str, Any] | None:
    """Run one step and store its result under ``step.alias``.

    The pool is ``{request, user}`` plus the step's ``inputs(ctx)`` mapping
    (or ``{"data": ctx.args}`` when ``inputs`` is ``None``);
    ``resolve_callable_kwargs`` then filters it to the callable's signature.
    """
    provided: Mapping[str, Any] = (
        step.inputs(ctx) if step.inputs is not None else {"data": ctx.args}
    )
    pool: dict[str, Any] = {"request": ctx.request, "user": ctx.user, **provided}
    try:
        if isinstance(step.spec, ServiceSpec):
            result: Any = _run_service_step(step.spec, pool)
        else:
            result = _run_selector_step(step.spec, pool)
    except ServiceValidationError as exc:
        # Tool-level failure -> ``isError`` result carrying ``failedStep``;
        # the surrounding ``transaction.atomic()`` (when ``binding.atomic``)
        # still rolls back via ``_ChainAbort``. JSON-RPC errors stay
        # reserved for protocol faults.
        return build_error_tool_result(
            exc.message,
            error_type="validation_error",
            detail={"failedStep": step.alias, **validation_error_data(exc.detail, {})},
        ).to_dict()
    except ServiceError as exc:
        if get_setting("RECORD_SERVICE_EXCEPTIONS"):
            otel_span.record_exception(exc)
        return build_error_tool_result(
            exc.message,
            error_type="service_error",
            detail={"failedStep": step.alias},
        ).to_dict()
    ctx.outputs[step.alias] = result
    return None


def _run_service_step(spec: ServiceSpec[Any, Any, Any], pool: dict[str, Any]) -> Any:
    # atomic=False: the chain owns the transaction (binding.atomic). The
    # service's own spec.atomic is subordinate under a chain.
    result: Any = run_service(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=False
    )
    out_spec = spec.output_selector_spec
    if out_spec is not None and out_spec.selector is not None:
        sel_pool: dict[str, Any] = {**pool, "instance": result, "result": result}
        result = run_selector(
            out_spec.selector, resolve_callable_kwargs(out_spec.selector, sel_pool)
        )
    return result


def _run_selector_step(spec: SelectorSpec[Any, Any], pool: dict[str, Any]) -> Any:
    selector = spec.selector
    assert selector is not None  # guaranteed by ChainToolBinding validation  # noqa: S101
    return run_selector(selector, resolve_callable_kwargs(selector, pool))


def _render_chain_output(binding: ChainToolBinding, ctx: ChainContext, drf_request: Any) -> Any:
    if binding.output_all:
        rendered: dict[str, Any] = {}
        for step in binding.steps:
            if _step_output_serializer(step) is not None:
                rendered[step.alias] = _render_step(step, ctx, drf_request)
        return rendered
    return _render_step(binding.output_step, ctx, drf_request)


def _render_step(step: ChainStep, ctx: ChainContext, drf_request: Any) -> Any:
    result: Any = ctx.outputs[step.alias]
    spec = step.spec
    provider: Any
    if isinstance(spec, SelectorSpec):
        serializer: type | None = spec.output_serializer
        many: bool = spec.kind is SelectorKind.LIST
        extra_name: str = "page" if many else "instance"
        provider = spec.output_serializer_context
    else:
        out_spec = spec.output_selector_spec
        serializer = out_spec.output_serializer if out_spec else None
        many = False
        extra_name = "result"
        provider = out_spec.output_serializer_context if out_spec else None
    if serializer is None:
        return {} if result is None else result
    if provider is None:
        return serializer(result, many=many).data
    view = MCPServiceView(request=drf_request, action=step.alias)
    sctx: Mapping[str, Any] = invoke_context_provider(
        provider, view, drf_request, extras={extra_name: result}
    )
    return serializer(result, many=many, context=dict(sctx)).data


def _step_output_serializer(step: ChainStep) -> type | None:
    spec = step.spec
    if isinstance(spec, ServiceSpec):
        return spec.output_selector_spec.output_serializer if spec.output_selector_spec else None
    return spec.output_serializer


__all__ = ["dispatch_chain_tool", "dispatch_chain_tool_async"]
