"""Dispatch for chain tools — run an ordered sequence of specs as one tool.

Flow (sync; the async sibling bridges the whole thing through ``acall``):

    auth (every step's wrapped classes, against stand-ins) + rate limit
      → validate(arguments, resolved_input_serializer)   → ctx.args
      → for each step:  the step spec's class-level permission_classes
                        → inputs(ctx) → pool → lookup → its object-level
                        permission_classes + affordances + preconditions
                        → run service/selector
                        → store result under step.alias
        (the loop runs inside transaction.atomic() when binding.atomic)
      → render the output step (or every step, when output_all)

A step's spec is dispatched directly rather than through ``dispatch_spec``:
a chain owns the transaction, the argument binding and the pool, and hands each
callable the mapping ``inputs`` built. The three gates ``dispatch_spec`` would
otherwise contribute are therefore run here explicitly — the spec's
``permission_classes``, its class-level half through ``enforce_permissions``
before the step's ``inputs`` and lookup and its object-level half through
``enforce_object_permissions`` on the target they resolve, a service's
``affordances`` through ``enforce_affordances``, and the spec's
``preconditions`` — so a rule written once on a spec holds on this path as well.

A step raising ``ServiceValidationError`` or DRF's ``ValidationError`` (a
``validation_error``), or a ``ServiceError``, is mapped to an error carrying
``failedStep`` (and, for a refusal, the affordance's ``code``
beside it); under an atomic chain the mapped error is
re-raised as a private abort signal so the surrounding ``transaction.atomic()``
unwinds, then returned.

Chains deliberately do **not** run the selector post-fetch pipeline (filter /
order / paginate) — that is a selector-tool concern. A ``LIST`` selector step's
result is used as-is and rendered ``many=True``, as is a service step whose
``output_selector_spec`` re-fetches a ``LIST``, and a ``many=True`` service step,
whose re-fetch never runs, as ``dispatch_spec`` never runs it. A ``RETRIEVE`` is
collapsed to its one row first, the way ``dispatch_spec`` collapses it, and a row
that is not there fails the step as ``not_found`` unless the spec sets
``allow_none``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db import transaction
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework_services import (
    OfflineServiceView,
    PoolSeeds,
    base_pool,
    base_serializer_context,
    enforce_affordances,
    enforce_permissions,
    materialize_retrieve,
    render_for_audience,
    resolve_callable_kwargs,
    run_selector,
    run_service,
)
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.offline_context import OfflineContext
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp._compat.acall import acall
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import JsonRpcErrorCode, OutputFormat
from rest_framework_mcp.handlers.selector_tool_dispatch import enforce_object_permissions
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    chain_shape,
    consume_rate_limits,
    effective_rate_limits,
    judge_tool_permissions,
    service_error_result,
    validate_input_against_serializer,
    validation_error_data,
    validation_error_result,
)
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.output.tool_result import build_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_context import ChainContext
from rest_framework_mcp.registry.types.chain_step import ChainStep
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.utils import rendered_kind


class _MissingInstance(Exception):
    """A ``RETRIEVE`` step resolved no row and its spec does not allow ``None``.

    Raised from the step runner and mapped to a ``not_found`` result in
    ``_run_step``, which is where the step's alias is in hand.
    """


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
    # Every step's wrapped classes, and the chain's own permissions, up front,
    # against stand-ins of the request the chain builds below and each step's
    # view of it. A failing step permission so blocks the chain before any step
    # runs only where the stand-in answers as the step's view does; each step
    # still judges its classes against its real view before its lookup.
    allowed, required_scopes = judge_tool_permissions(binding, arguments_raw, context)
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

    # From the shape the up-front check's stand-ins were built from. Chain tools
    # have no query-param (or URL-kwarg) registration surface, so its empty
    # query string exists to *replace* the wrapped request's ``GET``, keeping a
    # query string appended to the MCP endpoint URL out of a serializer that
    # reads ``request.query_params``.
    chain_context: OfflineContext = chain_shape(arguments_raw, binding.name).build(
        user=context.token.user, auth=context.token.raw, http_request=context.http_request
    )
    drf_request: Any = chain_context.request
    serializer: type | None = binding.resolved_input_serializer
    try:
        validated: Any = validate_input_against_serializer(
            arguments_raw,
            serializer,
            unknown_arguments=binding.unknown_arguments,
            # DRF's baseline context, as the serializer would have over HTTP.
            context=base_serializer_context(view=chain_context.view, request=drf_request),
        )
    except drf_serializers.ValidationError as exc:
        # The chain's own arguments refused: input validation, so the
        # ``validation_error`` result every tool kind answers with, carrying no
        # ``failedStep`` because no step ran. Not ``-32602``: the MCP spec keeps
        # that for an unknown tool and a malformed request.
        return validation_error_result(
            exc, arguments_raw, config=context.config, conventions=context.conventions
        ).to_dict()

    ctx = ChainContext(
        args=validated if serializer is not None else arguments_raw,
        request=drf_request,
        user=context.token.user,
    )

    try:
        error = _run_chain(binding, ctx, otel_span, context.config, context.pool_seeds)
    except PermissionDenied:
        # A step's object-level permission said no. The same envelope a service
        # tool answers with, and mapped here rather than upstream because
        # ``handle_tools_call`` wraps only its own dispatch arm — an escape from
        # this one would surface as an unhandled 500. Under ``binding.atomic``
        # the exception already unwound the transaction on its way out.
        return JsonRpcError(JsonRpcErrorCode.FORBIDDEN, "Insufficient permission")
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


async def dispatch_chain_tool_async(
    binding: ChainToolBinding,
    params: dict[str, Any],
    arguments_raw: dict[str, Any],
    context: MCPCallContext,
    otel_span: Any,
) -> dict[str, Any] | JsonRpcError:
    """Async sibling — runs the whole sync chain in a worker thread.

    The chain writes and opens ``transaction.atomic()``, which Django forbids
    inline in an async context, so the whole sync dispatcher runs in a sync-safe
    thread — an async service or selector is bridged inside ``run_service`` /
    ``run_selector``.
    """
    result: dict[str, Any] | JsonRpcError = await acall(
        dispatch_chain_tool, binding, params, arguments_raw, context, otel_span
    )
    return result


def _run_chain(
    binding: ChainToolBinding,
    ctx: ChainContext,
    otel_span: Any,
    config: MCPConfig,
    seeds: PoolSeeds,
) -> dict[str, Any] | None:
    """Run every step in order, optionally inside one transaction."""
    if binding.atomic:
        try:
            with transaction.atomic():
                error = _run_steps(binding, ctx, otel_span, config, seeds)
                if error is not None:
                    raise _ChainAbort(error)
        except _ChainAbort as abort:
            return abort.error
        return None
    return _run_steps(binding, ctx, otel_span, config, seeds)


def _run_steps(
    binding: ChainToolBinding,
    ctx: ChainContext,
    otel_span: Any,
    config: MCPConfig,
    seeds: PoolSeeds,
) -> dict[str, Any] | None:
    for step in binding.steps:
        error = _run_step(step, ctx, otel_span, config, seeds)
        if error is not None:
            return error
    return None


def _run_step(
    step: ChainStep,
    ctx: ChainContext,
    otel_span: Any,
    config: MCPConfig,
    seeds: PoolSeeds,
) -> dict[str, Any] | None:
    """Run one step and store its result under ``step.alias``.

    The pool is the step's ``inputs(ctx)`` mapping (or ``{"data": ctx.args}``
    when ``inputs`` is ``None``) with drf-services' ``base_pool`` seeded **over**
    it: ``request`` / ``user``, ``progress`` (the no-op reporter, as a chain
    step reports nowhere) and every name the server's ``pool_seeds`` registers,
    resolved per step as ``dispatch_spec`` resolves them per call.
    ``resolve_callable_kwargs`` then filters it to the callable's signature.

    Seeded last, deliberately. Forwarding the tool's own ``ctx.args`` is the
    natural way to write an ``inputs`` callable, and with the seeds merged
    first a client argument called ``user`` outranked the caller's identity and
    scoped the step to whoever the caller named; a client ``tenant`` would
    outrank a registered seed the same way
    (test_a_chain_step_reads_a_seed_over_what_its_inputs_provide). The other
    reserved names are left to ``inputs``, which owns ``data`` / ``instance`` by
    contract — that is how one step feeds the next.

    The step's own ``permission_classes`` and ``preconditions`` fire here, in
    drf-services' order: the class-level permissions before anything is looked
    up, the object-level ones on the resolved target, then preconditions, then
    the callable. A chain dispatches each step's spec directly rather than
    through ``dispatch_spec``, which is where those run on every other path.
    """
    offline = OfflineContext(
        user=ctx.user,
        request=ctx.request,
        # The step's alias is its action name, matching what ``_render_step``
        # hands the renderer and what the up-front stand-in carried, so a
        # permission reading ``view.action`` sees the step it is judging.
        view=OfflineServiceView(request=ctx.request, action=step.alias),
    )
    # The step's class-level permissions, against its own view, before its
    # ``inputs`` and its lookup run, as ``dispatch_spec`` judges a spec before
    # resolving its target. Judged after the lookup, a caller the step denies
    # was answered ``-32006`` for a row that exists and ``not_found`` for one
    # that does not, and the lookup ran
    # (``test_a_chain_step_judges_its_classes_before_its_lookup``). Only the
    # object-level half is left for the row the step resolves. A denial
    # escapes as ``PermissionDenied``, which ``dispatch_chain_tool`` answers.
    enforce_permissions(step.spec, offline)
    provided: Mapping[str, Any] = (
        step.inputs(ctx) if step.inputs is not None else {"data": ctx.args}
    )
    # Built through ``base_pool`` rather than restated, as drf-services asks of
    # every adapter assembling its own pool, so a step sees each seed a
    # ``dispatch_spec`` call would hand the same callable.
    seeded: dict[str, Any] = base_pool(user=ctx.user, request=ctx.request, seeds=seeds)
    pool: dict[str, Any] = {**provided, **seeded}
    try:
        if isinstance(step.spec, ServiceSpec):
            result: Any = _run_service_step(step.spec, pool, offline, seeds.reserved)
        else:
            result = _run_selector_step(step.spec, pool, offline)
    except (drf_serializers.ValidationError, ServiceValidationError) as exc:
        # Tool-level failure, so an ``isError`` result carrying ``failedStep``;
        # an atomic chain still rolls back via ``_ChainAbort``. DRF's exception
        # shares the arm, as it does on every service-tool path: it is what a
        # service's ``serializer.is_valid(raise_exception=True)`` raises, and it
        # escaped this one as a 500
        # (``test_a_steps_drf_validation_error_is_a_validation_error_result``).
        # It has no message of its own, so it keeps the one
        # ``validation_error_result`` gives it, while a kernel refusal keeps the
        # service's (``test_a_steps_service_validation_error_keeps_its_own_message``).
        # Before the ``ServiceError`` arm, which would otherwise take
        # ``ServiceValidationError`` as a plain failure.
        return build_error_tool_result(
            exc.message if isinstance(exc, ServiceValidationError) else "Invalid arguments",
            error_type="validation_error",
            detail={
                "failedStep": step.alias,
                **validation_error_data(
                    exc.detail, {}, include_value=config.include_validation_value
                ),
            },
        ).to_dict()
    except _MissingInstance:
        # The selector tool's wording and error type, with the step as the name:
        # the same missing row answers the same way whichever tool fetched it.
        return build_error_tool_result(
            f"{step.alias}: no matching instance found",
            error_type="not_found",
            detail={"failedStep": step.alias},
        ).to_dict()
    except ServiceError as exc:
        if config.record_service_exceptions:
            otel_span.record_exception(exc)
        # A refusal's ``code`` rides beside ``failedStep``: the step says where
        # the chain stopped, the code says which rule stopped it.
        return service_error_result(exc, detail={"failedStep": step.alias}).to_dict()
    ctx.outputs[step.alias] = result
    return None


def _run_service_step(
    spec: ServiceSpec[Any, Any, Any],
    pool: dict[str, Any],
    offline: OfflineContext,
    reserved: frozenset[str],
) -> Any:
    # Target first, as ``dispatch_spec`` does before a mutation: whatever
    # ``inputs`` put in the pool as ``instance`` is the row an object-level
    # permission judges. Only that half: ``_run_step`` ran the class-level one
    # before ``inputs`` resolved the row, and running it again here asked
    # ``has_permission`` once more per step
    # (``test_a_chain_step_asks_each_check_once``).
    enforce_object_permissions(spec, offline, instance=pool.get("instance"))
    # Then the service's own affordances, before its preconditions: the order
    # ``dispatch_spec`` runs them in, through the same drf-services function, so a
    # call refused as a tool of its own is refused as a chain step as well. They
    # were skipped here once -- the step ran, and the chain reported success.
    # ``reserved`` is the server's seed set, as ``dispatch_spec`` hands it: a
    # callable condition sees only the reserved names of the pool, so without
    # it a condition reading a registered seed would be asked without one.
    enforce_affordances(spec, pool, instance=pool.get("instance"), reserved=reserved)
    _run_preconditions(spec, pool)
    # atomic=False: the chain owns the transaction (binding.atomic). The
    # service's own spec.atomic is subordinate under a chain.
    result: Any = run_service(
        spec.service, resolve_callable_kwargs(spec.service, pool), atomic=False
    )
    out_spec = spec.output_selector_spec
    # A ``many=True`` step's result is the list its service returned, as
    # ``dispatch_spec`` leaves it: drf-services never runs the re-fetch for a list
    # payload, whose selector would be handed the whole list as ``instance``, and
    # ``rendered_kind`` renders that list ``many`` for a step and a tool alike.
    if out_spec is None or spec.many:
        return result
    if out_spec.selector is None:
        # No re-fetch, so the service's own return is presented, and under
        # ``LIST`` it must be a set of rows: refused as ``dispatch_spec`` refuses
        # it, where it once failed while rendering, a DRF ``AttributeError``
        # reading a serializer field off a mapping's keys.
        if out_spec.kind is SelectorKind.LIST:
            return _service_return_as_list(spec, result)
        return result
    sel_pool: dict[str, Any] = {**pool, "instance": result, "result": result}
    result = run_selector(out_spec.selector, resolve_callable_kwargs(out_spec.selector, sel_pool))
    # A ``RETRIEVE`` re-fetch renders one row, so a queryset collapses here as
    # ``dispatch_spec`` collapses it; handed to the renderer whole, every
    # serializer field was looked up on the queryset and the call failed.
    if out_spec.kind is SelectorKind.RETRIEVE:
        result = materialize_retrieve(result)
    return result


def _service_return_as_list(spec: ServiceSpec[Any, Any, Any], result: Any) -> Any:
    """``result``, once it is a set of rows a ``LIST`` declaration can present.

    A mirror of drf-services' ``dispatch.utils.service_return_as_list``, message
    included, so a chain step and a service tool refuse the same declaration
    alike. That function is not exported, and the public path to it is
    ``dispatch_spec`` itself, which runs the service a step has already run
    through ``run_service``.

    A mapping, a ``str`` or ``bytes`` (iterable, but by key or character rather
    than by row), anything else that does not iterate, and ``None`` are refused.
    ``ImproperlyConfigured``, not a tool error: the service has run and its
    write stands. The test is one arc to coverage, so each member is a row of
    ``test_a_list_output_spec_with_no_selector_refuses_a_return_that_is_no_set``
    (``mapping``, ``str``, ``bytes``, ``non-iterable``, ``none``), which fails
    without it.
    """
    if isinstance(result, Mapping | str | bytes) or not isinstance(result, Iterable):
        label = getattr(spec.service, "__qualname__", repr(spec.service))
        raise ImproperlyConfigured(
            "output_selector_spec declares kind=LIST with no selector, so the service's "
            f"own return is the list presented, and {label} returned "
            f"{type(result).__name__}, which is neither a QuerySet nor an iterable of "
            "rows. Return the rows, or declare kind=SelectorKind.RETRIEVE to present one "
            "value. The service has already run, so its write stands."
        )
    return result


def _run_selector_step(
    spec: SelectorSpec[Any, Any], pool: dict[str, Any], offline: OfflineContext
) -> Any:
    selector = spec.selector
    assert selector is not None  # guaranteed by ChainToolBinding validation  # noqa: S101
    if spec.kind is SelectorKind.LIST:
        result: Any = run_selector(selector, resolve_callable_kwargs(selector, pool))
        # A set is authorized per-set, never per-row, and ``_run_step`` judged
        # the classes before the selector ran, so nothing is left to judge.
        pool["collection"] = result
        _run_preconditions(spec, pool)
        return result
    # ``RETRIEVE`` resolves to its row before anything judges it, through the
    # function ``dispatch_spec`` resolves it with -- the sync one, since the async
    # transport runs a chain in a worker thread. The guard checks
    # ``has_object_permission`` only for a model instance, so a selector written
    # ``Model.objects.filter(pk=pk)`` -- a form drf-services supports -- used to
    # reach it as a queryset and skip the object-level rule entirely; the step
    # then handed that queryset on as ``instance``.
    try:
        instance: Any = materialize_retrieve(
            run_selector(selector, resolve_callable_kwargs(selector, pool))
        )
    except ObjectDoesNotExist:
        # ``Model.objects.get(...)`` reports a missing row by raising; the
        # queryset form reports it as ``None``. Both mean the same thing.
        instance = None
    if instance is None:
        if spec.allow_none:
            # The nullable contract: no row to guard or to test preconditions
            # against, so neither runs, as in ``dispatch_spec``.
            return None
        raise _MissingInstance
    # The object-level half only; ``_run_step`` judged the classes before the
    # lookup (``test_a_chain_step_asks_each_check_once``).
    enforce_object_permissions(spec, offline, instance=instance)
    pool["instance"] = instance
    _run_preconditions(spec, pool)
    return instance


def _run_preconditions(
    spec: ServiceSpec[Any, Any, Any] | SelectorSpec[Any, Any], pool: dict[str, Any]
) -> None:
    """Run a spec's ``preconditions`` through the step pool, in order.

    drf-services fires these from inside ``dispatch_spec`` only, and its helper
    is not part of that package's public surface, so the loop is spelled out
    here against the same ``resolve_callable_kwargs`` this module already
    dispatches every step through. Raise-to-abort, exactly as upstream: a
    predicate's return value is ignored, so one written ``-> bool`` returning
    ``False`` is a no-op.
    """
    for precondition in spec.preconditions or ():
        precondition(**resolve_callable_kwargs(precondition, pool))


def _render_chain_output(binding: ChainToolBinding, ctx: ChainContext, drf_request: Any) -> Any:
    if binding.output_all:
        rendered: dict[str, Any] = {}
        for step in binding.steps:
            if _step_output_serializer(step) is not None:
                rendered[step.alias] = _render_step(step, ctx, drf_request)
        return rendered
    return _render_step(binding.output_step, ctx, drf_request)


def _render_step(step: ChainStep, ctx: ChainContext, drf_request: Any) -> Any:
    """Render one step's stored output through its own spec.

    Only the ``many`` flag and the extra's *name* are decided here;
    ``render_for_audience`` owns serializer lookup, context layering, and the
    audience projection.

    The serializer-less short-circuit stays local because it is this transport's
    own contract: a step with nothing to render contributes ``{}`` rather than a
    ``null``, and its raw value passes through uncoerced —
    ``render_for_audience`` would list-coerce a ``many`` result, which a chain
    step never wants since a later step may consume it.
    """
    result: Any = ctx.outputs[step.alias]
    spec = step.spec
    # ``many`` from the same answer the binding advertises ``outputSchema`` by, so
    # the payload and its schema cannot disagree about cardinality. A service step
    # was once always rendered as one object, so a ``LIST`` output re-fetch handed
    # its whole set to the serializer as a single row and failed on every call.
    many: bool = rendered_kind(spec) is SelectorKind.LIST
    # The extra's name follows ``dispatch_spec``'s: a list result is the ``page``
    # whichever spec produced it, and a single one is the selector's ``instance``
    # or the service's ``result``.
    extra_name: str = "result"
    if many:
        extra_name = "page"
    elif isinstance(spec, SelectorSpec):
        extra_name = "instance"
    if _step_output_serializer(step) is None:
        return {} if result is None else result
    # Derived per step rather than taken from the binding: a chain renders
    # each step through its own spec, and the binding's projection describes
    # only the output step's serializer.
    return render_for_audience(
        spec,
        result,
        many=many,
        view=OfflineServiceView(request=drf_request, action=step.alias),
        request=drf_request,
        extras={extra_name: result},
    )


def _step_output_serializer(step: ChainStep) -> type | None:
    spec = step.spec
    if isinstance(spec, ServiceSpec):
        return spec.output_selector_spec.output_serializer if spec.output_selector_spec else None
    return spec.output_serializer


__all__ = ["dispatch_chain_tool", "dispatch_chain_tool_async"]
