"""``server_arguments=``: declared parameters the server answers for, so no caller does.

A tool registered with a
[`ServerArguments`][django_service_specs.types.server_arguments.ServerArguments]
takes the bound names from the server and the withheld ones from no one. The
caller is never offered either: ``tools/list`` leaves them out of the
``inputSchema`` and its ``required``. Whatever the caller sends under one of
them is dropped before anything reads the arguments, logged by name, and the
bound value takes its place, so the permission check, the strict split of a
required ``UrlKwarg`` and the missing-argument check all see the server's
value. A binding that cannot mean what it says is refused at registration,
naming the tool.

Every case goes through the public surface - the registrars, ``tools/list``,
``tools/call`` over the mounted ``/mcp/`` endpoint, ``call_tool`` and
``acall_tool`` - so each fails against a server that does not take the
keyword rather than at import.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.core.exceptions import ImproperlyConfigured
from django.test import AsyncClient, Client, override_settings
from django_service_specs import DEFAULT_POOL_SEEDS, SelectorKind, ServerArguments, SpecRegistry
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import QueryParam, SelectorSpec, ServiceSpec, UrlKwarg

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ELICITATION_KEY
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import granting_route, tool_error

_MODERN = "2026-07-28"


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _post(server: MCPServer, name: str, params: dict[str, Any], *, is_async: bool) -> Any:
    """One modern-era ``tools/call`` to the mounted endpoint; the JSON-RPC ``result``."""
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": name,
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": _MODERN,
                    "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }
    )
    headers = {"Mcp-Protocol-Version": _MODERN, "Mcp-Method": "tools/call", "Mcp-Name": name}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        if is_async:
            response = async_to_sync(AsyncClient().post)(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )
        else:
            response = Client().post(
                "/mcp/", data=body, content_type="application/json", headers=headers
            )
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]


def _listed(server: MCPServer, name: str) -> dict[str, Any]:
    tools = server.list_tools(user=None)
    assert isinstance(tools, dict)
    return next(tool for tool in tools["tools"] if tool["name"] == name)["inputSchema"]


_TRANSPORTS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])


# ---------- the consumer's shape: a run scoped to one project ----------
#
# Modelled on the one production binding the design answers: a document's
# enrichment run scopes a vendor listing to the document's project, or else to
# its draw request, so the model is never asked for an identifier it cannot
# know. Two optional route captures, an either-or scope, a permission reading
# the route, and a selector filtering by whichever capture it receives.

_VENDORS: list[dict[str, Any]] = [
    {"id": 1, "project": 7, "draw_request": None},
    {"id": 2, "project": 8, "draw_request": None},
    {"id": 3, "project": None, "draw_request": 5},
]


def _vendor_registry(seen: list[dict[str, Any]], routes: list[dict[str, Any]]) -> SpecRegistry:
    def list_document_vendors(
        *, project_pk: int | None = None, draw_request_pk: int | None = None
    ) -> list[dict[str, Any]]:
        seen.append({"project_pk": project_pk, "draw_request_pk": draw_request_pk})
        if project_pk is not None:
            return [row for row in _VENDORS if row["project"] == project_pk]
        return [row for row in _VENDORS if row["draw_request"] == draw_request_pk]

    def get_vendor(*, pk: int, project_pk: int | None = None) -> dict[str, Any] | None:
        return next(
            (row for row in _VENDORS if row["id"] == pk and row["project"] == project_pk), None
        )

    reads = [
        UrlKwarg("project_pk", type="integer", description="The project."),
        UrlKwarg("draw_request_pk", type="integer", description="The draw request."),
    ]
    registry = SpecRegistry()
    registry.register(
        "list_document_vendors",
        SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_document_vendors,
            permission_classes=[granting_route("project_pk", 7, routes)],
            reads=reads,
        ),
    )
    registry.register(
        "get_vendor",
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_vendor,
            permission_classes=[AllowAny],
            reads=reads[:1],
        ),
    )
    return registry


def _scoped(project_pk: int) -> ServerArguments:
    return ServerArguments(values={"project_pk": project_pk}, withheld={"draw_request_pk"})


def _scoped_server(seen: list[dict[str, Any]], routes: list[dict[str, Any]]) -> MCPServer:
    server = _server()
    server.register_specs(
        _vendor_registry(seen, routes),
        overrides={
            "list_document_vendors": {"server_arguments": _scoped(7)},
            "get_vendor": {"server_arguments": ServerArguments(values={"project_pk": 7})},
        },
    )
    return server


def test_the_scope_names_leave_the_listing() -> None:
    server = _scoped_server([], [])

    schema = _listed(server, "list_document_vendors")

    assert "project_pk" not in schema["properties"]
    assert "draw_request_pk" not in schema["properties"]
    assert "required" not in schema
    assert set(_listed(server, "get_vendor")["properties"]) == {"pk"}


_ROUTES = ["wire-sync", "wire-async", "call_tool", "acall_tool"]


def _call(server: MCPServer, route: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if route.startswith("wire"):
        return _post(server, name, {"arguments": arguments}, is_async=route == "wire-async")
    if route == "call_tool":
        return server.call_tool(name, arguments, user=None).to_dict()
    result = async_to_sync(server.acall_tool)(name, arguments, user=None)
    assert isinstance(result, dict)
    return result


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
def test_the_bound_project_wins_and_the_withheld_sibling_is_dropped(
    route: str, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[dict[str, Any]] = []
    routes: list[dict[str, Any]] = []
    server = _scoped_server(seen, routes)

    with caplog.at_level(logging.WARNING, logger="rest_framework_mcp"):
        # The model names another project and sends ``"null"`` for a sibling it
        # was never shown: neither refuses the call, and neither arrives.
        result = _call(
            server,
            route,
            "list_document_vendors",
            {"project_pk": 8, "draw_request_pk": "null"},
        )

    assert not result.get("isError"), result
    assert result["structuredContent"] == [{"id": 1, "project": 7, "draw_request": None}]
    assert seen == [{"project_pk": 7, "draw_request_pk": None}]
    # The spec's permission reads the route, and sees the server's value.
    assert routes and all(route_kwargs.get("project_pk") == 7 for route_kwargs in routes)
    # Named, never valued: arguments are the consumer's data.
    messages = [record.getMessage() for record in caplog.records]
    dropped = [message for message in messages if "list_document_vendors" in message]
    assert len(dropped) == 1, messages
    assert "draw_request_pk" in dropped[0]
    assert "project_pk" in dropped[0]
    assert "null" not in dropped[0]
    assert "8" not in dropped[0]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", _ROUTES)
def test_an_id_outside_the_scope_answers_not_found(route: str) -> None:
    server = _scoped_server([], [])

    assert _call(server, route, "get_vendor", {"pk": 1})["structuredContent"]["id"] == 1
    error = tool_error(_call(server, route, "get_vendor", {"pk": 2, "project_pk": 8}))
    assert error["type"] == "not_found"


def test_a_caller_restating_the_bound_value_is_not_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # What a task worker replays: arguments the binding already applied. A
    # withheld name is logged whenever it is present, a bound one only when the
    # caller's value differs from the server's.
    server = _scoped_server([], [])

    with caplog.at_level(logging.WARNING, logger="rest_framework_mcp"):
        server.call_tool("list_document_vendors", {"project_pk": 7}, user=None)
        server.call_tool("list_document_vendors", {"project_pk": True}, user=None)

    dropped = [r.getMessage() for r in caplog.records if "list_document_vendors" in r.getMessage()]
    assert len(dropped) == 1, dropped


# ---------- a binding fills a required read ----------
#
# Sized so each check that asks for an argument before dispatch is the one that
# would answer: the strict split of a ``required=True`` UrlKwarg for the first,
# the selector's own parameter with no default for the second. Applied after
# either, the binding would come too late and the call would be refused for the
# argument the server supplies.


def _required_read_server(seen: list[Any]) -> MCPServer:
    def by_required_route(*, project_pk: int | None = None) -> dict[str, Any]:
        seen.append(project_pk)
        return {"project": project_pk}

    def by_required_parameter(*, project_pk: int) -> dict[str, Any]:
        seen.append(project_pk)
        return {"project": project_pk}

    def archive(*, instance: dict[str, Any]) -> dict[str, Any]:
        seen.append(instance)
        return instance

    server = _server()
    registry = SpecRegistry()
    registry.register(
        "required_route",
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=by_required_route,
            permission_classes=[AllowAny],
            reads=[UrlKwarg("project_pk", type="integer", required=True)],
        ),
    )
    registry.register(
        "required_parameter",
        SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=by_required_parameter,
            permission_classes=[AllowAny],
            reads=[UrlKwarg("project_pk", type="integer")],
        ),
    )
    registry.register(
        "archive_project",
        ServiceSpec(
            service=archive,
            atomic=False,
            permission_classes=[AllowAny],
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=by_required_parameter,
                reads=[UrlKwarg("project_pk", type="integer", required=True)],
            ),
        ),
    )
    bound = {"server_arguments": ServerArguments(values={"project_pk": 7})}
    server.register_specs(
        registry,
        overrides={"required_route": bound, "required_parameter": bound, "archive_project": bound},
    )
    return server


@pytest.mark.django_db(transaction=True)
@_TRANSPORTS
@pytest.mark.parametrize("name", ["required_route", "required_parameter", "archive_project"])
def test_a_binding_fills_a_required_read_over_the_wire(name: str, is_async: bool) -> None:
    seen: list[Any] = []
    server = _required_read_server(seen)

    listed = _listed(server, name)
    assert "project_pk" not in listed["properties"]
    # Its only required name bound, the schema carries no ``required`` at all,
    # as a builder leaves it out when nothing is required.
    assert "required" not in listed
    result = _post(server, name, {"arguments": {}}, is_async=is_async)

    assert not result.get("isError"), result
    assert result["structuredContent"] == {"project": 7}
    assert seen[0] == 7


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("route", ["call_tool", "acall_tool"])
@pytest.mark.parametrize("name", ["required_route", "required_parameter", "archive_project"])
def test_a_binding_fills_a_required_read_in_process(name: str, route: str) -> None:
    server = _required_read_server([])

    assert _call(server, route, name, {})["structuredContent"] == {"project": 7}


# ---------- a service tool, and a retry's answers ----------


class _Note(serializers.Serializer):
    project = serializers.CharField()
    note = serializers.CharField()


def _note_server(ran: list[dict[str, Any]]) -> MCPServer:
    def add_note(*, data: Any) -> dict[str, Any]:
        ran.append(dict(data))
        return {"ok": True}

    server = _server()
    server.register_service_tool(
        name="add_note",
        description="Add a note to the project.",
        spec=ServiceSpec(
            service=add_note, input_serializer=_Note, atomic=False, permission_classes=[AllowAny]
        ),
        server_arguments=ServerArguments(values={"project": "7"}),
    )
    return server


def test_a_service_tools_schema_subtracts_the_bound_field() -> None:
    schema = _listed(_note_server([]), "add_note")

    assert set(schema["properties"]) == {"note"}
    assert schema["required"] == ["note"]


@pytest.mark.django_db(transaction=True)
@_TRANSPORTS
def test_an_answer_cannot_replace_a_bound_value(is_async: bool) -> None:
    # A retry's ``inputResponses`` are merged over the arguments with no limit
    # on their keys, and a first call can carry one.
    ran: list[dict[str, Any]] = []
    server = _note_server(ran)
    answer = {ELICITATION_KEY: {"action": "accept", "content": {"project": "8"}}}

    result = _post(
        server,
        "add_note",
        {"arguments": {"note": "hi"}, "inputResponses": answer},
        is_async=is_async,
    )

    assert not result.get("isError"), result
    assert ran == [{"project": "7", "note": "hi"}]


# ---------- what registration refuses ----------


def _plain_spec(*, project_default: bool = True) -> SelectorSpec[Any, Any]:
    def plain(*, project_pk: int | None = None) -> dict[str, Any]:
        return {"project": project_pk}

    def demanding(*, project_pk: int) -> dict[str, Any]:
        return {"project": project_pk}

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=plain if project_default else demanding,
        permission_classes=[AllowAny],
        reads=[UrlKwarg("project_pk", type="integer"), QueryParam("fields")],
    )


@pytest.mark.parametrize(
    ("server_arguments", "named"),
    [
        (ServerArguments(values={"tenant": 1}), "tenant"),
        (ServerArguments(values={"project_pk": "seven"}), "project_pk"),
    ],
    ids=["undeclared", "value-its-parameter-refuses"],
)
def test_a_binding_that_cannot_mean_what_it_says_is_refused_naming_the_tool(
    server_arguments: ServerArguments, named: str
) -> None:
    with pytest.raises(ImproperlyConfigured) as refused:
        _server().register_selector_tool(
            name="project_card", spec=_plain_spec(), server_arguments=server_arguments
        )

    assert "'project_card'" in str(refused.value)
    assert named in str(refused.value)


def test_withholding_a_name_no_call_can_do_without_is_refused() -> None:
    with pytest.raises(ImproperlyConfigured) as refused:
        _server().register_selector_tool(
            name="project_card",
            spec=_plain_spec(project_default=False),
            server_arguments=ServerArguments(withheld={"project_pk"}),
        )

    assert "'project_card'" in str(refused.value)
    assert "withholds project_pk" in str(refused.value)


def test_a_service_tool_binding_is_checked_too() -> None:
    with pytest.raises(ImproperlyConfigured, match="'add_note'.*tenant"):
        _server().register_service_tool(
            name="add_note",
            spec=ServiceSpec(service=lambda *, data: None, input_serializer=_Note, atomic=False),
            server_arguments=ServerArguments(values={"tenant": "acme"}),
        )


def test_a_register_specs_override_is_checked_too() -> None:
    registry = _vendor_registry([], [])

    with pytest.raises(ImproperlyConfigured, match="'get_vendor'.*tenant"):
        _server().register_specs(
            registry,
            overrides={"get_vendor": {"server_arguments": ServerArguments(values={"tenant": 1})}},
        )


@pytest.mark.parametrize("kind", ["selector", "service"])
def test_a_binding_built_by_hand_is_checked_when_it_is_registered(kind: str) -> None:
    # ``tools.register`` takes a binding as it is, so a binding built without a
    # registrar would otherwise reach every ``tools/call`` unchecked and fail
    # each one, where it should fail the server's startup.
    binding: SelectorToolBinding[Any, Any] | ToolBinding[Any, Any, Any]
    if kind == "selector":
        binding = SelectorToolBinding(
            name="by_hand",
            description="d",
            spec=_plain_spec(),
            server_arguments=ServerArguments(values={"tenant": 1}),
        )
    else:
        binding = ToolBinding(
            name="by_hand",
            description="d",
            spec=ServiceSpec(service=lambda *, data: None, input_serializer=_Note),
            server_arguments=ServerArguments(values={"tenant": 1}),
        )
    server = _server()

    with pytest.raises(ImproperlyConfigured, match="'by_hand'.*tenant"):
        server.tools.register(binding)
    assert "by_hand" not in server.tools


def test_a_registered_seed_is_refused_with_the_servers_own_seeds() -> None:
    # The registry checks against the seeds the server dispatches with, not
    # the dispatcher's defaults alone.
    def scoped(*, tenant: str = "none") -> dict[str, Any]:
        return {"tenant": tenant}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=scoped, permission_classes=[AllowAny])
    bind = ServerArguments(values={"tenant": "acme"})
    _server().register_selector_tool(name="plain", spec=spec, server_arguments=bind)
    seeded = MCPServer(name="t", pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme"))

    with pytest.raises(ImproperlyConfigured, match="'plain'.*pool seed"):
        seeded.register_selector_tool(name="plain", spec=spec, server_arguments=bind)


def test_no_binding_leaves_the_tool_as_it_was() -> None:
    server = _server()
    bound = server.register_selector_tool(name="bound", spec=_plain_spec())

    assert bound.server_arguments == ServerArguments()
    assert set(_listed(server, "bound")["properties"]) == {"project_pk", "fields"}


def test_a_tool_binding_nothing_never_reaches_the_kernels_rule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A binding that binds nothing hands its arguments on untouched: the
    # kernel's rule, which checks each bound value against its parameter on
    # every call, is not run for a tool with nothing bound.
    def refuse(*_: Any, **__: Any) -> dict[str, Any]:
        raise AssertionError("apply_server_arguments ran for a tool binding nothing")

    monkeypatch.setattr("rest_framework_mcp.handlers.utils.apply_server_arguments", refuse)
    server = _server()
    server.register_selector_tool(name="plain", spec=_plain_spec())

    result = server.call_tool("plain", {"project_pk": 7}, user=None).to_dict()

    assert result.get("isError") is not True
