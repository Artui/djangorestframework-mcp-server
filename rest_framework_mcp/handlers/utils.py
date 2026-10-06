from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from typing import Any

from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import UnsetType

# Not a top-level export of the sister package, so this reaches past its stable
# dispatch surface on purpose. It is the single implementation of "can this
# spec's declared key set be enumerated?", which is exactly the question
# ``advertises_closed_schema`` has to answer, and a local copy of that logic is
# what would let the advertisement drift away from the enforcement again. A
# rename upstream breaks this import loudly at start-up rather than quietly at
# the wire, which is the failure mode to prefer here.
from rest_framework_services.dispatch.utils import declared_input_keys
from rest_framework_services.exceptions.action_unavailable import ActionUnavailable
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import (
    ServiceValidationError,
)
from rest_framework_services.types.pool_seeds import PoolSeeds
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp._compat.reject_awaitable import reject_awaitable
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.auth.rate_limits.types.mcp_rate_limit import MCPRateLimit
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import (
    MODERN_PROTOCOL_VERSIONS,
    RESERVED_POOL_SEEDS,
    RESERVED_POST_FETCH_KEYS,
    ArgumentBinding,
    CacheScope,
    JsonRpcErrorCode,
    OutputFormat,
    UnknownArguments,
)
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.output.enforce_result_bytes import enforce_result_bytes
from rest_framework_mcp.output.error_tool_result import build_error_tool_result
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.tool_result import ToolResult
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.query_param import QueryParam
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.registry.types.url_kwarg import UrlKwarg
from rest_framework_mcp.schema.types.agent_conventions import AgentConventions
from rest_framework_mcp.schema.utils import declares_default, end_sentence, required_arguments

_SPREAD_BINDINGS = frozenset(
    {ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS}
)


def split_url_kwargs(
    arguments: dict[str, Any],
    url_kwargs: tuple[UrlKwarg, ...],
    *,
    refuse_missing: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split ``arguments`` into ``(params, url_kwarg_values)``.

    Each declared kwarg takes the model's value, else its ``default``, else stays
    absent; the name is removed from ``params`` so the value routes only through
    ``view.kwargs`` (authoritative over params) and never also reaches the spec
    as an ordinary input. Non-mutating.

    A ``required=True`` kwarg the model omitted is refused here rather than
    failing further down: ``required`` in the schema is only a hint, and
    registration forbids pairing it with a ``default``. The refusal is the one
    ``refuse_missing_arguments`` raises for a selector parameter, DRF's
    ``ValidationError`` keyed by the argument, so a client reads one shape for
    a missing argument whichever way it was declared
    (``test_a_missed_required_url_kwarg_is_answered_in_the_same_shape``).
    ``refuse_missing=False`` returns what the call delivered instead, for a
    caller that has to answer something else first (``call_spec_tool``, whose
    permission check reads the context built from it).

    **An explicit ``null`` is not a supplied value.** A URL kwarg stands in for
    a route capture, and a route capture can never be null: over HTTP the
    segment either matched or the URL did not resolve. Off-HTTP a model that
    emits ``{"pk": null}`` is saying it has no value, so the kwarg falls through
    to its ``default`` and then to the ``required`` check, exactly as an omitted
    key does. Routing the ``None`` on instead would satisfy ``required=True``
    with nothing, and would reach the ORM as ``IS NULL`` — an unscoped read that
    answers successfully with the wrong rows. ``split_query_params`` applies the
    same rule for a different reason, which its own docstring gives.
    """
    if not url_kwargs:
        return arguments, {}
    names = {uk.name for uk in url_kwargs}
    values: dict[str, Any] = {}
    missing: list[str] = []
    for url_kwarg in url_kwargs:
        supplied: Any = arguments.get(url_kwarg.name)
        if supplied is not None:
            values[url_kwarg.name] = supplied
        elif declares_default(url_kwarg.default):
            values[url_kwarg.name] = url_kwarg.default
        elif url_kwarg.required:
            missing.append(url_kwarg.name)
    if missing and refuse_missing:
        raise _missing_arguments_error(sorted(missing))
    params = {key: value for key, value in arguments.items() if key not in names}
    return params, values


def split_query_params(
    arguments: dict[str, Any], query_params: tuple[QueryParam, ...]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split ``arguments`` into ``(params, query_param_values)``.

    The sibling of ``split_url_kwargs``, minus the ``required`` flag a
    [`QueryParam`][rest_framework_services.types.query_param.QueryParam] does not carry.
    Popping the name also keeps a query param out of ``unknown_arguments``.

    A ``filter_set`` field is **not** a query param. Filter fields are already in
    the tool schema and flow through as ordinary ``params``, which is where
    ``dispatch_spec`` reads them (as ``filter_data``); declaring one here pops it
    out of the args and it silently stops filtering.

    **An explicit ``null`` is not a supplied value**, which is the contract
    ``QueryParam`` itself states for every transport: over HTTP a query param is
    always a string, so nothing a caller sends there means null, and off-HTTP
    ``{"fields": null}`` is how a model says it chose not to fill an optional
    param. So a null falls through to the ``default`` and otherwise produces no
    value, exactly as an omitted key does. Routing it on is not harmless:
    ``build_offline_context`` stringifies every value as HTTP would, so the
    serializer read the four characters ``None`` — which a selection parser
    refuses as malformed (django-restql does), failing the render of a call whose
    caller asked for nothing. The name is still popped from ``params`` either way.
    """
    if not query_params:
        return arguments, {}
    names = {qp.name for qp in query_params}
    values: dict[str, Any] = {}
    for query_param in query_params:
        supplied: Any = arguments.get(query_param.name)
        if supplied is not None:
            values[query_param.name] = supplied
        elif declares_default(query_param.default):
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

    A ``many=True`` service spec is closed whatever its policy. Its list travels
    under ``spec.many_argument``, and drf-services refuses any argument beside it
    under every ``unknown_arguments`` value; URL kwargs and query params are split
    out before dispatch and advertised as properties of their own. The policy
    governs the keys inside each item there, which ``advertises_closed_items``
    answers. Everything below describes the arguments of every other binding.

    ``REJECT`` is a silent no-op for a serializer-less binding —
    ``services_dispatch_policies`` downgrades it and
    ``build_validated_input_serializer`` short-circuits before the
    unknown-key check — so advertising a closed schema there would be a lie.

    A **service** tool needs one further condition. Its unknown-argument check
    is not run here but by the sister package, against the key set the spec
    declares; that set is not always enumerable — the one lookup dispatch
    calls (``collection_selector_spec`` when declared, else
    ``instance_selector_spec``, and neither on ``many=True``) leaves it open
    when it takes a bare ``**kwargs`` or carries a ``filter_set`` — and an
    open set is answered by accepting and silently dropping every undeclared
    key. Where nothing is enforced, nothing closed may be advertised. A lookup
    dispatch never calls cannot open it
    (``test_an_open_lookup_dispatch_never_calls_leaves_the_schema_closed``).
    """
    if takes_list_payload(binding):
        return True
    return _enforces_unknown_keys(binding)


def advertises_closed_items(binding: Any) -> bool:
    """Whether each item of a ``many=True`` service tool's list may be advertised closed.

    drf-services checks ``unknown_arguments`` against every item as it checks a
    single-item call's arguments, against the same child serializer and the same
    declared key set, so the item gets the answer a single-item spec would.
    """
    return _enforces_unknown_keys(binding)


def takes_list_payload(binding: Any) -> bool:
    """Whether ``binding`` is a service tool whose spec validates a list (``many=True``).

    Selector and chain bindings never do: a chain dispatches its steps itself, and
    a selector spec has no ``many``.
    """
    spec: Any = getattr(binding, "spec", None)
    return isinstance(spec, ServiceSpec) and spec.many


def _enforces_unknown_keys(binding: Any) -> bool:
    if binding.unknown_arguments is not UnknownArguments.REJECT:
        return False
    if binding_input_serializer(binding) is None:
        return False
    spec: Any = getattr(binding, "spec", None)
    if not isinstance(spec, ServiceSpec):
        # Selector and chain bindings enforce the closed set in this package,
        # via ``build_validated_input_serializer``, so the guarantee holds.
        return True
    # Asked of the sister package rather than recomputed here: this is the
    # exact predicate its dispatch consults, and a second implementation of it
    # would drift into advertising what the runtime stopped enforcing. The
    # serializer only ever *adds* declared names, so it cannot change whether
    # the set is enumerable and is not needed for the question.
    return declared_input_keys(spec, serializer=None) is not None


def validate_output_format(params: dict[str, Any]) -> JsonRpcError | None:
    """Reject a client-supplied ``outputFormat`` naming a format this server cannot render.

    ``outputFormat`` is a client-facing knob, so an unrecognised value is a bad
    parameter and belongs in a ``-32602`` envelope. Left unchecked it reaches
    ``OutputFormat.coerce`` at the *end* of dispatch, where the ``ValueError``
    it raises is mapped by nothing and surfaces as a bare HTTP 500 — after the
    tool has already run and any mutation has committed, so the caller cannot
    tell whether retrying would apply the write twice.

    Called once at the entry of each ``tools/call`` handler, ahead of task
    creation and dispatch, which is what makes the coercions further down the
    three dispatch paths safe. ``None`` means "nothing to object to"; a falsy
    value is passed over because the dispatch paths read the knob as
    ``params.get("outputFormat") or binding.output_format``, i.e. treat it as
    unsupplied.
    """
    requested: Any = params.get("outputFormat")
    if not requested:
        return None
    try:
        OutputFormat.coerce(requested)
    except ValueError:
        supported: str = ", ".join(repr(member.value) for member in OutputFormat)
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"'outputFormat' must be one of {supported}; got {requested!r}",
        )
    return None


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

    Shared by ``check_permissions`` and
    ``rest_framework_mcp.handlers.is_binding_listable.is_binding_listable``
    so the two places a consumer-supplied permission is consulted cannot drift on
    whether ``async def`` is allowed. It is not.

    **Without the guard this fails open on both transports.** Nothing awaits
    these hooks: the async path reaches them through ``acall``, which bridges
    this sync function, leaving an ``async def has_permission`` as un-awaited as
    on WSGI — and a coroutine is truthy, so the caller is granted. A permission
    that must await wraps the work in ``asgiref.sync.async_to_sync``.
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
    *,
    view_kwargs: Mapping[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Return ``(allowed, required_scopes)`` after evaluating every permission.

    Permissions are AND-combined. The aggregated ``required_scopes`` from any
    permission that would deny is returned so the transport can surface them in
    the ``WWW-Authenticate`` header.

    **``view_kwargs`` is the route the request names**, and every
    [`DRFPermissionAdapter`][rest_framework_mcp.auth.permissions.drf_permission_adapter.DRFPermissionAdapter]
    among ``permissions`` is judged against a copy whose stand-in view carries
    it: the URL kwargs a ``tools/call`` delivered, or the variables of the URI a
    ``resources/read`` names, the values the dispatch then puts in
    ``view.kwargs``. A spec permission scoping by a route capture reads
    ``view.kwargs["project_pk"]``, as it would over HTTP, and judged against
    ``{}`` it denied a caller it admits. Any other permission is judged as it
    is, since an ``MCPPermission`` judges the request and token and has no view.
    ``None`` judges every permission as registered, for the paths that name no
    route: ``prompts/get``, ``completion/complete`` and chain steps.

    The registered adapters are never written to, because every concurrent
    call to the binding shares them, and the wrapped DRF permission is not
    instantiated again (``test_the_registered_adapter_is_left_unbound``,
    ``test_the_permission_is_not_instantiated_again_nor_a_subclass_state_dropped``).
    """
    required: list[str] = []
    allowed: bool = True
    for registered in permissions:
        perm: Any = registered
        # Both conjuncts hold a test: without the ``None`` check every adapter
        # on a path naming no route is bound to ``None``
        # (``test_without_view_kwargs_every_adapter_is_judged_on_an_empty_route``),
        # and without the ``isinstance`` an ``MCPPermission``, which has no view,
        # is handed one (``test_view_kwargs_reach_every_adapter_and_pass_the_rest_through``).
        if view_kwargs is not None and isinstance(registered, DRFPermissionAdapter):
            # The adapter's private hook, and this is its one caller: binding a
            # route is how a check is made, not something a consumer composes.
            perm = registered._bound_to(view_kwargs)  # noqa: SLF001
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

    Thin wrapper over ``build_validated_input_serializer`` (see there for the
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
    """Build the ``detail`` / ``value`` pair a validation failure reports.

    Merged into the ``error`` object of a ``validation_error`` tool result (see
    ``validation_error_result``). Always carries the per-field ``detail`` shape
    DRF produces. ``include_value`` (the server's
    ``MCPConfig.include_validation_value``) also echoes ``value`` back; off by
    default because it may carry PII or secrets that must not flow back to the
    client or into client-side logs.
    """
    payload: dict[str, Any] = {"detail": detail}
    if include_value:
        payload["value"] = value
    return payload


def refuse_missing_arguments(
    binding: SelectorToolBinding | ToolBinding,
    present: Iterable[str],
    *,
    pool_seeds: PoolSeeds,
    input_serializer_runs: bool = True,
) -> None:
    """Refuse a call that leaves out an argument its selectors cannot run without.

    A selector -- a selector tool's own, or the lookup a service tool resolves
    its target through -- called without a parameter it has no default for
    raises ``TypeError``, which no handler maps, so the call used to answer
    HTTP 500 and JSON-RPC ``-32603`` on the wire and raise from ``call_tool``.
    The names checked are the ones the tool's ``inputSchema`` requires of its
    selectors (``schema.utils.required_arguments``), read from the same
    reflection, so the rule enforced is the rule the client was told, and a name
    the server fills is never among them.

    Raised as DRF's ``ValidationError`` keyed by field with DRF's own
    ``required`` message and code, the detail an input serializer gives a
    missing field, so every caller's existing ``ValidationError`` arm catches
    it; ``validation_error_result`` reads it as this check's refusal and words
    it with the server's ``missing_arguments``. ``present`` is every name the
    call delivers to the selector: the arguments left after the channel splits
    plus the ``UrlKwarg`` values, which reach the pool through ``view.kwargs``.
    ``input_serializer_runs=False`` is for ``call_tool``, which does not run a
    selector tool's ``input_serializer``, so its defaults deliver nothing there
    (``schema.utils.required_arguments``).

    Called after the transport-level permissions on every route, so a caller
    ``tools/list`` hides the tool from is refused for the permission before it
    could learn from this answer that the tool exists
    (``test_a_denied_caller_is_refused_before_the_argument_is_checked``).
    """
    delivered = frozenset(present)
    required = required_arguments(
        binding, pool_seeds=pool_seeds, input_serializer_runs=input_serializer_runs
    )
    missing = [name for name in required if name not in delivered]
    if missing:
        raise _missing_arguments_error(missing)


def _missing_arguments_error(names: list[str]) -> drf_serializers.ValidationError:
    """The refusal of a call that left ``names`` out.

    Both checks that ask for an argument before anything runs raise this: the
    selectors' (``refuse_missing_arguments``) and a ``UrlKwarg(required=True)``'s
    (``split_url_kwargs``). Those are the two the Pydantic-AI toolset words as
    ``Missing required argument(s): ...``, so both are answered that way here.
    """
    return _MissingArguments(names)


class _MissingArguments(drf_serializers.ValidationError):
    """DRF's ``ValidationError`` for arguments a call left out, marked as such.

    The detail is DRF's own ``required`` message and code keyed by each name,
    which is what an input serializer gives a missing field: a client reading
    the detail reads one shape for both. A subclass because the detail cannot
    tell them apart, and the message must: only this refusal gets the server's
    ``missing_arguments``, while a serializer's own missing field keeps
    ``"Invalid arguments"``
    (``test_a_service_tools_serializer_keeps_the_generic_message_for_its_own_field``).
    Every ``except ValidationError`` arm still catches it unchanged.
    """

    def __init__(self, names: Sequence[str]) -> None:
        message = drf_serializers.Field.default_error_messages["required"]
        super().__init__({name: [message] for name in names}, code="required")
        self.names: tuple[str, ...] = tuple(names)


def render_convention(template: str | None) -> str | None:
    """One placeholder-free ``AgentConventions`` field as the model reads it.

    Every field is a ``str.format`` template, rendered whether or not it has a
    placeholder, so a doubled brace is one brace in all of them alike -- the
    Pydantic-AI toolset's rule -- rather than reaching a model doubled from the
    three fields that take no placeholder
    (``test_a_doubled_brace_reaches_the_model_as_one_in_every_field``). ``None``
    is a dropped sentence and stays ``None``. The template was validated against
    this exact call when the conventions were built, so this cannot raise.
    ``missing_arguments``, the one field with a placeholder, is rendered where
    its names are known, in ``validation_error_result``.
    """
    return None if template is None else template.format()


def validation_error_result(
    exc: drf_serializers.ValidationError | ServiceValidationError,
    arguments: Any,
    *,
    config: MCPConfig,
    conventions: AgentConventions,
) -> ToolResult:
    """The ``isError`` result for a tool call whose arguments were refused.

    One answer for every way a call's input can be refused before or while it
    is dispatched: DRF's ``ValidationError`` (an unexpected argument under
    ``UnknownArguments.REJECT``, an ``input_serializer`` rejection, a value a
    spec's ``FilterSet`` refuses) and drf-services' ``ServiceValidationError``
    (a service's own validation, a missing ``InputRequired`` argument a
    ``kwargs=`` provider might have filled). The MCP spec's tools "Error
    Handling" section files "input validation errors (e.g., date in wrong
    format, value out of range)" under tool execution errors, reported with ``isError: true`` so the
    model can read them and correct its call; a JSON-RPC protocol error is for
    an unknown tool or a request that fails the ``CallToolRequest`` schema. The
    same rule holds in every served protocol version: 2025-06-18 lists "invalid
    input data" under tool execution errors too.

    The ``error`` object carries ``type: "validation_error"``, the message, and
    what ``validation_error_data`` builds: ``detail`` keyed by field as DRF
    produces it, and ``value`` only under ``INCLUDE_VALIDATION_VALUE``. Those are
    the names the ``-32602`` envelope's ``data`` carried, so a client reading
    the detail finds it under the same key one level down. A DRF error has no
    message of its own, so it keeps the one that envelope had, with one
    exception: arguments a call left out before anything ran (a selector
    parameter with no default, a service tool's target lookup, a
    ``UrlKwarg(required=True)``) are named in the server's
    ``conventions.missing_arguments``, ``Missing required argument(s): `pk`.``
    by default. Only that refusal: an input serializer's, a missing field
    included, is indistinguishable by its detail and keeps ``"Invalid
    arguments"``, because the marker is the exception's type, not its codes.

    Every argument-validation arm on the ``tools/call`` paths builds its result
    here -- the service tool handlers sync and async, the in-process
    ``call_tool``, both selector tool siblings, and a chain's input -- so a
    refused argument answers the same way whichever path served it. A failing
    chain *step* adds ``failedStep`` and builds its own, and a refusal while
    rendering goes through ``read_shaping_error_result``.
    """
    message: str
    if isinstance(exc, _MissingArguments):
        # Sorted, each in backticks, joined with ", ": the Pydantic-AI toolset's
        # format, so one omission reads the same on both transports
        # (``test_several_missing_names_are_sorted_and_joined``).
        names = ", ".join(f"`{name}`" for name in sorted(exc.names))
        message = conventions.missing_arguments.format(names=names)
    elif isinstance(exc, ServiceValidationError):
        message = exc.message
    else:
        message = "Invalid arguments"
    return build_error_tool_result(
        message,
        error_type="validation_error",
        detail=validation_error_data(
            exc.detail, arguments, include_value=config.include_validation_value
        ),
    )


def service_error_result(
    exc: ServiceError, *, detail: Mapping[str, Any] | None = None
) -> ToolResult:
    """The ``isError`` tool result for a ``ServiceError`` a dispatch raised.

    ``type`` stays ``"service_error"`` for every member of the family, because
    that is what an existing client branches on. An
    [`ActionUnavailable`][rest_framework_services.exceptions.action_unavailable.ActionUnavailable]
    -- a declared affordance refusing the call -- also carries its ``code``: the
    message is the affordance's ``reason``, a sentence that gets reworded, and the
    code is the stable name a client switches on. drf-services asks a transport
    serving an agent to pass on both, and every arm once passed on only the
    sentence.

    The key is **absent**, not ``null``, for any other ``ServiceError``. A
    ``ServiceConflict`` raised by hand from a precondition has no code and never
    will, which is why drf-services made the code a subclass field rather than a
    nullable one on the parent; a ``null`` here would reintroduce the field that
    lies at every other call site.

    Every ``ServiceError`` arm builds its result here -- the service tool paths
    sync and async, the in-process ``call_tool``, both selector tool siblings and
    each chain step -- so a refusal answers the same way whichever path served
    it. ``detail`` is merged in as ``build_error_tool_result`` merges it, which is
    how a chain step adds ``failedStep`` beside the code.
    """
    error_detail: dict[str, Any] = dict(detail or {})
    if isinstance(exc, ActionUnavailable):
        error_detail["code"] = exc.code
    return build_error_tool_result(exc.message, error_type="service_error", detail=error_detail)


def read_shaping_error_result(
    exc: drf_serializers.ValidationError | ServiceValidationError,
    *,
    query_params: tuple[QueryParam, ...],
    arguments: Mapping[str, Any],
    paginated: bool,
    config: MCPConfig,
    conventions: AgentConventions,
) -> ToolResult:
    """The ``isError`` result for a validation error raised while *rendering*.

    A read-shaping ``QueryParam`` is the one caller input used while the output is
    rendered rather than while the spec is dispatched: a field selection, whichever
    library parses it, is applied by the output serializer one row at a time, long
    after ``dispatch_spec`` has returned. So a bad selection fails outside every
    ``except`` that decides whether a failure is the caller's to fix, and it escaped
    as whatever the transport made of an unhandled exception — a bare DRF body in
    JSON mode, a ``-32603`` in a stream, a raised ``ValidationError`` from
    ``acall_tool``. This turns it into the channel the dispatch path already uses
    for "your argument was wrong": ``validation_error``, built exactly as the
    ``ServiceValidationError`` arms build it, which an in-process toolset maps to a
    retry the model can act on.

    **Only when the caller shaped the render.** With no read-shaping value
    supplied on this call nothing the model sends can change the outcome, so the
    error is a server bug and is re-raised unchanged: a retry would spend the
    model's budget on it and hide it from the operator. "Supplied" is read off
    the raw ``arguments`` rather than the values ``split_query_params`` routed,
    because that split seeds a ``QueryParam.default`` for an omitted name — a
    value nobody sent, whose failure is a configuration bug and must stay loud.
    An explicit ``null`` is not supplied either, as ``QueryParam``'s own contract
    says a transport treats it. The split does not mutate its input, so every
    call site still holds the unpopped mapping to pass here.

    Validation errors only, never ``Exception``: an ``AttributeError`` in a
    serializer is a server bug whatever the caller sent, so call sites catch
    exactly DRF's ``ValidationError`` and ``ServiceValidationError``.

    The detail is keyed under the supplied name when there is one. With several
    it goes under ``non_field_errors`` and the message names them all, because
    nothing in the error says which one the serializer refused, and keying it
    under one would be a guess presented as a fact. On a paged tool the message
    also says what the param applies to, since selecting the page envelope —
    the shape the tool's ``outputSchema`` shows — is the likeliest way to get
    here. That sentence is the server's ``conventions.query_param_on_pages``,
    the one its ``tools/list`` appends to the param, and ``None`` drops it from
    both.
    """
    # ``is not None`` rather than ``in``: the null rule is its own condition, held
    # by ``test_an_explicit_null_is_not_supplied``, which fails with ``in``.
    supplied: list[str] = [
        query_param.name
        for query_param in query_params
        if arguments.get(query_param.name) is not None
    ]
    # One branch arc to coverage, so the gate cannot see it removed. Held by the
    # five tests in tests/handlers/test_render_time_query_params.py that assert
    # the original error still escapes -- the three
    # ``test_render_error_with_nothing_supplied_still_raises*`` (sync selector,
    # async service, ``call_tool``), ``test_a_seeded_default_is_not_supplied`` and
    # ``test_an_explicit_null_is_not_supplied`` -- each of which fails when this
    # guard is deleted.
    if not supplied:
        raise exc
    detail: Any = exc.detail
    key: str = supplied[0] if len(supplied) == 1 else "non_field_errors"
    message: str = (
        f"{_name_list(supplied)} was rejected while rendering the result: "
        f"{_readable_detail(detail)}"
    )
    # One branch arc for two conditions, so each is named by the test that fails
    # without it: ``test_an_unpaged_selector_tool_gets_no_page_sentence``
    # (``paginated``) and
    # ``test_none_drops_the_handle_wording_and_the_scope_sentence`` (the
    # ``None`` check, which would otherwise append the text "None").
    scope = render_convention(conventions.query_param_on_pages)
    if paginated and scope is not None:
        message = f"{message} {scope}"
    return build_error_tool_result(
        message,
        error_type="validation_error",
        detail=validation_error_data(
            # Normalised to DRF's per-field shape: a ``ServiceValidationError``
            # may carry a bare string, and a field's errors are always a list.
            {key: detail if isinstance(detail, (list, dict)) else [detail]},
            arguments,
            include_value=config.include_validation_value,
        ),
    )


def _name_list(names: list[str]) -> str:
    """``names`` as prose: "`a`", or "`a`, `b` or `c`" when there are several.

    "Or" rather than "and", because the error came from one render that read all
    of them, and which one it refused is not something the error says.
    """
    quoted: list[str] = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _readable_detail(detail: Any) -> str:
    """A validation ``detail`` as sentences a model reads, not as a ``repr``.

    DRF's detail is an ``ErrorDetail`` string, a list of them, or a mapping of
    field name to either; the ``repr`` of any of those is what an unhandled
    render error used to put in front of the model, ``ErrorDetail(string=...,
    code=...)`` included.
    """
    if isinstance(detail, dict):
        return " ".join(f"`{key}`: {_readable_detail(value)}" for key, value in detail.items())
    if isinstance(detail, list):
        return " ".join(_readable_detail(item) for item in detail)
    return end_sentence(str(detail))


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


def catalog_cache_hints(*, ttl_ms: int, per_caller: bool) -> dict[str, Any]:
    """``ttlMs`` / ``cacheScope`` for a catalog result.

    Covers ``server/discover`` and the four list methods. ``cacheScope`` is
    derived, not configured: ``per_caller`` says whether anything about this
    caller shaped the result. ``FILTER_LISTINGS_BY_PERMISSIONS`` does for every
    list, since a listing is then a function of the caller's permissions;
    ``tools/list`` also does whenever it asked an operation-scope affordance,
    which is answered against the caller's ``user`` and ``request``. Either way
    ``public`` would licence a shared proxy to serve one tenant's listing to
    another.
    """
    scope = CacheScope.PRIVATE if per_caller else CacheScope.PUBLIC
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

    Applied once per handler, to the finished result, so every dispatch path is covered
    by one check that sees what goes on the wire. A
    [`JsonRpcError`][rest_framework_mcp.protocol.types.json_rpc_error.JsonRpcError]
    passes through — rewriting it would change the envelope the client awaits."""
    if isinstance(result, JsonRpcError):
        return result
    message: str | None = enforce_result_bytes(result, max_result_bytes, label=label)
    if message is None:
        return result
    return build_error_tool_result(message, error_type="result_too_large").to_dict()


async def run_with_deadline(coro: Awaitable[Any], seconds: float | None) -> Any:
    """Await ``coro``, raising ``asyncio.TimeoutError`` past ``seconds``.

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
    "advertises_closed_items",
    "advertises_closed_schema",
    "binding_input_serializer",
    "build_validated_input_serializer",
    "check_permissions",
    "consume_rate_limits",
    "effective_rate_limits",
    "enforce_result_ceiling",
    "permission_verdict",
    "read_shaping_error_result",
    "refuse_missing_arguments",
    "render_convention",
    "resolve_bound",
    "run_with_deadline",
    "services_dispatch_policies",
    "split_query_params",
    "split_url_kwargs",
    "takes_list_payload",
    "validate_input_against_serializer",
    "validate_output_format",
    "validation_error_data",
    "validation_error_result",
]
