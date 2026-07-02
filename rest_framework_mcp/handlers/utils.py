from __future__ import annotations

import dataclasses
import inspect
from collections.abc import Callable, Mapping
from typing import Any

from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.request import Request
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.permissions.types.mcp_permission import MCPPermission
from rest_framework_mcp.auth.rate_limits.types.mcp_rate_limit import MCPRateLimit
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.constants import (
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
    UnknownArguments,
)
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding

_SPREAD_BINDINGS = frozenset(
    {ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS}
)


def binding_input_serializer(binding: Any) -> type | None:
    """The serializer a binding actually validates ``arguments`` against.

    A service tool validates against its ``spec.input_serializer``; a selector
    tool against the MCP-only ``binding.input_serializer``; a chain tool against
    its ``resolved_input_serializer`` (explicit or first-step fallback). ``None``
    means the binding has no serializer, so there is nothing to validate against.
    """
    if isinstance(binding, SelectorToolBinding):
        return binding.input_serializer
    if isinstance(binding, ChainToolBinding):
        return binding.resolved_input_serializer
    return binding.spec.input_serializer


def advertises_closed_schema(binding: Any) -> bool:
    """Whether ``tools/list`` may stamp ``additionalProperties: false`` for ``binding``.

    A closed schema is honest only when the runtime *actually* rejects unknown
    keys. ``REJECT`` is a silent no-op for a **serializer-less** binding: a
    service with no ``input_serializer`` is downgraded to IGNORE / PASSTHROUGH by
    :func:`services_dispatch_policies`, and the read-path validator
    (:func:`build_validated_input_serializer`) short-circuits before the
    unknown-key check when there is no serializer. In both cases undeclared keys
    are accepted, so the advertised schema must stay open. A binding therefore
    advertises a closed schema only under ``REJECT`` *and* with a serializer to
    validate against.
    """
    return (
        binding.unknown_arguments is UnknownArguments.REJECT
        and binding_input_serializer(binding) is not None
    )


def services_dispatch_policies(binding: Any) -> tuple[ArgumentBinding, UnknownArguments]:
    """The ``(argument_binding, unknown_arguments)`` to pass ``dispatch_spec``.

    The binding's enums are drf-services' own (re-exported), so the binding
    value passes straight through; only the ``unknown_arguments`` choice is
    refined to preserve MCP's historical behaviour:

    - A **selector** is validated by the MCP layer against its own
      ``inputSchema`` (the binding's ``input_serializer`` + filter-set fields +
      post-fetch knobs) *before* dispatch, so the neutral core must not
      re-reject: its declared set is only the selector signature, which excludes
      filter / ordering / pagination args. Always ``IGNORE``.
    - A **service with no ``input_serializer``** has an empty declared set, so
      MCP's old "unknown-args policy short-circuits, raw args spread when
      spreading" maps to ``PASSTHROUGH`` under the ``SPREAD_*`` bindings (raw
      args still reach the callable) and ``IGNORE`` under ``BUNDLE`` (raw args
      drop, ``data`` stays ``None``) — never a rejection against an empty set.
    - Otherwise the service binding's own ``unknown_arguments`` carries over.
    """
    argument_binding = binding.argument_binding
    if not isinstance(binding.spec, ServiceSpec):
        return argument_binding, UnknownArguments.IGNORE
    if binding.spec.input_serializer is None:
        unknown = (
            UnknownArguments.PASSTHROUGH
            if argument_binding in _SPREAD_BINDINGS
            else UnknownArguments.IGNORE
        )
    else:
        unknown = binding.unknown_arguments
    return argument_binding, unknown


def check_permissions(
    permissions: tuple[Any, ...],
    http_request: HttpRequest,
    token: TokenInfo,
) -> tuple[bool, list[str]]:
    """Return ``(allowed, required_scopes)`` after evaluating every permission.

    Permissions are AND-combined (all must pass). The aggregated
    ``required_scopes`` from any permission that *would* deny is returned so
    the transport can surface them in the ``WWW-Authenticate`` header.
    """
    required: list[str] = []
    allowed: bool = True
    for perm in permissions:
        if not isinstance(perm, MCPPermission):  # defensive — caught at registration
            continue  # pragma: no cover
        if not perm.has_permission(http_request, token):
            allowed = False
            required.extend(perm.required_scopes())
    return allowed, required


def consume_rate_limits(
    rate_limits: tuple[Any, ...],
    http_request: HttpRequest,
    token: TokenInfo,
) -> int | None:
    """Run every rate limiter in order, returning the largest retry-after.

    Each limiter's ``consume`` updates its quota atomically and returns the
    suggested retry-after-seconds or ``None`` to allow. The handler stops at
    the first denial (no point further consuming quota when the call is
    already going to fail), so listing several limits per binding works as
    "deny if any of these is exhausted".
    """
    for limiter in rate_limits:
        if not isinstance(limiter, MCPRateLimit):  # defensive — caught at registration
            continue  # pragma: no cover
        retry_after: int | None = limiter.consume(http_request, token)
        if retry_after is not None:
            return retry_after
    return None


def build_validated_input_serializer(
    arguments: dict[str, Any],
    input_serializer: type | None,
    *,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    additional_known_keys: frozenset[str] = frozenset(),
    partial: bool = False,
) -> tuple[Any, drf_serializers.Serializer | None]:
    """Validate ``arguments``; return ``(validated, bound_serializer)``.

    The validator for the **read-shaped** transport paths (selector tools and
    chain steps), where the input is a flat, instance-free, context-free arg
    map. Service-tool validation now flows through drf-services'
    ``dispatch_spec`` (instance resolution, ``input_serializer_context``, the
    bundle/spread pool), so the instance- and context-aware variants live there.

    ``validated`` is:
      - the dataclass instance produced by a ``DataclassSerializer`` (when
        ``input_serializer`` is a bare ``@dataclass`` — auto-wrapped),
      - the ``validated_data`` dict for a plain DRF ``Serializer``,
      - ``None`` when ``input_serializer`` is ``None``.

    Raises :class:`drf_serializers.ValidationError` on invalid input.

    ``partial`` relaxes required-field validation; the read paths default to
    full validation (``False``).

    ``partial`` mirrors sister-repo 0.16's ``spec.partial``: MCP has no
    HTTP method to derive partiality from, so ``False`` (full validation)
    is the default and the spec's explicit flag is the only way to relax
    it. ``instance`` (when supplied) is the row resolved by
    ``spec.instance_selector_spec`` — the serializer is constructed
    DRF-style (``serializer(instance, data=..., partial=...)``) so
    instance-dependent ``validate()`` / field validators see
    ``self.instance`` identically over MCP and HTTP.

    ``unknown_arguments`` (default :attr:`UnknownArguments.REJECT`) controls
    what happens to ``arguments`` keys that aren't part of the binding's
    declared field set. ``additional_known_keys`` lets the caller widen
    the "known" set beyond the serializer's own fields — selector tools
    pass in their filter / ordering / pagination keys here, so they're
    not seen as "unknown".

    Reserved transport-pool seeds (``request`` / ``user`` / ``data`` /
    ``instance`` / ``serializer``) and selector-tool post-fetch keys
    (``ordering`` / ``page`` / ``limit``) are always exempted from the
    unknown-key check; the dispatch pipeline handles them and they never
    legitimately reach validation as user-typed args.
    """
    if input_serializer is None:
        return None, None
    target: type = input_serializer
    if dataclasses.is_dataclass(target) and not isinstance(target, type):  # pragma: no cover
        raise TypeError("input_serializer must be a class")
    serializer_kwargs: dict[str, Any] = {"data": arguments, "partial": partial}
    if isinstance(target, type) and dataclasses.is_dataclass(target):
        wrapper_cls: type[drf_serializers.Serializer] = type(
            f"{target.__name__}Serializer",
            (DataclassSerializer,),
            {"Meta": type("Meta", (), {"dataclass": target})},
        )
        serializer = wrapper_cls(**serializer_kwargs)
    else:
        serializer = target(**serializer_kwargs)

    declared_fields: set[str] = set(serializer.fields.keys())
    known: set[str] = (
        declared_fields
        | set(additional_known_keys)
        | RESERVED_POOL_SEEDS
        | RESERVED_POST_FETCH_KEYS
    )
    unknown_keys: set[str] = set(arguments.keys()) - known

    if unknown_keys and unknown_arguments is UnknownArguments.REJECT:
        offenders: str = ", ".join(sorted(unknown_keys))
        raise drf_serializers.ValidationError(
            {"non_field_errors": [f"Unknown argument(s): {offenders}"]}
        )

    serializer.is_valid(raise_exception=True)
    validated: Any = serializer.validated_data

    # ``PASSTHROUGH``: merge truly-unknown user keys onto the validated
    # dict. Only meaningful when ``validated`` is dict-shaped (plain
    # ``Serializer`` output); ``DataclassSerializer`` returns a dataclass
    # instance which isn't a merge target — those bindings get
    # ``IGNORE``-equivalent behaviour even under ``PASSTHROUGH``.
    # Reserved keys (pool seeds, post-fetch) are excluded from the merge
    # so clients can't poison transport-controlled state.
    if unknown_keys and unknown_arguments is UnknownArguments.PASSTHROUGH:
        merge_keys: set[str] = unknown_keys - RESERVED_POOL_SEEDS - RESERVED_POST_FETCH_KEYS
        if isinstance(validated, dict):
            for key in merge_keys:
                validated.setdefault(key, arguments[key])

    return validated, serializer


def validate_input_against_serializer(
    arguments: dict[str, Any],
    input_serializer: type | None,
    *,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    additional_known_keys: frozenset[str] = frozenset(),
) -> Any:
    """Validate ``arguments`` against ``input_serializer``; return ``validated`` only.

    Thin wrapper over :func:`build_validated_input_serializer` (see there
    for the full semantics) for callers that don't need the bound
    serializer — the selector-tool and chain-tool paths.
    """
    validated, _serializer = build_validated_input_serializer(
        arguments,
        input_serializer,
        unknown_arguments=unknown_arguments,
        additional_known_keys=additional_known_keys,
    )
    return validated


def validation_error_data(detail: Any, value: Any) -> dict[str, Any]:
    """Build the ``data`` payload for a JSON-RPC validation error.

    Always carries the per-field ``detail`` shape that DRF / sister-repo
    validation produces. When ``REST_FRAMEWORK_MCP["INCLUDE_VALIDATION_VALUE"]``
    is True, ``value`` is also echoed back — useful for debugging schema
    mismatches against opaque client SDKs. Off by default because the value
    may carry sensitive payloads (PII, secrets) consumers don't want flowing
    back to the client or appearing in client-side logs.
    """
    payload: dict[str, Any] = {"detail": detail}
    if get_setting("INCLUDE_VALIDATION_VALUE"):
        payload["value"] = value
    return payload


def invoke_context_provider(
    provider: Callable[..., Mapping[str, Any]],
    view: Any,
    request: Request,
    *,
    extras: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Call a serializer-context ``provider(view, request, **declared)``.

    ``view`` / ``request`` are forwarded positionally and unconditionally;
    each entry in ``extras`` — the resolved data about to be serialized
    (``result`` / ``instance`` / ``page``) — is passed by keyword **only**
    when ``provider`` declares a parameter of that name or accepts
    ``**kwargs``.

    This mirrors sister-repo 0.15's ``output_serializer_context`` contract
    (the library's private ``views.utils._invoke_with_extras``) so the same
    provider works identically whether dispatched through a DRF view or
    through MCP tool dispatch. Reproduced locally rather than importing the
    private helper, matching the package's "transport-shaped equivalents"
    rule for kwarg-pool / validation / rendering.

    A legacy ``(view, request)`` provider declares neither extra and is
    therefore called as ``provider(view, request)`` exactly as before —
    regardless of how it names those two positional parameters.
    """
    params = inspect.signature(provider).parameters
    accepts_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    declared: dict[str, Any] = {
        name: value for name, value in extras.items() if accepts_var_keyword or name in params
    }
    return provider(view, request, **declared)


__all__ = [
    "advertises_closed_schema",
    "binding_input_serializer",
    "build_validated_input_serializer",
    "check_permissions",
    "consume_rate_limits",
    "invoke_context_provider",
    "services_dispatch_policies",
    "validate_input_against_serializer",
    "validation_error_data",
]
