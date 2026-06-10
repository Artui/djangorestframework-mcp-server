"""Adapter-layer helpers shared by the service / selector adapters.

The single piece of shared logic right now is the registration-time
check that an ``input_serializer``'s declared fields actually map to
the dispatched callable's parameter list. The check is intentionally
ran at adapter time — *before* the binding lands in a registry — so
configuration mistakes surface during application startup rather than
the first time a client calls the tool.
"""

from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers

from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
)


def validate_input_serializer_against_callable(
    *,
    label: str,
    input_serializer: type | None,
    callable_: Any,
    argument_binding: ArgumentBinding,
    spec_kwargs_provides: frozenset[str] = frozenset(),
    provides_instance: bool = False,
) -> None:
    """Fail-fast at registration time when input shape doesn't match the callable.

    Runs two complementary checks:

    1. **Serializer fields reach the callable** — every declared
       ``input_serializer`` field must correspond to a named parameter
       on the callable, or be a reserved-name exemption (pool seed /
       post-fetch key), or be absorbed by ``**kwargs`` / a ``data``
       bundle parameter. Without this check, a misspelt field name
       would be silently dropped by :func:`resolve_callable_kwargs`
       at dispatch time.

    2. **Required callable parameters have a source** — every
       parameter the callable declares as required (no default,
       no ``**kwargs``) must come from somewhere the MCP transport
       can produce at dispatch: an ``input_serializer`` field, a
       reserved pool seed (``request`` / ``user`` / ``data``), or an
       explicit ``spec_kwargs_provides`` opt-in declaring that
       ``spec.kwargs(...)`` will supply the value. Post-fetch keys
       (``ordering`` / ``page`` / ``limit``) are *not* sources — the
       dispatch pipeline consumes them before the callable runs.

       The opt-in exists because ``spec.kwargs`` is a runtime callable
       whose output depends on the transport context. A spec reused
       across DRF API views and MCP tools may receive a populated
       ``view.kwargs`` in the DRF case (URL path params) and an empty
       one in the MCP-tool case, returning ``None`` for keys it tried
       to derive from path params. Trusting ``spec.kwargs`` to
       satisfy a required callable parameter on the MCP side is
       therefore explicit — list the parameter names in
       ``spec_kwargs_provides`` to acknowledge the contract.

    ``input_serializer=None`` skips check (1) (no fields to validate)
    but check (2) still runs against the pool-seed and opt-in sources.

    ``callable_=None`` short-circuits everything — the per-adapter
    "selector=None" / "service=None" guards already cover that case
    with a more specific error.
    """
    if callable_ is None:
        return

    sig = _resolve_signature(callable_)
    if sig is None:  # pragma: no cover - paired with _resolve_signature's except branch
        # Builtin / C-extension callables don't expose a signature. The
        # check can't fire — fall through silently rather than spuriously
        # raising on something the framework can't introspect.
        return

    if argument_binding is ArgumentBinding.DATA_ONLY:
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
        # Sister-repo 0.16: the bound, validated serializer is a pool seed —
        # a callable that owns persistence via ``serializer.save()`` receives
        # the payload through it and needs no ``data`` parameter.
        return
    raise ImproperlyConfigured(
        f"{label}: argument_binding=DATA_ONLY requires the callable to declare a "
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
    # If the callable declares ``data``, the full validated payload is
    # forwarded under that name — individual fields don't need to map to
    # individual parameters. This is a deliberate MERGE-mode pattern
    # (``def fn(*, data, request)``): the callable wants the bundle, not
    # the spread. Skip the per-field check in that case.
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
) -> None:
    """Every required callable parameter must have a static source.

    Sources, in priority order:

    - ``request`` / ``user`` / ``data`` — always in the pool (transport
      seeds). In ``MERGE`` / ``REPLACE`` mode the validated payload
      also lands as ``data=`` in the pool, so a callable declaring
      ``data`` always gets it regardless of mode. ``instance`` counts
      only when the spec resolves one (``provides_instance``) and
      ``serializer`` only when an ``input_serializer`` is declared —
      sister-repo 0.16's conditional seeds.
    - ``input_serializer`` fields — only count as sources in
      ``MERGE`` / ``REPLACE`` mode, where the validated dict is
      spread into the pool. In ``DATA_ONLY`` mode the fields are
      bundled into ``data`` and individual names never reach the
      callable as kwargs.
    - ``spec_kwargs_provides`` — explicit opt-in declaring that
      ``spec.kwargs(view, request)`` will supply these names at
      dispatch. The registration site is the right place to make
      this trust visible because the same spec may behave
      differently across transports.

    ``**kwargs`` callables are exempt — every required name is
    structurally satisfiable.

    When ``input_serializer`` is ``None`` we treat the binding as
    "trust mode" — the client's raw ``arguments`` dict is spread
    verbatim and there is no static contract to validate against.
    The check then only requires that pool-seed sources cover the
    callable's required params; the rest are presumed to come from
    the raw arguments. Combined with ``spec_kwargs_provides``, this
    still catches a callable that declares a required param the
    transport has no way to produce (e.g. ``DATA_ONLY`` with no
    serializer and a callable that doesn't take ``data``).
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
    # Conditional pool seeds: ``instance`` only exists when the spec
    # carries an ``instance_selector_spec`` with a selector, ``serializer``
    # only when an ``input_serializer`` produces a bound instance. The
    # unconditional seeds are ``request`` / ``user`` / ``data``.
    sources: set[str] = {"request", "user", "data"}
    if provides_instance:
        sources.add("instance")
    if input_serializer is not None:
        sources.add("serializer")
    sources.update(spec_kwargs_provides)
    if argument_binding is not ArgumentBinding.DATA_ONLY:
        if input_serializer is not None:
            sources.update(_serializer_field_names(input_serializer))
        else:
            # Trust mode — raw ``arguments`` are spread verbatim, so any
            # name the callable declares can in principle be supplied by
            # the client. We can't validate names statically, but we
            # *can* still flag a required param that's outside the
            # spread (i.e. a pool seed the transport could supply but
            # the callable didn't declare any other way). Since the
            # raw-spread set is dynamic, mark every required param as
            # satisfiable.
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


def _accepts_var_keyword(sig: inspect.Signature) -> bool:
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _serializer_field_names(input_serializer: type) -> Iterable[str]:
    """Best-effort field-name extraction for the kinds of inputs MCP accepts.

    Supports the same shapes :func:`build_input_schema` does: a DRF
    ``Serializer`` subclass (via ``_declared_fields``), or a bare
    ``@dataclass`` (via :func:`dataclasses.fields`). Anything else is
    ignored — :func:`validate_input_serializer_against_callable` will
    then have nothing to validate against, which is preferable to a
    false positive.
    """
    if isinstance(input_serializer, type) and issubclass(
        input_serializer, drf_serializers.Serializer
    ):
        return tuple(input_serializer._declared_fields.keys())  # type: ignore[attr-defined]
    if isinstance(input_serializer, type) and dataclasses.is_dataclass(input_serializer):
        return tuple(f.name for f in dataclasses.fields(input_serializer))
    return ()


__all__ = ["validate_input_serializer_against_callable"]
