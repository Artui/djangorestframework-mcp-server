"""Single source of truth for tools/call kwarg-pool construction.

Both service-tool dispatch and selector-tool dispatch build a pool of
candidate kwargs that :func:`resolve_callable_kwargs` then narrows down
to whatever the dispatched callable actually declares. The pool always
carries ``request`` / ``user``; the rest depends on the binding's
:class:`ArgumentBinding` mode and on the spec's optional
``kwargs(view, request)`` provider.

Keeping pool construction in one place lets the two dispatch paths share
the precedence rules (provider vs spread), the pipeline-reserved key
exclusions (``ordering`` / ``page`` / ``limit`` — selector-tool
post-fetch knobs that must not flow into the selector itself), and the
pool-seed protection (MCP clients cannot poison ``request`` / ``user`` /
``data`` via top-level argument keys — those are transport-controlled).
"""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
)
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.server.types.mcp_service_view import MCPServiceView


def build_call_pool(
    binding: ToolBinding | SelectorToolBinding,
    *,
    drf_request: Any,
    user: Any,
    validated: Any,
    arguments_raw: dict[str, Any],
) -> dict[str, Any]:
    """Assemble the kwarg pool for ``resolve_callable_kwargs``.

    Behavior per :class:`ArgumentBinding` mode:

    - ``DATA_ONLY``: pool = ``{request, user, data=validated}`` plus the
      ``spec.kwargs(...)`` provider's keys. This is the historical
      service-tool shape — selectors that opt in here look like RPC
      reads with a single dict payload.
    - ``MERGE``: pool = ``{request, user}`` plus the spread arguments
      (validated dict when available, otherwise the raw arguments
      minus pipeline-reserved keys), then the provider's keys override
      on conflict. Spread also includes ``data=`` (the dict form) so
      callables that declare ``def fn(*, data)`` keep working.
    - ``REPLACE``: same as ``MERGE`` but the spread arguments win on
      conflict instead — the spec.kwargs provider's keys land in the
      pool *first* and are then overwritten.

    Pipeline-reserved keys (``ordering`` / ``page`` / ``limit``) are
    excluded from the spread in every mode for selector tools; service
    tools have no such reservation but the keys are excluded anyway for
    consistency (a service author shouldn't accidentally bind a
    parameter that conflicts with selector-tool post-fetch).
    """
    pool: dict[str, Any] = {
        "request": drf_request,
        "user": user,
    }

    spread: dict[str, Any] = _spread_arguments(validated, arguments_raw, binding.argument_binding)
    provider_kwargs: dict[str, Any] = _resolve_kwargs_provider(binding, drf_request)

    # ``data`` is always present in the pool — services / selectors that
    # don't declare an ``input_serializer`` receive ``data=None``, matching
    # the historical shape before :class:`ArgumentBinding` was introduced.
    # ``resolve_callable_kwargs`` only forwards it to callables that
    # actually declare a ``data`` parameter, so spreading the dict and
    # also exposing it as ``data=`` aren't mutually exclusive — callable
    # authors pick whichever shape fits their signature.
    pool["data"] = validated

    if binding.argument_binding is ArgumentBinding.DATA_ONLY:
        pool.update(provider_kwargs)
    elif binding.argument_binding is ArgumentBinding.MERGE:
        pool.update(spread)
        # Provider overrides on conflict — author-declared kwargs win
        # over client-supplied ones (e.g. ``project_id`` scoped by the
        # provider must not be overridable from the client).
        pool.update(provider_kwargs)
    else:
        # REPLACE — provider lands first, then spread overrides on
        # conflict. Useful when the provider supplies *defaults* the
        # client may customise.
        pool.update(provider_kwargs)
        pool.update(spread)

    return pool


def _spread_arguments(
    validated: Any,
    arguments_raw: dict[str, Any],
    argument_binding: ArgumentBinding,
) -> dict[str, Any]:
    """Compute the top-level spread dict for ``MERGE`` / ``REPLACE`` modes.

    ``DATA_ONLY`` callers never use the result — short-circuit there to
    avoid materialising a dict that gets discarded.

    Source priority for the spread:

    - ``validated`` dict (output of ``input_serializer``), when present
      and dict-shaped.
    - ``arguments_raw`` (the JSON ``arguments`` from ``tools/call``)
      minus pipeline-reserved keys, when no validator ran *or* the
      validated value isn't a dict (e.g. a bare-dataclass instance).
      In the dataclass case we don't try to introspect; bare-dataclass
      authors typically pair their callable with ``DATA_ONLY``.

    Reserved pool-seed keys (``request`` / ``user`` / ``data``) are
    always stripped — clients can't poison transport-controlled state
    by including those names in ``arguments``.
    """
    if argument_binding is ArgumentBinding.DATA_ONLY:
        return {}
    if isinstance(validated, dict):
        source: dict[str, Any] = validated
    else:
        source = arguments_raw
    excluded: frozenset[str] = RESERVED_POST_FETCH_KEYS | RESERVED_POOL_SEEDS
    return {k: v for k, v in source.items() if k not in excluded}


def _resolve_kwargs_provider(
    binding: ToolBinding | SelectorToolBinding, drf_request: Any
) -> dict[str, Any]:
    """Invoke ``spec.kwargs(view, request)`` if declared; return an empty dict otherwise."""
    if binding.spec.kwargs is None:
        return {}
    view = MCPServiceView(request=drf_request, action=binding.name)
    return dict(binding.spec.kwargs(view, drf_request))


__all__ = ["build_call_pool"]
