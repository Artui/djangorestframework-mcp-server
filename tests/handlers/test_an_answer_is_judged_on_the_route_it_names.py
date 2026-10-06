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
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import granting_route, tool_error

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
    permission: type[BasePermission], ran_on: list[Any], *url_kwargs: UrlKwarg
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
        conventions=server.conventions,
    )


def _answering(name: str, arguments: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    """A ``tools/call`` carrying an accepted answer of ``content``, on a first call."""
    return {
        "name": name,
        "arguments": arguments,
        "inputResponses": {ELICITATION_KEY: {"action": "accept", "content": content}},
    }


async def _handler(server: MCPServer, params: dict[str, Any], *, is_async: bool) -> Any:
    if is_async:
        return await handle_tools_call_async(params, _ctx(server))
    # Off the event loop, where the sync handler's ORM work is allowed.
    return await sync_to_async(handle_tools_call)(params, _ctx(server))


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
