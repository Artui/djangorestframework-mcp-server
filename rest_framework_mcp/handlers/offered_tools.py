"""Which tools ``tools/list`` offers: the ones no operation-scope affordance refuses now.

A service's ``affordances`` come in two kinds. A condition on the row (an ORM
expression) varies per object and is answered where it always is, at the call.
A callable condition varies with nothing the caller can pass -- it is answered
against the pool's seeds alone -- so when it is unmet, every call of the tool is
refused whatever its arguments. Offering such a tool only invites a refusal, and
the model spends a round trip learning what the server already knew.

This is not ``FILTER_LISTINGS_BY_PERMISSIONS``, and deliberately not gated by it.
That flag is off by default because a permission may depend on arguments that do
not exist at list time, so asking it early can deny unfairly. An operation-scope
condition cannot read arguments by construction, so that reason does not apply,
and a tool declaring no condition costs nothing here.

The answer is advisory. ``dispatch_spec`` (and a chain's per-step
``enforce_affordances``) still enforces every affordance at the call, so a
listing built a moment before a condition flips is answered, when the stale entry
is called, by an ``ActionUnavailable`` carrying the same ``code``.

Sync only, and that serves both transports: ``tools/list`` is a sync handler
that the async transport and ``alist_tools`` run in Django's thread-sensitive
executor, so a condition that queries is already off the event loop there and
drf-services' async twin would add a second hop for nothing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from rest_framework_services import (
    base_pool,
    build_offline_context,
    operation_affordances,
    unmet_operation_affordance,
)
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.tool_registry import ToolBindingLike
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding


def offered_tools(
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> tuple[list[ToolBindingLike], bool]:
    """``(the bindings to list, whether any condition was asked of this caller)``.

    Order is kept, so pagination over the result behaves as it does over the
    registry. The second element is what ``cacheScope`` derives from: a listing
    shaped by an answer asked against this caller's ``user`` and ``request`` is
    no longer byte-identical across callers, so ``public`` would licence a shared
    proxy to serve one caller's answer to another.
    """
    answers, asked = _answer(bindings, context)
    return [binding for binding, unmet in answers if unmet is None], asked


def unavailable_tools(
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> dict[str, Affordance]:
    """The bindings ``offered_tools`` leaves out, by name, each with the condition that did.

    The same pass over the same bindings, so a name is here exactly when the
    listing omits it: an in-process consumer that lists once and asks this each
    step offers what a fresh ``tools/list`` would, and can say why the rest are
    missing. For a chain the affordance is the first unmet one in step order,
    which is the step whose refusal a call would meet first.
    """
    answers, _asked = _answer(bindings, context)
    return {binding.name: unmet for binding, unmet in answers if unmet is not None}


def declares_operation_conditions(bindings: Iterable[ToolBindingLike]) -> bool:
    """Whether any of ``bindings`` would be asked a condition at all.

    Pure Python over the declarations -- no pool, no query, no permission check
    -- so a caller can skip an executor hop when the answer is certain to be
    empty. ``True`` for exactly the bindings ``_answer`` asks about, so a skip
    taken on ``False`` cannot change a result.
    """
    return any(_asked_specs(binding) for binding in bindings)


def _answer(
    bindings: list[ToolBindingLike], context: MCPCallContext
) -> tuple[list[tuple[ToolBindingLike, Affordance | None]], bool]:
    """Each binding with the first condition refusing it now, or ``None``.

    ``always_listed=True`` answers ``None`` without asking, as it keeps a
    binding listed that ``FILTER_LISTINGS_BY_PERMISSIONS`` would drop: the name
    promises the binding is always in the list, and a discovery aid whose
    ``tools/call`` is refused with a ``code`` is the same trade the permission
    case already makes.

    **One pool per pass, built only when first needed.** Most tools declare no
    condition, so a pass over them builds nothing; the first binding that does
    builds the pool every later one is asked against, since the seeds are the
    same for every tool in one request.
    """
    pool: dict[str, Any] | None = None
    answers: list[tuple[ToolBindingLike, Affordance | None]] = []
    for binding in bindings:
        gating: tuple[ServiceSpec[Any, Any, Any], ...] = _asked_specs(binding)
        if not gating:
            answers.append((binding, None))
            continue
        if pool is None:
            pool = _list_time_pool(context)
        answers.append((binding, _first_unmet(gating, pool)))
    return answers, pool is not None


def _asked_specs(binding: ToolBindingLike) -> tuple[ServiceSpec[Any, Any, Any], ...]:
    """The specs a pass asks about for ``binding``: none when ``always_listed``.

    A conditional rather than an ``and``, so each way out is a branch coverage
    can see: test_always_listed_keeps_an_unavailable_tool holds the first, and
    test_a_listing_of_tools_declaring_nothing_builds_no_pool the empty gating.
    """
    return () if binding.always_listed else _gating_specs(binding)


def _first_unmet(
    gating: tuple[ServiceSpec[Any, Any, Any], ...], pool: dict[str, Any]
) -> Affordance | None:
    """The first unmet condition across ``gating``, in order, or ``None``.

    Stops at the first spec refusing, as the listing always did, so a later
    step's condition is not asked once an earlier one has decided.

    ``reserved`` is left at drf-services' default because this server registers
    no ``PoolSeeds``: dispatch runs with the default seeds, so the condition is
    asked here with the names it sees there.
    """
    for spec in gating:
        unmet: Affordance | None = unmet_operation_affordance(spec, pool)
        if unmet is not None:
            return unmet
    return None


def _gating_specs(binding: ToolBindingLike) -> tuple[ServiceSpec[Any, Any, Any], ...]:
    """The service specs whose operation conditions gate *every* call of ``binding``.

    A service tool's own spec. Never a selector tool's: a ``SelectorSpec``'s
    ``affordances`` names *other* operations to project onto its rows, not a
    condition on the read, so it gates nothing and asks nothing here.

    **A chain is gated by every one of its service steps.** ``_run_steps`` runs the
    steps in declaration order with no branch that skips one -- a ``ChainStep``
    is an alias, a spec and an ``inputs`` callable, with nothing to make it
    conditional -- and a step's refusal ends the chain. So a step whose operation
    condition is unmet refuses every call that reaches it, and a call that does
    not reach it has already failed on an earlier step: no call of the chain can
    succeed, which is the one case this hides a tool for. Were steps ever to
    become conditional, this would have to stop treating every step as reached,
    since a step that may be skipped cannot refuse the chain on its own.

    Only specs with an operation-scope condition are returned, as drf-services'
    ``operation_affordances`` selects them: the callable conditions, never the
    row conditions, and never a selector's. That is what keeps a listing with
    nothing to ask from building a pool, and from being served as private: a
    tool declaring only row conditions is listed exactly as one declaring
    nothing is, since there is no row at list time to ask them about.
    """
    specs: tuple[Any, ...] = (
        tuple(step.spec for step in binding.steps)
        if isinstance(binding, ChainToolBinding)
        else (binding.spec,)
    )
    return tuple(spec for spec in specs if operation_affordances(spec))


def _list_time_pool(context: MCPCallContext) -> dict[str, Any]:
    """The seeds a condition is asked against at list time, as the call would hand them.

    ``user`` is the token's user, as ``dispatch_spec`` receives it. ``request`` is
    the same kind of object a call's condition reads: the DRF ``Request`` that
    ``build_offline_context`` wraps around a copy of the transport's request,
    with the method forced to ``POST`` and ``query_params`` replaced by an empty
    mapping, as ``tools/call`` and a chain build it. Handing the raw
    ``HttpRequest`` here, or ``None``, would let a condition reading ``request``
    answer differently in the list than at the call -- listing a tool that is
    then refused, or hiding one that would run.

    There are no call arguments at list time, so ``request.data`` is empty; a
    condition reading it was reading client input, which an operation-scope
    condition must not depend on.
    """
    offline = build_offline_context(
        context.token.user, http_request=context.http_request, query_params={}
    )
    return base_pool(user=context.token.user, request=offline.request)


__all__ = ["declares_operation_conditions", "offered_tools", "unavailable_tools"]
