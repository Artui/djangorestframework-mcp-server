from __future__ import annotations

import dataclasses
import json
from typing import Any, cast

from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework_dataclasses.serializers import DataclassSerializer

from rest_framework_mcp.auth.permissions.mcp_permission import MCPPermission
from rest_framework_mcp.auth.rate_limits.mcp_rate_limit import MCPRateLimit
from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.conf import get_setting


def build_internal_drf_request(
    http_request: HttpRequest,
    *,
    user: Any,
    data: dict[str, Any] | None,
) -> Request:
    """Wrap a Django request as a DRF ``Request`` populated with MCP-supplied data.

    Services that declare ``request`` or ``user`` parameters expect a DRF
    ``Request`` with parsed ``.data`` and a ``.user``. We synthesise that
    here so callables written for the HTTP transport keep working when
    invoked via MCP.

    The HTTP method is forced to ``POST`` because mutation services often
    branch on it; this is internal and not surfaced to MCP clients.
    """
    http_request.method = "POST"
    body: bytes = json.dumps(data or {}).encode("utf-8")
    http_request._body = body  # type: ignore[attr-defined]
    http_request.META["CONTENT_TYPE"] = "application/json"
    # The django-stubs ``HttpRequest.__new__`` is typed ``(cls) -> _MutableHttpRequest``
    # and bleeds into the ``Request`` subclass: ty resolves the wrong ``__new__``
    # first and rejects the call (wrong return type, surplus positional + keyword
    # args). ``parsers=`` is a real DRF init kwarg per
    # ``rest_framework-stubs/request.pyi``. Construct via ``Any`` and cast back
    # to bypass the stub-confusion without losing the static type on the result.
    raw: Any = Request(http_request, parsers=[JSONParser()])  # ty: ignore[unknown-argument, too-many-positional-arguments]
    drf_request: Request = cast(Request, raw)
    drf_request.user = user
    return drf_request


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


def validate_input_against_serializer(
    arguments: dict[str, Any], input_serializer: type | None
) -> Any:
    """Validate ``arguments`` against ``input_serializer`` (DRF or bare dataclass).

    Returns:
      - the dataclass instance produced by a ``DataclassSerializer`` (when
        ``input_serializer`` is a bare ``@dataclass`` — auto-wrapped),
      - the ``validated_data`` dict for a plain DRF ``Serializer``,
      - ``None`` when ``input_serializer`` is ``None``.

    Raises :class:`drf_serializers.ValidationError` on invalid input. Lifted
    out of the ``handle_tools_call`` module so service-tool and selector-
    tool dispatch can share it without a circular import.
    """
    if input_serializer is None:
        return None
    target: type = input_serializer
    if dataclasses.is_dataclass(target) and not isinstance(target, type):  # pragma: no cover
        raise TypeError("input_serializer must be a class")
    if isinstance(target, type) and dataclasses.is_dataclass(target):
        wrapper_cls: type[drf_serializers.Serializer] = type(
            f"{target.__name__}Serializer",
            (DataclassSerializer,),
            {"Meta": type("Meta", (), {"dataclass": target})},
        )
        serializer = wrapper_cls(data=arguments)
    else:
        serializer = target(data=arguments)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


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


__all__ = [
    "build_internal_drf_request",
    "check_permissions",
    "consume_rate_limits",
    "validate_input_against_serializer",
    "validation_error_data",
]
