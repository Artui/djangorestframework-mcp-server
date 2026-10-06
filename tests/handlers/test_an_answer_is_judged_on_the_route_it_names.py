"""A retry's answer naming a route capture is judged on the route it names.

``tools/call`` judges the binding's permissions on the URL kwargs the call
delivered, split from its arguments as sent, because a denied caller is told
so before its answers are read. A retry's ``inputResponses`` (and the answers
a ``requestState`` carries) are then merged over those arguments, and an
answer may name any key, a URL kwarg included. Judged only on the route as
sent, a caller granted project 7 could send ``project_pk: 7`` and answer
``project_pk: 8``: a per-binding ``DRFPermissionAdapter`` was never judged
again and the service ran on project 8, and a spec's ``permission_classes``
was judged again only by the target guard, after the target was looked up, so
an existing target answered ``FORBIDDEN`` and a missing one ``not_found``.
The route the merge produces is now judged again whenever it differs from the
one delivered, before anything is looked up or run.

"Differs" is ``same_route``, not ``==``: an answer of ``True`` or ``1.0`` for
``1``, or ``-0.0`` for ``0.0``, is equal in Python and names another row
through a ``CharField``, and ``!=`` waved it through unjudged. The second check
also comes before the rate limit, so a caller it refuses is not charged, and
the invalidation the call announces names the route the service ran on.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync, sync_to_async
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import DRFPermissionAdapter, MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ELICITATION_KEY, JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.utils import topic_for_resource
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import RefusingRateLimit, granting_route, tool_error

MODERN = "2026-07-28"


def _admitting_unless_another_project(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A permission admitting project 7, or a call naming no project at all.

    What a tool letting the user pick the project looks like: the call may
    leave ``project_pk`` out and have it answered, and the permission refuses
    only a project the caller holds no grant on.
    """

    class _AdmitsSevenOrNone(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            return view.kwargs.get("project_pk", 7) == 7

    return _AdmitsSevenOrNone


def _per_binding_server(
    permission: type[BasePermission],
    ran_on: list[Any],
    *url_kwargs: UrlKwarg,
    rate_limits: list[Any] | None = None,
) -> MCPServer:
    """``archive_project`` behind ``permission`` as a per-binding adapter.

    Per-binding, not on the spec, because the spec's ``permission_classes``
    are also judged by the target guard after the merge, which is what hid
    this: only ``check_permissions`` counts here.
    """

    def _archive_project(project_pk: Any = None) -> dict[str, Any]:
        ran_on.append(project_pk)
        return {"archived": project_pk}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="archive_project",
        description="Archive a project.",
        spec=ServiceSpec(
            service=_archive_project,
            kwargs=lambda view, request: {"project_pk": view.kwargs.get("project_pk")},
        ),
        permissions=[DRFPermissionAdapter(permission)],
        url_kwargs=url_kwargs,
        rate_limits=rate_limits or [],
    )
    return server


def _ctx(server: MCPServer, subscriptions: Any = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        conventions=server.conventions,
        subscriptions=subscriptions,
    )


def _answering(name: str, arguments: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """A ``tools/call`` carrying an accepted answer of ``content``, on a first call."""
    return {
        "name": name,
        "arguments": arguments,
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": content}},
    }


async def _handler(
    server: MCPServer, params: dict[str, Any], *, is_async: bool, subscriptions: Any = None
) -> Any:
    if is_async:
        return await handle_tools_call_async(params, _ctx(server, subscriptions))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server, subscriptions))


def _forbidden(out: Any) -> bool:
    return isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_naming_another_route_is_refused_by_a_per_binding_permission(
    is_async: bool,
) -> None:
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, seen),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await _handler(
        server,
        _answering("archive_project", {"project_pk": 7}, {"project_pk": 8}),
        is_async=is_async,
    )

    assert _forbidden(out), f"answered {out!r}"
    assert ran_on == []
    # Judged on the route as sent, then on the route the answer names.
    assert seen == [{"project_pk": 7}, {"project_pk": 8}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
def test_an_answer_naming_another_route_is_refused_over_http(is_async: bool) -> None:
    # The same call through the transport, where it answered ``200`` with the
    # service run on project 8 on both, and ``main`` answered ``403``.
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, []),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
    )
    params: dict[str, Any] = {
        **_answering("archive_project", {"project_pk": 7}, {"project_pk": 8}),
        "_meta": {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientCapabilities": {},
        },
    }
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    headers = {
        "Mcp-Protocol-Version": MODERN,
        "Mcp-Method": "tools/call",
        "Mcp-Name": "archive_project",
    }

    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response: Any = async_to_sync(AsyncClient().post)(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )
        else:
            response = Client().post(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )

    assert response.status_code == 403
    assert json.loads(response.content)["error"]["code"] == JsonRpcErrorCode.FORBIDDEN
    assert ran_on == []


_PROJECTS_WITH_A_TARGET: dict[int, dict[str, Any]] = {7: {"id": 1}, 8: {"id": 2}}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_cannot_tell_an_existing_target_from_a_missing_one(
    is_async: bool,
) -> None:
    # Project 8 has a target and project 9 none. Judged again only by the
    # target guard, which runs once the target is found, the first answered
    # ``FORBIDDEN`` and the second ``not_found``: which projects hold one was
    # readable by a caller granted neither.
    looked_up: list[Any] = []

    def _project_target(project_pk: Any = None) -> Any:
        looked_up.append(project_pk)
        return _PROJECTS_WITH_A_TARGET.get(project_pk)

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="touch",
        description="Touch a project's target.",
        spec=ServiceSpec(
            service=lambda instance=None: {"touched": instance},
            permission_classes=[granting_route("project_pk", 7, [])],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=_project_target,
                kwargs=lambda view, request: {"project_pk": view.kwargs["project_pk"]},
            ),
        ),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
    )

    answered: list[bool] = [
        _forbidden(
            await _handler(
                server,
                _answering("touch", {"project_pk": 7}, {"project_pk": project}),
                is_async=is_async,
            )
        )
        for project in (8, 9)
    ]

    assert answered == [True, True]
    # Refused before the target is looked up, so the lookup leaks nothing.
    assert looked_up == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize("content", [{"note": "urgent"}, {"project_pk": 7}])
async def test_an_answer_leaving_the_route_unchanged_is_not_judged_again(
    is_async: bool, content: dict[str, Any]
) -> None:
    # An answer naming no route capture, or naming the one the call sent, has
    # nothing new to judge; the permission is asked once, as on any call.
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, seen),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
    )

    out = await _handler(
        server, _answering("archive_project", {"project_pk": 7}, content), is_async=is_async
    )

    assert out.get("isError") is not True, f"answered {out!r}"
    assert ran_on == [7]
    assert seen == [{"project_pk": 7}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_filling_a_route_kwarg_left_out_is_judged_on_the_filled_route(
    is_async: bool,
) -> None:
    # The call names no project, which the permission admits, and the answer
    # picks project 7: judged once more, on the route the service runs with.
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        _admitting_unless_another_project(seen),
        ran_on,
        UrlKwarg("project_pk", type="integer"),
    )

    out = await _handler(
        server, _answering("archive_project", {}, {"project_pk": 7}), is_async=is_async
    )

    assert out.get("isError") is not True, f"answered {out!r}"
    assert ran_on == [7]
    assert seen == [{}, {"project_pk": 7}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_filling_a_route_kwarg_with_another_project_is_refused(
    is_async: bool,
) -> None:
    # The other half: the filled route is judged rather than admitted because
    # the route as sent was, so a project the caller holds no grant on is
    # refused before the service runs.
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        _admitting_unless_another_project(seen),
        ran_on,
        UrlKwarg("project_pk", type="integer"),
    )

    out = await _handler(
        server, _answering("archive_project", {}, {"project_pk": 8}), is_async=is_async
    )

    assert _forbidden(out), f"answered {out!r}"
    assert ran_on == []
    assert seen == [{}, {"project_pk": 8}]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_leaving_a_required_route_kwarg_missing_is_told_which(
    is_async: bool,
) -> None:
    # The route the answer produces is split without refusing a missing kwarg,
    # as the route as sent is: the permission is not where a missing argument
    # is reported, and a strict split there raised past the ``isError`` mapping.
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        _admitting_unless_another_project(seen),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
        UrlKwarg("tenant"),
    )

    out = await _handler(
        server,
        _answering("archive_project", {"tenant": "acme"}, {"tenant": "beta"}),
        is_async=is_async,
    )

    error = tool_error(out)
    assert error["message"] == "Missing required argument(s): `project_pk`."
    assert ran_on == []
    assert seen == [{"tenant": "acme"}, {"tenant": "beta"}]


# ----- an answer equal to the route as sent, under ``==``, naming another row -----

# Each pair is equal in Python, so ``!=`` read the route as unchanged and the
# second check was skipped, while a ``CharField`` lookup reads ``str()`` of the
# value and so another row: ``True`` reads ``"True"``, ``1.0`` reads ``"1.0"``,
# and ``-0.0`` reads ``"-0.0"``. The last is equal *and of the same type*, so a
# comparison by type and ``==`` still misses it.
_EQUAL_ROUTES: list[tuple[Any, Any]] = [(1, True), (1, 1.0), (0.0, -0.0)]
_EQUAL_ROUTE_IDS: list[str] = ["true-for-1", "float-for-int", "negative-zero"]


def _owning_the_invoice(seen: list[dict[str, Any]]) -> type[BasePermission]:
    """A permission admitting a caller on the invoice its route names, if it owns it.

    Reads the row the way the target lookup does, through the ``CharField``, so
    it denies the row an equal-but-different value names, as a real ownership
    check would. ``amount_cents=100`` marks the caller's own invoice.
    """

    class _OwnsTheInvoice(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            seen.append(dict(view.kwargs))
            number = view.kwargs.get("number")
            return (
                number is not None
                and Invoice.objects.filter(number=number, amount_cents=100).exists()
            )

    return _OwnsTheInvoice


def _invoice_server(
    permission: type[BasePermission],
    voided: list[Any],
    looked_up: list[Any],
    *,
    on_spec: bool,
) -> MCPServer:
    """``void_invoice``, its target looked up by the ``number`` URL kwarg.

    ``on_spec`` puts ``permission`` in the spec's ``permission_classes``,
    which the target guard also judges once the target is found; otherwise it
    is a per-binding adapter, which only ``check_permissions`` judges.
    """

    def _lookup(number: Any) -> Any:
        looked_up.append(number)
        return Invoice.objects.filter(number=number)

    def _void(instance: Any = None) -> dict[str, Any]:
        voided.append(instance.number)
        return {"voided": instance.number}

    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="void_invoice",
        description="Void an invoice.",
        spec=ServiceSpec(
            service=_void,
            permission_classes=[permission] if on_spec else [],
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_lookup),
        ),
        permissions=[] if on_spec else [DRFPermissionAdapter(permission)],
        url_kwargs=(UrlKwarg("number", required=True),),
    )
    return server


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(("sent", "answered"), _EQUAL_ROUTES, ids=_EQUAL_ROUTE_IDS)
async def test_an_answer_equal_to_the_route_but_naming_another_row_is_judged_again(
    is_async: bool, sent: Any, answered: Any
) -> None:
    # The caller owns invoice ``str(sent)`` and not ``str(answered)``. Judged
    # once, on the route as sent, the service voided the other invoice.
    await sync_to_async(Invoice.objects.create)(number=str(sent), amount_cents=100)
    await sync_to_async(Invoice.objects.create)(number=str(answered), amount_cents=0)
    seen: list[dict[str, Any]] = []
    voided: list[Any] = []
    server = _invoice_server(_owning_the_invoice(seen), voided, [], on_spec=False)

    out = await _handler(
        server,
        _answering("void_invoice", {"number": sent}, {"number": answered}),
        is_async=is_async,
    )

    assert _forbidden(out), f"answered {out!r}"
    assert voided == []
    # ``repr``, since a list of the two routes compares equal to ``[sent, sent]``.
    assert repr(seen) == repr([{"number": sent}, {"number": answered}])


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(("sent", "answered"), _EQUAL_ROUTES, ids=_EQUAL_ROUTE_IDS)
@pytest.mark.parametrize("other_exists", [True, False], ids=["existing", "missing"])
async def test_an_answer_equal_to_the_route_cannot_tell_an_existing_target_from_a_missing_one(
    is_async: bool, sent: Any, answered: Any, other_exists: bool
) -> None:
    # The spec's own class, judged by the target guard as well. Left to the
    # guard, an existing other invoice answered ``FORBIDDEN`` after its lookup
    # and a missing one ``not_found``, which tells a caller owning neither
    # which invoices exist.
    await sync_to_async(Invoice.objects.create)(number=str(sent), amount_cents=100)
    if other_exists:
        await sync_to_async(Invoice.objects.create)(number=str(answered), amount_cents=0)
    looked_up: list[Any] = []
    voided: list[Any] = []
    server = _invoice_server(_owning_the_invoice([]), voided, looked_up, on_spec=True)

    out = await _handler(
        server,
        _answering("void_invoice", {"number": sent}, {"number": answered}),
        is_async=is_async,
    )

    assert _forbidden(out), f"answered {out!r}"
    # Refused before the target is looked up, so the lookup leaks nothing.
    assert looked_up == []
    assert voided == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_an_answer_clearing_a_route_kwarg_is_judged_on_the_route_it_leaves(
    is_async: bool,
) -> None:
    # An answer of ``null`` drops the kwarg from the route, as a ``null``
    # argument does, so the service would run on no project at all. The route
    # it leaves names fewer kwargs than the one judged, which is a move too.
    seen: list[dict[str, Any]] = []
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, seen), ran_on, UrlKwarg("project_pk", type="integer")
    )

    out = await _handler(
        server,
        _answering("archive_project", {"project_pk": 7}, {"project_pk": None}),
        is_async=is_async,
    )

    assert _forbidden(out), f"answered {out!r}"
    assert ran_on == []
    assert seen == [{"project_pk": 7}, {}]


# ----- the invalidation an answered call announces -----


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_the_invalidation_names_the_route_an_answer_moved_to(is_async: bool) -> None:
    # Announced with the arguments as sent, the notice named project 7 while
    # the service archived project 8: a subscriber to 8 missed the change and
    # one to 7 re-read an unchanged resource.
    broker = InMemorySubscriptionBroker()
    moved_to = await broker.subscribe(frozenset({topic_for_resource("projects://8")}))
    sent_to = await broker.subscribe(frozenset({topic_for_resource("projects://7")}))
    ran_on: list[Any] = []
    server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)
    server.register_service_tool(
        name="archive_project",
        description="Archive a project.",
        spec=ServiceSpec(
            service=lambda project_pk=None: ran_on.append(project_pk) or {"archived": True},
            permission_classes=[_granting_either(7, 8)],
            kwargs=lambda view, request: {"project_pk": view.kwargs.get("project_pk")},
        ),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
        invalidates=("projects://{project_pk}",),
    )

    out = await _handler(
        server,
        _answering("archive_project", {"project_pk": 7}, {"project_pk": 8}),
        is_async=is_async,
        subscriptions=broker,
    )

    assert out.get("isError") is not True, f"answered {out!r}"
    assert ran_on == [8]
    assert moved_to.get_nowait()["params"]["uri"] == "projects://8"
    assert sent_to.qsize() == 0


def _granting_either(*projects: int) -> type[BasePermission]:
    class _GrantsEither(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return view.kwargs.get("project_pk") in projects

    return _GrantsEither


# ----- the rate limit, against the second check -----


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
async def test_a_caller_refused_on_the_route_an_answer_names_is_not_charged(
    is_async: bool,
) -> None:
    # Charged between the two checks, a caller refused on the answered route
    # had already spent a unit; with a limit refusing every call, it was told
    # ``RATE_LIMITED`` rather than that the route is not its to name.
    limit = RefusingRateLimit()
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, []),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
        rate_limits=[limit],
    )

    out = await _handler(
        server,
        _answering("archive_project", {"project_pk": 7}, {"project_pk": 8}),
        is_async=is_async,
    )

    assert _forbidden(out), f"answered {out!r}"
    assert limit.consumed == 0
    assert ran_on == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("is_async", [False, True])
@pytest.mark.parametrize(
    "answer",
    [
        {"action": "accept", "content": {"project_pk": 7}},
        {"action": "decline"},
        {"action": "cancel"},
    ],
    ids=["route-unchanged", "declined", "cancelled"],
)
async def test_a_caller_admitted_on_the_answered_route_is_charged_once(
    is_async: bool, answer: dict[str, Any]
) -> None:
    # The other half: the charge moved after the second check, not away. An
    # answer the permission admits is charged as any call is, and so is one
    # the user declined or cancelled, answered after the charge as before.
    limit = RefusingRateLimit()
    ran_on: list[Any] = []
    server = _per_binding_server(
        granting_route("project_pk", 7, []),
        ran_on,
        UrlKwarg("project_pk", type="integer", required=True),
        rate_limits=[limit],
    )
    params: dict[str, Any] = {
        "name": "archive_project",
        "arguments": {"project_pk": 7},
        "inputResponses": {ELICITATION_KEY: answer},
    }

    out = await _handler(server, params, is_async=is_async)

    assert isinstance(out, JsonRpcError) and out.code == JsonRpcErrorCode.RATE_LIMITED
    assert limit.consumed == 1
    assert ran_on == []
