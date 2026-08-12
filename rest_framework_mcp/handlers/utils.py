from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Mapping
from typing import Any

from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import UnsetType
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp._compat.reject_awaitable import reject_awaitable
from rest_framework_mcp.auth.rate_limits.types.mcp_rate_limit import MCPRateLimit
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import (
    MODERN_PROTOCOL_VERSIONS,
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
    CacheScope,
    JsonRpcErrorCode,
    UnknownArguments,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg

_SPREAD_BINDINGS = frozenset(
    {ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS}
)


def split_url_kwargs(
    arguments: dict[str, Any], url_kwargs: tuple[UrlKwarg, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split ``arguments`` into ``(params, url_kwarg_values)``.

    Each declared kwarg takes the model's value, else its ``default``, else stays
    absent; the name is removed from ``params`` so the value routes only through
    ``view.kwargs`` (authoritative over params) and never also reaches the spec
    as an ordinary input. Non-mutating.

    A ``required=True`` kwarg the model omitted raises
    :exc:`ServiceValidationError` here rather than failing further down:
    ``required`` in the schema is only a hint, and registration forbids pairing
    it with a ``default``.
    """
    if not url_kwargs:
        return arguments, {}
    names = {uk.name for uk in url_kwargs}
    values: dict[str, Any] = {}
    missing: list[str] = []
    for url_kwarg in url_kwargs:
        if url_kwarg.name in arguments:
            values[url_kwarg.name] = arguments[url_kwarg.name]
        elif url_kwarg.default is not None:
            values[url_kwarg.name] = url_kwarg.default
        elif url_kwarg.required:
            missing.append(url_kwarg.name)
    if missing:
        names_repr = ", ".join(repr(name) for name in sorted(missing))
        raise ServiceValidationError(
            {"non_field_errors": [f"Missing required argument(s): {names_repr}."]}
        )
    params = {key: value for key, value in arguments.items() if key not in names}
    return params, values


def split_query_params(
    arguments: dict[str, Any], query_params: tuple[QueryParam, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split ``arguments`` into ``(params, query_param_values)``.

    The sibling of :func:`split_url_kwargs`, minus the ``required`` flag a
    :class:`QueryParam` does not carry. Popping the name also keeps a query param
    out of ``unknown_arguments``.

    A ``filter_set`` field is **not** a query param. Filter fields are already in
    the tool schema and flow through as ordinary ``params``, which is where
    ``dispatch_spec`` reads them (as ``filter_data``); declaring one here pops it
    out of the args and it silently stops filtering.
    """
    if not query_params:
        return arguments, {}
    names = {qp.name for qp in query_params}
    values: dict[str, Any] = {}
    for query_param in query_params:
        if query_param.name in arguments:
            values[query_param.name] = arguments[query_param.name]
        elif query_param.default is not None:
            values[query_param.name] = query_param.default
    params = {key: value for key, value in arguments.items() if key not in names}
    return params, values


def binding_input_serializer(binding: Any) -> type | None:
    """The serializer a binding actually validates ``arguments`` against.

    A service tool uses ``spec.input_serializer``, a selector tool the MCP-only
    ``binding.input_serializer``, a chain tool its ``resolved_input_serializer``.
    ``None`` means there is nothing to validate against.
    """
    if isinstance(binding, SelectorToolBinding):
        return binding.input_serializer
    if isinstance(binding, ChainToolBinding):
        return binding.resolved_input_serializer
    return binding.spec.input_serializer


def advertises_closed_schema(binding: Any) -> bool:
    """Whether ``tools/list`` may stamp ``additionalProperties: false`` for ``binding``.

    ``REJECT`` is a silent no-op for a serializer-less binding —
    :func:`services_dispatch_policies` downgrades it and
    :func:`build_validated_input_serializer` short-circuits before the
    unknown-key check — so advertising a closed schema there would be a lie.
    """
    return (
        binding.unknown_arguments is UnknownArguments.REJECT
        and binding_input_serializer(binding) is not None
    )


def services_dispatch_policies(binding: Any) -> tuple[ArgumentBinding, UnknownArguments]:
    """The ``(argument_binding, unknown_arguments)`` to pass ``dispatch_spec``.

    The binding value passes straight through; only ``unknown_arguments`` is
    refined. A **selector** is already validated by the MCP layer against its own
    ``inputSchema``, which is wider than the selector signature (filter /
    ordering / pagination), so the neutral core must not re-reject: always
    ``IGNORE``. A **service with no ``input_serializer``** has an empty declared
    set, so rejecting against it is never right — ``PASSTHROUGH`` under the
    ``SPREAD_*`` bindings (raw args still reach the callable), ``IGNORE`` under
    ``BUNDLE``. Otherwise the binding's own value carries over.
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


def permission_verdict(perm: Any, result: Any, *, method: str, effect: str) -> Any:
    """A permission hook's answer, refusing one that must be awaited.

    Shared by :func:`check_permissions` and
    :func:`rest_framework_mcp.handlers.is_binding_listable.is_binding_listable`
    so the two places a consumer-supplied permission is consulted cannot drift on
    whether ``async def`` is allowed. It is not.

    **Without the guard this fails open on both transports.** Nothing awaits
    these hooks: the async path reaches them through ``acall``, which bridges
    this sync function, leaving an ``async def has_permission`` as un-awaited as
    on WSGI — and a coroutine is truthy, so the caller is granted. A permission
    that must await wraps the work in :func:`asgiref.sync.async_to_sync`.
    """
    return reject_awaitable(
        result,
        call=f"{type(perm).__name__}.{method}()",
        remedy=(
            f"MCP permissions are synchronous by contract on both transports: {method} "
            "must be a plain 'def'. Wrap any awaiting it needs in "
            "asgiref.sync.async_to_sync inside the method body."
        ),
        hazard=f"an un-awaited coroutine is truthy, so {effect}",
    )


def check_permissions(
    permissions: tuple[Any, ...],
    http_request: HttpRequest,
    token: TokenInfo,
) -> tuple[bool, list[str]]:
    """Return ``(allowed, required_scopes)`` after evaluating every permission.

    Permissions are AND-combined. The aggregated ``required_scopes`` from any
    permission that would deny is returned so the transport can surface them in
    the ``WWW-Authenticate`` header.
    """
    required: list[str] = []
    allowed: bool = True
    for perm in permissions:
        # Do not gate this loop on ``isinstance(perm, MCPPermission)``: the
        # Protocol is ``runtime_checkable``, so that demands *every* member
        # including ``required_scopes``, and a gate-only permission would be
        # honoured by the duck-typing ``is_binding_listable`` but skipped here —
        # vanishing from listings while the call goes through.
        verdict = permission_verdict(
            perm,
            perm.has_permission(http_request, token),
            method="has_permission",
            effect="every caller would be granted access.",
        )
        if not verdict:
            allowed = False
            scopes = getattr(perm, "required_scopes", None)
            if callable(scopes):
                required.extend(scopes())
    return allowed, required


def consume_rate_limits(
    rate_limits: tuple[Any, ...],
    http_request: HttpRequest,
    token: TokenInfo,
) -> int | None:
    """Run every rate limiter in order, returning the largest retry-after.

    Each limiter's ``consume`` updates its quota atomically and returns the
    suggested retry-after-seconds, or ``None`` to allow. The first denial stops
    the loop, so several limits per binding read as "deny if any is exhausted".

    An ``async def consume`` is refused rather than run: a coroutine is not
    ``None``, so it would deny every call with the coroutine object standing in
    for the retry-after seconds.
    """
    for limiter in rate_limits:
        if not isinstance(limiter, MCPRateLimit):  # defensive — caught at registration
            continue  # pragma: no cover
        retry_after: int | None = reject_awaitable(
            limiter.consume(http_request, token),
            call=f"{type(limiter).__name__}.consume()",
            remedy=(
                "MCP rate limiters are synchronous by contract: consume must be a plain "
                "'def'. Wrap any awaiting it needs in asgiref.sync.async_to_sync inside "
                "the method body."
            ),
            hazard=(
                "an un-awaited coroutine is not None, so every call would be denied with "
                "the coroutine object as its retryAfter."
            ),
        )
        if retry_after is not None:
            return retry_after
    return None


def effective_rate_limits(binding: Any, context: MCPCallContext) -> tuple[Any, ...]:
    """A tool binding's rate limiters, or none when this dispatch must not charge.

    Nothing is charged for a task worker replaying a call whose limits the client
    already consumed; see ``MCPCallContext.enforce_rate_limits``. Only the tool
    paths consult this — resources, prompts and completions have no task
    equivalent.
    """
    return binding.rate_limits if context.enforce_rate_limits else ()


def build_validated_input_serializer(
    arguments: dict[str, Any],
    input_serializer: type | None,
    *,
    unknown_arguments: UnknownArguments = UnknownArguments.REJECT,
    additional_known_keys: frozenset[str] = frozenset(),
    partial: bool = False,
    context: Mapping[str, Any] | None = None,
) -> tuple[Any, drf_serializers.Serializer | None]:
    """Validate ``arguments``; return ``(validated, bound_serializer)``.

    The validator for the read-shaped paths (selector tools and chain steps),
    where the input is a flat, instance-free arg map. Service-tool validation
    flows through drf-services' ``dispatch_spec`` instead.

    Args:
        arguments: The raw arg map off the wire.
        input_serializer: A DRF serializer class, a bare ``@dataclass`` (wrapped
            in a ``DataclassSerializer``), or ``None``.
        unknown_arguments: What to do with keys outside the declared set.
            Reserved pool seeds and post-fetch keys are always exempt, and never
            merged under ``PASSTHROUGH`` — the dispatch pipeline owns them, so a
            client must not be able to poison them.
        additional_known_keys: Widens the known set beyond the serializer's own
            fields; selector tools pass their filter / ordering / pagination keys.
        partial: Relaxes required-field validation. MCP has no HTTP method to
            derive partiality from, so the read paths default to full validation.
        context: Serializer context — callers pass ``base_serializer_context`` so
            a validator reading ``self.context["request"]`` behaves as it does
            behind a DRF view.

    Returns:
        The dataclass instance for a ``DataclassSerializer``, the
        ``validated_data`` dict for a plain ``Serializer``, or ``None`` for no
        serializer; paired with the bound serializer.

    Raises:
        drf_serializers.ValidationError: On invalid or, under ``REJECT``,
            unknown input.
    """
    if input_serializer is None:
        return None, None
    target: type = input_serializer
    if dataclasses.is_dataclass(target) and not isinstance(target, type):  # pragma: no cover
        raise TypeError("input_serializer must be a class")
    serializer_kwargs: dict[str, Any] = {"data": arguments, "partial": partial}
    if context is not None:
        serializer_kwargs["context"] = dict(context)
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

    # A ``DataclassSerializer`` returns a dataclass instance, which is not a
    # merge target, so those bindings get IGNORE-equivalent behaviour even under
    # PASSTHROUGH.
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
    context: Mapping[str, Any] | None = None,
) -> Any:
    """Validate ``arguments`` against ``input_serializer``; return ``validated`` only.

    Thin wrapper over :func:`build_validated_input_serializer` (see there for the
    full semantics) for callers that don't need the bound serializer.
    """
    validated, _serializer = build_validated_input_serializer(
        arguments,
        input_serializer,
        unknown_arguments=unknown_arguments,
        additional_known_keys=additional_known_keys,
        context=context,
    )
    return validated


def validation_error_data(detail: Any, value: Any, *, include_value: bool) -> dict[str, Any]:
    """Build the ``data`` payload for a JSON-RPC validation error.

    Always carries the per-field ``detail`` shape DRF produces.
    ``include_value`` (the server's ``MCPConfig.include_validation_value``) also
    echoes ``value`` back; off by default because it may carry PII or secrets
    that must not flow back to the client or into client-side logs.
    """
    payload: dict[str, Any] = {"detail": detail}
    if include_value:
        payload["value"] = value
    return payload


def resolve_bound(override: Any, default: Any) -> Any:
    """Resolve a per-binding outbound bound against the server's default.

    ``UNSET`` means the binding said nothing; any other value — including
    ``None``, meaning *no ceiling* — is the binding's deliberate answer and wins.
    All three bounds need this shape because a ``None``-is-default idiom would
    make "no ceiling for this one tool" inexpressible.
    """
    return default if isinstance(override, UnsetType) else override


def resource_not_found_code(protocol_version: str) -> JsonRpcErrorCode:
    """Which code a missing ``resources/read`` target gets, by era.

    The one place the two eras disagree on a wire value. ``2025-11-25`` names
    ``-32002`` for "Resource not found"; ``2026-07-28`` retired it for
    ``-32602`` while telling clients to keep *recognising* the old one, so
    neither value is safe to emit to both.
    """
    if protocol_version in MODERN_PROTOCOL_VERSIONS:
        return JsonRpcErrorCode.INVALID_PARAMS
    return JsonRpcErrorCode.RESOURCE_NOT_FOUND


def catalog_cache_hints(*, ttl_ms: int, filtered_by_permissions: bool) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a catalog result.

    Covers ``server/discover`` and the four list methods. ``cacheScope`` is
    derived from ``FILTER_LISTINGS_BY_PERMISSIONS``, not configured: with
    filtering on a listing is a function of the caller's permissions, so
    ``public`` would licence a shared proxy to serve one tenant's visible tools
    to another.
    """
    scope = CacheScope.PRIVATE if filtered_by_permissions else CacheScope.PUBLIC
    return {"ttlMs": ttl_ms, "cacheScope": scope.value}


def resource_cache_hints(ttl_ms: int) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a ``resources/read`` result.

    Always ``private``: the body is whatever the binding's selector produced for
    *this* caller, so sharing it across authorization contexts is never correct.
    The TTL is the only knob.
    """
    return {"ttlMs": ttl_ms, "cacheScope": CacheScope.PRIVATE.value}


def enforce_result_ceiling(result: Any, *, max_result_bytes: int | None, label: str) -> Any:
    """Replace an over-ceiling tool result with an ``isError`` result.

    Applied once per handler, to the finished result, so every dispatch path is
    covered by one check that sees what goes on the wire. A :class:`JsonRpcError`
    passes through — rewriting it would change the envelope the client awaits.
    """
    if isinstance(result, JsonRpcError):
        return result
    message: str | None = enforce_result_bytes(result, max_result_bytes, label=label)
    if message is None:
        return result
    return build_error_tool_result(message, error_type="result_too_large").to_dict()


async def run_with_deadline(coro: Awaitable[Any], seconds: float | None) -> Any:
    """Await ``coro``, raising :class:`asyncio.TimeoutError` past ``seconds``.

    ``None`` awaits without a deadline, so callers can hand the resolved bound
    straight in.

    **This does not stop the work.** ``wait_for`` cancels the *task*, and a task
    parked in ``sync_to_async`` — where every ORM-backed spec spends its time —
    waits on a thread asyncio cannot interrupt. The deadline buys the client a
    terminal response, not a stopped query; pair it with a database statement
    timeout. ``asyncio.wait_for`` rather than ``asyncio.timeout``, which is 3.11+.
    """
    if seconds is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=seconds)


__all__ = [
    "advertises_closed_schema",
    "binding_input_serializer",
    "build_validated_input_serializer",
    "check_permissions",
    "consume_rate_limits",
    "effective_rate_limits",
    "enforce_result_ceiling",
    "permission_verdict",
    "resolve_bound",
    "run_with_deadline",
    "services_dispatch_policies",
    "split_query_params",
    "split_url_kwargs",
    "validate_input_against_serializer",
    "validation_error_data",
]
