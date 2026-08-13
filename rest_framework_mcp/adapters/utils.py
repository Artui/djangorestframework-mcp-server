"""Adapter-layer helpers shared by the service / selector / chain adapters.

The ``validate_*`` functions run at adapter time — *before* the binding lands
in a registry — so a configuration mistake surfaces during application startup
rather than the first time a client calls the tool. The ``merge_*`` ones fold
the several contributions a binding assembles (tool annotations, ``_meta``
bundles) into the single dict the wire types emit.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.validate_channel_names import validate_channel_names

from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
)
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg


def validate_url_kwargs(*, label: str, url_kwargs: tuple[UrlKwarg, ...]) -> None:
    """Fail-fast at registration time on a bad ``url_kwargs`` declaration.

    A URL kwarg is popped into the off-HTTP ``view.kwargs`` and stripped from the
    spec params, so its name must not collide with a reserved transport key —
    the post-fetch pagination knobs (``ordering`` / ``page`` / ``limit``) or the
    dispatcher's pool seeds — nor be declared twice, nor claim to be ``required``
    while carrying a ``default``. Colliding with an ordinary spec input is
    *allowed*: that is the intended way to route a route-capture the spec also
    reads.

    The checks live in drf-services' ``validate_channel_names``, which folds in
    the pool seeds it owns; only the pagination names are ours to contribute.
    Sharing the check is what keeps this package's notion of a valid declaration
    from drifting away from the agent toolset's.
    """
    validate_channel_names(
        label=label,
        kind="url_kwargs",
        declarations=url_kwargs,
        reserved=RESERVED_POST_FETCH_KEYS,
    )


def validate_query_params(
    *,
    label: str,
    query_params: tuple[QueryParam, ...],
    url_kwargs: tuple[UrlKwarg, ...] = (),
) -> None:
    """Fail-fast at registration time on a bad ``query_params`` declaration.

    The sibling of :func:`validate_url_kwargs`, delegating the name checks to the
    same shared ``validate_channel_names`` with the same reserved set: a query
    param is popped out of the caller's arguments exactly as a URL kwarg is, so
    the same names are off-limits. ``QueryParam`` carries no ``required`` flag,
    so the validator's required-with-a-default check is inert here by
    construction — a read-shaping param the spec runs fine without cannot be
    required.

    One name cannot route to two channels: a URL kwarg lands in ``view.kwargs``
    and a query param in ``request.query_params``, and a value is popped from the
    arguments once. **That exclusivity is checked here rather than upstream**
    because ``validate_channel_names`` takes one declaration list and so cannot
    see a name in both channels; a concatenated list would report the overlap as
    a duplicate ``url_kwargs`` name and point the consumer at the wrong knob.
    """
    validate_channel_names(
        label=label,
        kind="query_params",
        declarations=query_params,
        reserved=RESERVED_POST_FETCH_KEYS,
    )
    overlap = sorted({qp.name for qp in query_params} & {uk.name for uk in url_kwargs})
    if overlap:
        raise ImproperlyConfigured(
            f"{label}: name(s) {overlap} are declared as both a QueryParam and a "
            "UrlKwarg. A value routes to one channel — query_params reaches "
            "request.query_params, url_kwargs reaches view.kwargs — and is popped "
            "from the arguments once. Pick the channel the reader actually uses."
        )


def validate_input_serializer_against_callable(
    *,
    label: str,
    input_serializer: type | None,
    callable_: Any,
    argument_binding: ArgumentBinding,
    spec_kwargs_provides: frozenset[str] = frozenset(),
    provides_instance: bool = False,
    provides_collection: bool = False,
) -> None:
    """Fail-fast at registration time when input shape doesn't match the callable.

    Runs two complementary checks:

    1. **Serializer fields reach the callable** — every declared
       ``input_serializer`` field must correspond to a named parameter on the
       callable, be a reserved-name exemption (pool seed / post-fetch key), or
       be absorbed by ``**kwargs`` / a ``data`` bundle parameter. Without it, a
       misspelt field name is silently dropped at dispatch.

    2. **Required callable parameters have a source** — every parameter with no
       default must come from something the MCP transport can produce: an
       ``input_serializer`` field, a reserved pool seed, or an explicit
       ``spec_kwargs_provides`` opt-in declaring that ``spec.kwargs(...)``
       supplies it. Post-fetch keys (``ordering`` / ``page`` / ``limit``) are
       *not* sources — the pipeline consumes them before the callable runs.

       The opt-in is explicit because ``spec.kwargs`` output depends on the
       transport: a spec reused across DRF views and MCP tools sees populated
       URL path params in the first case and none in the second, so it may
       return ``None`` for keys it derives from them.

    ``input_serializer=None`` skips check (1) but check (2) still runs against
    the pool-seed and opt-in sources. ``callable_=None`` short-circuits
    everything — the per-adapter ``selector=None`` / ``service=None`` guards
    cover that with a more specific error.
    """
    if callable_ is None:
        return

    sig = _resolve_signature(callable_)
    if sig is None:  # pragma: no cover - paired with _resolve_signature's except branch
        # Builtin / C-extension callables expose no signature, so the check
        # cannot fire; falling through beats raising on something the framework
        # cannot introspect.
        return

    if argument_binding is ArgumentBinding.BUNDLE:
        if input_serializer is not None:
            _validate_data_only(label, sig)
    else:
        if input_serializer is not None:
            _validate_merge_or_replace(label, sig, input_serializer)

    _validate_required_params_have_sources(
        label=label,
        sig=sig,
        input_serializer=input_serializer,
        argument_binding=argument_binding,
        spec_kwargs_provides=spec_kwargs_provides,
        provides_instance=provides_instance,
        provides_collection=provides_collection,
    )


def _resolve_signature(callable_: Any) -> inspect.Signature | None:
    """Best-effort ``inspect.signature`` that tolerates exotic callables."""
    try:
        return inspect.signature(callable_)
    except (TypeError, ValueError):  # pragma: no cover - defensive fallback
        return None


def _validate_data_only(label: str, sig: inspect.Signature) -> None:
    if _accepts_var_keyword(sig):
        return
    if "data" in sig.parameters:
        return
    if "serializer" in sig.parameters:
        # The bound, validated serializer is itself a pool seed: a callable that
        # owns persistence via ``serializer.save()`` receives the payload
        # through it and needs no ``data`` parameter.
        return
    raise ImproperlyConfigured(
        f"{label}: argument_binding=BUNDLE requires the callable to declare a "
        "`data` parameter (or `serializer`, or accept `**kwargs`) — the validated "
        "input payload is forwarded under those names. The callable declares "
        "none of them, so the payload would be silently dropped at dispatch time."
    )


def _validate_merge_or_replace(label: str, sig: inspect.Signature, input_serializer: type) -> None:
    if _accepts_var_keyword(sig):
        return
    declared_params: frozenset[str] = frozenset(
        name
        for name, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    )
    # A callable declaring ``data`` receives the whole validated payload under
    # that name, so its fields need not map to individual parameters — a
    # deliberate spread-mode pattern (``def fn(*, data, request)``).
    if "data" in declared_params:
        return
    fields: frozenset[str] = frozenset(_serializer_field_names(input_serializer))
    exempt: frozenset[str] = RESERVED_POOL_SEEDS | RESERVED_POST_FETCH_KEYS
    unmatched: set[str] = set(fields - declared_params - exempt)
    if unmatched:
        raise ImproperlyConfigured(
            f"{label}: input_serializer declares field(s) {sorted(unmatched)!r} "
            "that the dispatched callable does not accept as parameters and the "
            "callable has no `**kwargs` catch-all (nor a `data` parameter to "
            "receive the validated payload as a bundle). Those fields would be "
            "silently dropped at dispatch time. Add the parameter(s) to the "
            "callable signature, declare `**kwargs` / `data`, or remove the "
            "field(s) from the serializer."
        )


def _validate_required_params_have_sources(
    *,
    label: str,
    sig: inspect.Signature,
    input_serializer: type | None,
    argument_binding: ArgumentBinding,
    spec_kwargs_provides: frozenset[str],
    provides_instance: bool,
    provides_collection: bool,
) -> None:
    """Every required callable parameter must have a static source.

    Sources, in priority order:

    - **Pool seeds.** ``request`` / ``user`` / ``data`` / ``progress`` always;
      ``instance`` and ``collection`` only when the spec resolves one, and
      ``serializer`` only when an ``input_serializer`` is declared.
    - **``input_serializer`` fields**, in the spread modes only, where the
      validated dict is spread into the pool. Under ``BUNDLE`` the fields ride
      inside ``data`` and their names never reach the callable as kwargs.
    - **``spec_kwargs_provides``** — the explicit opt-in that
      ``spec.kwargs(view, request)`` supplies these names at dispatch.

    ``**kwargs`` callables are exempt: every required name is structurally
    satisfiable. With ``input_serializer=None`` the binding is in trust mode —
    the client's raw ``arguments`` are spread verbatim, so there is no static
    contract and only the pool seeds are checked. That still catches a callable
    the transport could never satisfy, such as ``BUNDLE`` with no serializer and
    no ``data`` parameter.
    """
    if _accepts_var_keyword(sig):
        return
    required_params: frozenset[str] = frozenset(
        name
        for name, param in sig.parameters.items()
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
        and param.default is inspect.Parameter.empty
    )
    # ``progress`` is an unconditional seed even though most requests carry
    # nowhere to send it: drf-services substitutes its no-op reporter, so the
    # parameter is always satisfiable and refusing to register a service that
    # declares one would refuse a service that runs perfectly well.
    sources: set[str] = {"request", "user", "data", "progress"}
    if provides_instance:
        sources.add("instance")
    if provides_collection:
        sources.add("collection")
    if input_serializer is not None:
        sources.add("serializer")
    sources.update(spec_kwargs_provides)
    if argument_binding is not ArgumentBinding.BUNDLE:
        if input_serializer is not None:
            sources.update(_serializer_field_names(input_serializer))
        else:
            # Trust mode: raw ``arguments`` are spread verbatim, so the client
            # can in principle supply any name the callable declares. The set is
            # dynamic and cannot be validated statically, so every required
            # param counts as satisfiable.
            sources.update(required_params)
    missing: set[str] = set(required_params) - sources
    if missing:
        sources_human = ", ".join(sorted(sources)) or "(none)"
        raise ImproperlyConfigured(
            f"{label}: callable declares required parameter(s) {sorted(missing)!r} "
            "with no static source on the MCP transport. Available sources are: "
            f"{sources_human}. Add the parameter(s) to ``input_serializer``, give "
            "them defaults on the callable, accept ``**kwargs``, or — if "
            "``spec.kwargs(...)`` is intentionally supplying them — pass "
            "``spec_kwargs_provides=(...)`` at registration to acknowledge that "
            "contract. (``spec.kwargs`` output is not assumed because its "
            "behaviour can differ between DRF API-view and MCP transports.)"
        )


def merge_tool_annotations(explicit: dict[str, Any] | None, *, read_only: bool) -> dict[str, Any]:
    """Auto-derive a tool's MCP ``ToolAnnotations``, explicit hints winning.

    A tool's mutation profile is known from its kind, so the standard MCP hints
    are stamped here rather than hand-set downstream:

    - ``read_only=True`` (selector tools, and chains whose every step is a
      selector) → ``{"readOnlyHint": True}``. ``destructiveHint`` /
      ``idempotentHint`` are deliberately *not* emitted — the MCP spec defines
      them as meaningful only when ``readOnlyHint`` is false.
    - ``read_only=False`` (service tools, and chains with any service step) →
      ``{"readOnlyHint": False, "destructiveHint": True}``. A mutation is
      destructive by default, and ``idempotentHint`` stays unset because
      ``ServiceSpec`` carries no idempotency signal.

    Any hint supplied at registration via ``annotations=`` overrides the derived
    default: a non-destructive mutation passes
    ``annotations={"destructiveHint": False}``, an idempotent one adds
    ``{"idempotentHint": True}``, and either kind can set ``title`` /
    ``openWorldHint``. The result is stored on the binding, so it is the single
    source of truth for ``tools/list`` and for anything reading
    ``binding.annotations``.
    """
    derived: dict[str, Any] = (
        {"readOnlyHint": True} if read_only else {"readOnlyHint": False, "destructiveHint": True}
    )
    return {**derived, **(explicit or {})}


def merge_meta(*pieces: Mapping[str, Any] | None) -> dict[str, Any]:
    """Shallow-merge ``_meta`` contributions into one bundle, later wins.

    ``_meta`` is the base protocol's open extension namespace, where each
    extension owns a top-level key and several sources may contribute at once:
    the ``meta=`` a consumer passes at registration plus whatever a framework
    feature derives. Combining them here means a later feature injects its key
    by adding one argument at the adapter call site.

    Semantics, deliberately narrow:

    - **Shallow**, one level deep. A later piece replaces an earlier one's value
      for the same top-level key outright rather than deep-merging into it —
      extension keys are opaque bundles owned by one extension, and splicing two
      together produces a shape neither owner declared.
    - **Later wins**, so call sites read as precedence order: framework piece
      first and the consumer's ``meta=`` last for "consumer overrides", the
      reverse when the framework must win.
    - ``None`` and empty pieces are skipped, and the result is always a new dict,
      so no piece is mutated and no caller shares a mutable default.
    """
    merged: dict[str, Any] = {}
    for piece in pieces:
        if piece:
            merged.update(piece)
    return merged


def _accepts_var_keyword(sig: inspect.Signature) -> bool:
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _serializer_field_names(input_serializer: type) -> Iterable[str]:
    """Best-effort field-name extraction for the kinds of inputs MCP accepts.

    Supports the same shapes :func:`build_input_schema` does: a DRF
    ``Serializer`` subclass (via ``_declared_fields``) or a bare ``@dataclass``
    (via :func:`dataclasses.fields`). Anything else yields nothing to validate
    against, which is preferable to a false positive.
    """
    if isinstance(input_serializer, type) and issubclass(
        input_serializer, drf_serializers.Serializer
    ):
        return tuple(input_serializer._declared_fields.keys())
    if isinstance(input_serializer, type) and dataclasses.is_dataclass(input_serializer):
        return tuple(f.name for f in dataclasses.fields(input_serializer))
    return ()


__all__ = [
    "merge_meta",
    "merge_tool_annotations",
    "validate_input_serializer_against_callable",
    "validate_query_params",
    "validate_url_kwargs",
]
