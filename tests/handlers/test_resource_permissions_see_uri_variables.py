"""A resource's permission reading ``view.kwargs`` judges the URI a read names.

A templated resource's variables ride on ``view.kwargs`` once the read
dispatches, and a permission class scoping by one reads it there, as it would
read a route capture over HTTP. ``resources/read`` judged the class-level check
before that, against a stand-in view whose ``kwargs`` were always ``{}``, so
such a permission denied a caller it admits. A subscription to the same URI is
judged the way the read is, or it would refuse a caller the read admits.
"""

from __future__ import annotations

from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.http import HttpRequest, QueryDict
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer, SubscriptionFilter
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.subscriptions.grant_subscription import grant_subscription
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.utils import RefusingRateLimit, granting_route


def _project_invoices(*, project_pk: str) -> Any:
    return Invoice.objects.all()


def _server(permission: type[BasePermission], *rate_limits: Any) -> MCPServer:
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_resource(
        name="project-invoices",
        uri_template="projects://{project_pk}/invoices",
        selector=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=_project_invoices,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[permission],
        ),
        rate_limits=list(rate_limits),
    )
    return server


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


async def _read(server: MCPServer, uri: str, *, is_async: bool) -> Any:
    if is_async:
        return await handle_resources_read_async({"uri": uri}, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_resources_read)({"uri": uri}, _ctx(server))


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_resource_permission_sees_the_uri_variables_of_the_read(is_async: bool) -> None:
    seen: list[dict[str, Any]] = []
    server = _server(granting_route("project_pk", "7", seen))

    out = await _read(server, "projects://7/invoices", is_async=is_async)

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out["contents"]
    # Every look it took: the class-level check up front, and the guard's on
    # the value the selector resolved.
    assert seen
    assert all(kwargs == {"project_pk": "7"} for kwargs in seen)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_resource_permission_still_denies_a_uri_it_does_not_grant(is_async: bool) -> None:
    seen: list[dict[str, Any]] = []
    server = _server(granting_route("project_pk", "7", seen))

    out = await _read(server, "projects://8/invoices", is_async=is_async)

    assert _forbidden(out)
    assert seen == [{"project_pk": "8"}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    ("uri", "refused_with"),
    [("projects://8/invoices", "permission"), ("projects://7/invoices", "rate limit")],
)
async def test_the_permission_still_answers_before_the_rate_limit(
    is_async: bool, uri: str, refused_with: str
) -> None:
    # Binding the variables moved the permission's view, not its place: a
    # caller it denies is told so and charged nothing, and a caller it admits
    # is the one the rate limit then refuses.
    limiter = RefusingRateLimit()
    server = _server(granting_route("project_pk", "7", []), limiter)

    out = await _read(server, uri, is_async=is_async)

    assert isinstance(out, JsonRpcError)
    if refused_with == "permission":
        assert out.code == JsonRpcErrorCode.FORBIDDEN
        assert limiter.consumed == 0
    else:
        assert out.code == JsonRpcErrorCode.RATE_LIMITED
        assert limiter.consumed == 1


def test_a_subscription_is_granted_the_uris_the_read_would_admit() -> None:
    seen: list[dict[str, Any]] = []
    server = _server(granting_route("project_pk", "7", seen))

    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("projects://7/invoices", "projects://8/invoices")),
        _ctx(server),
    )

    assert granted.resource_uris == ("projects://7/invoices",)
    assert seen == [{"project_pk": "7"}, {"project_pk": "8"}]


# ----- the stand-in and the guard's view are one request -----


class _User:
    pk = 1
    is_authenticated = True


# What a token backend publishes as ``TokenInfo.raw``, compared by identity.
_AUTH: object = object()


class _ReadsAuthThenUser(BasePermission):
    """``TokenHasScope``'s order: the backend's payload first, then the caller."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.auth is _AUTH and bool(request.user and request.user.is_authenticated)


class _AdmitsTheResourceAction(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return view.action == "project-invoices"


class _AdmitsNoQueryValue(BasePermission):
    """A resource takes no query value; the endpoint's own query string says ``project=99``."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return "project" not in request.query_params


class _AdmitsNoData(BasePermission):
    """A read carries no body: ``request.data`` is ``{}`` in both checks."""

    def has_permission(self, request: Any, view: Any) -> bool:
        return request.data == {}


_FAITHFUL = [_ReadsAuthThenUser, _AdmitsTheResourceAction, _AdmitsNoQueryValue, _AdmitsNoData]
_FAITHFUL_IDS = ["auth", "action", "query-params", "data"]


def _caller_ctx(server: MCPServer) -> MCPCallContext:
    """A signed-in caller with a token payload, on an endpoint whose URL carries a query string."""
    http_request = HttpRequest()
    http_request.GET = QueryDict("project=99")
    return MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=_User(), raw=_AUTH),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.parametrize("permission", _FAITHFUL, ids=_FAITHFUL_IDS)
async def test_a_resource_permission_reads_one_request_in_both_checks(
    permission: type[BasePermission], is_async: bool
) -> None:
    # The stand-in carried the endpoint's query string and no ``view.action``;
    # the guard's view carried no ``request.auth``, so reading it reset the
    # caller to ``AnonymousUser``. A permission reading any of the three
    # refused every caller in one check or the other.
    server = _server(permission)
    params: dict[str, Any] = {"uri": "projects://7/invoices"}

    if is_async:
        out = await handle_resources_read_async(params, _caller_ctx(server))
    else:
        out = await sync_to_async(handle_resources_read)(params, _caller_ctx(server))

    assert not isinstance(out, JsonRpcError), f"refused: {out!r}"
    assert out["contents"]


@pytest.mark.parametrize("permission", _FAITHFUL, ids=_FAITHFUL_IDS)
def test_a_subscription_reads_the_request_the_read_does(permission: type[BasePermission]) -> None:
    server = _server(permission)

    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("projects://7/invoices",)), _caller_ctx(server)
    )

    assert granted.resource_uris == ("projects://7/invoices",)
