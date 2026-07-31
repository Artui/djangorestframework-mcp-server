from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Any

from rest_framework_services import build_offline_context, resolve_callable_kwargs

from rest_framework_mcp._compat.tracing import span
from rest_framework_mcp.constants import MAX_COMPLETION_VALUES, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import _span_attrs
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import check_permissions, consume_rate_limits
from rest_framework_mcp.protocol.types.completion import Completion
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


def handle_completion_complete(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Suggest values for one argument of a prompt or resource template.

    This is the interactive-typing surface: a client shows a dropdown as the
    user fills in a prompt argument or a URI-template variable, and asks here
    for candidates. Completers are registered per binding, keyed by the
    argument they complete, and are dispatched through
    :func:`resolve_callable_kwargs` — so a completer declares whichever of
    ``value`` / ``arguments`` / ``request`` / ``user`` it needs (plus any
    already-resolved sibling argument by name) and gets nothing else.

    ⚠ **A completion is a read of the thing being completed**, so it runs the
    binding's permission and rate-limit stacks before the completer. Without
    that, a resource a caller may not read would still answer "which ids
    exist?" one keystroke at a time — a slower version of the same disclosure.
    The spec asks for the rate limit explicitly; the permission check is the
    same rule ``resources/read`` and ``prompts/get`` already apply.

    ⚠ **Completers are sync callables.** Under ASGI this handler runs in
    Django's thread-sensitive executor (see :func:`adispatch`), so ORM access
    inside a completer is fine — but returning a coroutine is not, because
    nothing awaits it.

    Errors are all ``-32602``, per the completion spec: an unknown prompt or
    template, an argument with no completer, or a malformed ``ref``.
    """
    if not isinstance(params, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "completion/complete params must be an object"
        )

    argument: Any = params.get("argument")
    if not isinstance(argument, dict) or not isinstance(argument.get("name"), str):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            "'argument' is required and must be an object with a string 'name'",
        )
    argument_name: str = argument["name"]
    # The spec types ``value`` as the text typed so far, which is legitimately
    # empty when the field has just been focused.
    value: Any = argument.get("value", "")
    if not isinstance(value, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'argument.value' must be a string")

    target = _resolve_ref(params.get("ref"), context)
    if isinstance(target, JsonRpcError):
        return target

    completer: Callable[..., Any] | None = target.completions.get(argument_name)
    if completer is None:
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS,
            f"{target.label} has no completer for argument {argument_name!r}",
            data={"completable": sorted(target.completions)},
        )

    context_raw: Any = params.get("context") or {}
    siblings: Any = context_raw.get("arguments") if isinstance(context_raw, dict) else None
    resolved_siblings: dict[str, Any] = siblings if isinstance(siblings, dict) else {}

    attributes: dict[str, Any] = {
        **_span_attrs(target.name, context),
        "mcp.completion.kind": target.kind,
        "mcp.completion.argument": argument_name,
    }
    with span("mcp.completion.complete", attributes=attributes):
        allowed, required_scopes = check_permissions(
            target.permissions, context.http_request, context.token
        )
        if not allowed:
            return JsonRpcError(
                JsonRpcErrorCode.FORBIDDEN,
                "Insufficient permission",
                data={"requiredScopes": required_scopes} if required_scopes else None,
            )

        retry_after: int | None = consume_rate_limits(
            target.rate_limits, context.http_request, context.token
        )
        if retry_after is not None:
            return JsonRpcError(
                JsonRpcErrorCode.RATE_LIMITED,
                "Rate limit exceeded",
                data={"retryAfter": retry_after},
            )

        drf_request = build_offline_context(
            context.token.user,
            None,
            http_request=context.http_request,
            # Same reasoning as ``prompts/get``: nothing here reads the query
            # string, and passing an empty mapping guarantees the endpoint's
            # own can never leak into a completer if that ever changes.
            query_params={},
        ).request
        # The transport's seeds are applied *after* the client-supplied
        # siblings, so a sibling argument named ``user`` or ``value`` shadows
        # nothing — the same precedence ``RESERVED_POOL_SEEDS`` enforces on the
        # dispatch path.
        pool: dict[str, Any] = {
            **resolved_siblings,
            "request": drf_request,
            "user": context.token.user,
            "value": value,
            "arguments": resolved_siblings,
        }
        raw: Any = completer(**resolve_callable_kwargs(completer, pool))
        return {"completion": _to_completion(raw).to_dict()}


@dataclass(frozen=True)
class _CompletionTarget:
    """The slice of a prompt or resource binding a completion request needs.

    A record rather than a tuple because the handler reads four unrelated
    things off it and the two binding types have no common base — flattening
    them here is what keeps the rest of the handler from branching on which
    kind it got.
    """

    kind: str
    """``prompt`` or ``resource`` — used to label errors and the span."""

    name: str
    permissions: tuple[Any, ...]
    rate_limits: tuple[Any, ...]
    completions: dict[str, Callable[..., Any]]

    @property
    def label(self) -> str:
        return f"{self.kind.capitalize()} {self.name!r}"


def _resolve_ref(ref: Any, context: MCPCallContext) -> _CompletionTarget | JsonRpcError:
    """Look up the prompt or resource a ``ref`` names."""
    if not isinstance(ref, dict):
        return JsonRpcError(
            JsonRpcErrorCode.INVALID_PARAMS, "'ref' is required and must be an object"
        )
    ref_type: Any = ref.get("type")

    if ref_type == "ref/prompt":
        name: Any = ref.get("name")
        if not isinstance(name, str):
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS, "ref/prompt requires a string 'name'"
            )
        prompt = context.prompts.get(name)
        if prompt is None:
            return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown prompt: {name!r}")
        return _CompletionTarget(
            kind="prompt",
            name=name,
            permissions=prompt.permissions,
            rate_limits=prompt.rate_limits,
            completions=prompt.completions,
        )

    if ref_type == "ref/resource":
        uri: Any = ref.get("uri")
        if not isinstance(uri, str):
            return JsonRpcError(
                JsonRpcErrorCode.INVALID_PARAMS, "ref/resource requires a string 'uri'"
            )
        # The spec's example passes the *template* (``file:///{path}``), which
        # is what a client has in hand while the user is still typing the
        # variable. Matched exactly first, because a template string also
        # happens to satisfy its own pattern — ``file:///{path}`` would
        # otherwise "resolve" with ``path="{path}"``.
        resource = context.resources.by_uri_template(uri)
        if resource is None:
            return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, f"Unknown resource: {uri!r}")
        return _CompletionTarget(
            kind="resource",
            name=resource.name,
            permissions=resource.permissions,
            rate_limits=resource.rate_limits,
            completions=resource.completions,
        )

    return JsonRpcError(
        JsonRpcErrorCode.INVALID_PARAMS,
        f"Unsupported completion ref type: {ref_type!r}. Expected 'ref/prompt' or 'ref/resource'.",
    )


def _to_completion(raw: Any) -> Completion:
    """Cap a completer's output at the spec's limit without draining it.

    One value past the cap is pulled to decide ``hasMore`` and then discarded,
    so a completer returning a queryset or a generator is sliced rather than
    materialised — the difference between reading 101 rows and reading the
    table.
    """
    values: list[str] = [str(item) for item in islice(_iterable(raw), MAX_COMPLETION_VALUES + 1)]
    has_more: bool = len(values) > MAX_COMPLETION_VALUES
    return Completion(values=tuple(values[:MAX_COMPLETION_VALUES]), has_more=has_more)


def _iterable(raw: Any) -> Iterable[Any]:
    """A lone string is one suggestion, not a sequence of characters."""
    return [raw] if isinstance(raw, str) else raw


__all__ = ["handle_completion_complete"]
