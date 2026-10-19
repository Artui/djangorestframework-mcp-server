"""A tool's channels come from its spec's ``reads``; the mount-level knobs are deprecated.

A spec declares the route captures and query values it reads as ``reads``, and
the output fields an agent sees as ``field_audiences``. Every registrar reads
both off the spec - ``register_service_tool``, ``register_selector_tool``, the
decorators and ``register_specs`` alike - so one spec is the same tool whichever
way it is mounted. The mount-level knobs that declared the same things beside
the spec (``url_kwargs=``, ``query_params=`` and ``agent_contract=``, as a
registrar argument or a ``register_specs`` override) still work for one minor
release, and say so with a ``DeprecationWarning`` naming what replaces them.
"""

from __future__ import annotations

import warnings
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import FieldAudience, FieldMarking, SelectorKind, SpecRegistry
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework_services import (
    OfflineContract,
    QueryParam,
    SelectorSpec,
    ServiceSpec,
    UrlKwarg,
)

from rest_framework_mcp import MCPServer, ToolDefinition, register_tools
from tests.testapp.serializers import AgentInvoiceSerializer


def _server() -> MCPServer:
    return MCPServer(name="t")


def _listed(server: MCPServer, name: str) -> dict[str, Any]:
    tools = server.list_tools(user=None)
    assert isinstance(tools, dict)
    return next(tool for tool in tools["tools"] if tool["name"] == name)["inputSchema"]


def _project_spec(**extra: Any) -> SelectorSpec[Any, Any]:
    # No catch-all: the route captures are there to be advertised, and a
    # bare ``**kwargs`` that names none of them is refused at registration.
    def project_card() -> dict[str, Any]:
        return {}

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=project_card,
        permission_classes=[AllowAny],
        reads=[
            UrlKwarg("project_pk", type="integer", required=True, description="The project."),
            QueryParam("fields", description="Fields to return."),
        ],
        **extra,
    )


def _registered(how: str, spec: SelectorSpec[Any, Any]) -> MCPServer:
    server = _server()
    if how == "direct":
        server.register_selector_tool(name="card", description="A project.", spec=spec)
    elif how == "decorator":
        server.selector_tool(name="card", description="A project.", spec=spec)(spec.selector)
    else:
        registry = SpecRegistry()
        registry.register("card", spec)
        server.register_specs(registry, overrides={"card": {"description": "A project."}})
    return server


_HOW = pytest.mark.parametrize("how", ["direct", "decorator", "register_specs"])


# ---------- the spec's own reads ----------


@_HOW
def test_every_registrar_advertises_the_specs_reads(how: str) -> None:
    schema = _listed(_registered(how, _project_spec()), "card")

    assert schema["properties"]["project_pk"] == {"type": "integer", "description": "The project."}
    assert schema["properties"]["fields"] == {"type": "string", "description": "Fields to return."}
    assert schema["required"] == ["project_pk"]


@_HOW
def test_every_registrar_refuses_a_missing_required_read_in_its_own_words(how: str) -> None:
    result = _registered(how, _project_spec()).call_tool("card", {}, user=None).to_dict()

    assert result["isError"] is True
    assert "Missing required argument(s): `project_pk`." in result["content"][0]["text"]


@_HOW
def test_every_registrar_applies_the_specs_field_audiences(how: str) -> None:
    def invoice(**_: Any) -> dict[str, Any]:
        return {}

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=invoice,
        permission_classes=[AllowAny],
        output_serializer=AgentInvoiceSerializer,
        field_audiences={"sent": FieldMarking()},
    )
    server = _registered(how, spec)

    binding = server.tools.get("card")
    assert binding is not None
    assert binding.audience_projection.audience("sent") is not FieldAudience.HIDDEN


@_HOW
def test_no_registrar_warns_for_a_spec_declaring_its_own_reads(how: str) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _registered(how, _project_spec())


# ---------- a QueryParam read naming one of the selector's parameters ----------


class _SeesQuery(BasePermission):
    seen: list[dict[str, Any]] = []

    def has_permission(self, request: Any, view: Any) -> bool:
        type(self).seen.append(dict(request.query_params))
        return True


def _status_spec() -> SelectorSpec[Any, Any]:
    def orders_by_status(*, status: str = "any") -> dict[str, Any]:
        return {"status": status}

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=orders_by_status,
        permission_classes=[_SeesQuery],
        reads=[QueryParam("status", description="The order status.")],
    )


@_HOW
def test_a_specs_own_query_param_naming_a_selector_parameter_is_served_on_every_route(
    how: str,
) -> None:
    # The spec says where the parameter comes from, so it is not a shadow: it
    # is advertised once, as the selector's parameter, and the value reaches
    # the selector and ``request.query_params`` both.
    _SeesQuery.seen = []
    server = _registered(how, _status_spec())

    binding = server.tools.get("card")
    assert binding is not None
    assert binding.query_params == ()
    assert list(_listed(server, "card")["properties"]) == ["status"]
    result = server.call_tool("card", {"status": "open"}, user=None).to_dict()
    assert result["structuredContent"] == {"status": "open"}
    assert _SeesQuery.seen and all(seen == {"status": ["open"]} for seen in _SeesQuery.seen)


def test_a_null_for_a_specs_own_query_param_is_no_query_value() -> None:
    # Off HTTP a null is how a model says it left an optional argument out, so
    # the request the permission judges carries no query value for it.
    _SeesQuery.seen = []
    server = _registered("direct", _status_spec())

    server.call_tool("card", {"status": None}, user=None)

    assert _SeesQuery.seen and all("status" not in seen for seen in _SeesQuery.seen)


def test_a_url_kwarg_read_never_reaches_the_query_string() -> None:
    # A route capture a deprecated ``url_kwargs=`` leaves out is still the
    # spec's ``UrlKwarg``, never a query value.
    _SeesQuery.seen = []

    # The captures are the permission class's to read, so the selector takes
    # none: a bare ``**kwargs`` beside them is refused at registration.
    def report() -> dict[str, Any]:
        return {}

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=report,
        permission_classes=[_SeesQuery],
        reads=[UrlKwarg("project_pk"), UrlKwarg("draw_pk")],
    )
    server = _server()
    with pytest.warns(DeprecationWarning):
        server.register_selector_tool(name="card", spec=spec, url_kwargs=(UrlKwarg("project_pk"),))

    server.call_tool("card", {"project_pk": "1", "draw_pk": "2"}, user=None)

    assert _SeesQuery.seen and all("draw_pk" not in seen for seen in _SeesQuery.seen)


@_HOW
def test_a_specs_own_query_param_naming_no_parameter_stays_a_channel(how: str) -> None:
    # The exemption is for a name the selector takes; another read is still
    # routed to ``request.query_params`` and advertised as declared.
    binding = _registered(how, _project_spec()).tools.get("card")

    assert binding is not None
    assert [query_param.name for query_param in binding.query_params] == ["fields"]


_MOUNT_SHADOWS = pytest.mark.parametrize(
    "knob",
    [
        {"query_params": (QueryParam("status"),)},
        {"agent_contract": OfflineContract(query_params=(QueryParam("status"),))},
    ],
    ids=["query_params", "agent_contract"],
)


@_MOUNT_SHADOWS
@_HOW
def test_a_mount_level_query_param_naming_a_selector_parameter_is_still_refused(
    how: str, knob: dict[str, Any]
) -> None:
    def orders_by_status(*, status: str = "any") -> dict[str, Any]:
        return {"status": status}

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=orders_by_status, permission_classes=[AllowAny]
    )
    server = _server()

    with (
        warnings.catch_warnings(),
        pytest.raises(ImproperlyConfigured, match="also declares as a QueryParam"),
    ):
        warnings.simplefilter("ignore", DeprecationWarning)
        if how == "direct":
            server.register_selector_tool(name="card", spec=spec, **knob)
        elif how == "decorator":
            server.selector_tool(name="card", spec=spec, **knob)(orders_by_status)
        else:
            registry = SpecRegistry()
            registry.register("card", spec)
            server.register_specs(registry, overrides={"card": knob})


# ---------- the deprecated knobs ----------


_KNOBS = pytest.mark.parametrize(
    ("knob", "replacement"),
    [
        ({"url_kwargs": (UrlKwarg("project_pk", type="integer"),)}, "reads=[UrlKwarg"),
        ({"query_params": (QueryParam("fields"),)}, "reads=[QueryParam"),
        (
            {"agent_contract": OfflineContract(query_params=(QueryParam("fields"),))},
            "server_arguments=ServerArguments(withheld=",
        ),
    ],
    ids=["url_kwargs", "query_params", "agent_contract"],
)


def _plain_selector() -> SelectorSpec[Any, Any]:
    # No catch-all: the route captures are there to be advertised, and a
    # bare ``**kwargs`` that names none of them is refused at registration.
    def project_card() -> dict[str, Any]:
        return {}

    return SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=project_card, permission_classes=[AllowAny]
    )


def _plain_service() -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(service=lambda **_: None, atomic=False, permission_classes=[AllowAny])


@_KNOBS
@pytest.mark.parametrize(
    "how", ["register_selector_tool", "register_service_tool", "decorator", "register_specs"]
)
def test_each_mount_level_knob_warns_naming_its_replacement(
    how: str, knob: dict[str, Any], replacement: str
) -> None:
    server = _server()
    name = next(iter(knob))

    with pytest.warns(DeprecationWarning) as caught:
        if how == "register_selector_tool":
            server.register_selector_tool(name="card", spec=_plain_selector(), **knob)
        elif how == "register_service_tool":
            server.register_service_tool(name="card", spec=_plain_service(), **knob)
        elif how == "decorator":
            spec = _plain_selector()
            server.selector_tool(name="card", spec=spec, **knob)(spec.selector)
        else:
            registry = SpecRegistry()
            registry.register("card", _plain_selector())
            server.register_specs(registry, overrides={"card": knob})

    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(deprecations) == 1
    message = str(deprecations[0].message)
    assert f"{name}=" in message
    assert "'card'" in message
    assert replacement in message
    # Attributed to the caller's line, not to this package's internals.
    assert deprecations[0].filename == __file__


def test_a_deprecated_knob_keeps_working_while_it_warns() -> None:
    server = _server()

    with pytest.warns(DeprecationWarning):
        server.register_selector_tool(
            name="card",
            spec=_plain_selector(),
            url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
        )

    assert _listed(server, "card")["required"] == ["project_pk"]


def test_a_mount_level_knob_replaces_the_specs_reads_of_its_kind_in_the_listing_only() -> None:
    # Held as it stands, for the release that deprecates the knob: an override
    # replaces every read of its kind in what the tool advertises and routes,
    # while drf-services still reads the spec's own declaration at dispatch.
    # So ``until``, which the override leaves out, is unadvertised and still
    # delivered to ``request.query_params``. Only ``server_arguments``'
    # ``withheld`` takes a channel away from the caller.
    _SeesQuery.seen = []

    # The captures are the permission class's to read, so the selector takes
    # none: a bare ``**kwargs`` beside them is refused at registration.
    def report() -> dict[str, Any]:
        return {}

    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=report,
        permission_classes=[_SeesQuery],
        reads=[QueryParam("since", description="spec"), QueryParam("until")],
    )
    server = _server()
    with pytest.warns(DeprecationWarning):
        server.register_selector_tool(
            name="card", spec=spec, query_params=(QueryParam("since", description="mount"),)
        )

    assert _listed(server, "card")["properties"] == {
        "since": {"type": "string", "description": "mount"}
    }
    server.call_tool("card", {"since": "a", "until": "b"}, user=None)
    assert _SeesQuery.seen[-1] == {"since": ["a"], "until": ["b"]}


@pytest.mark.parametrize(
    ("knob", "kind"),
    [
        ({"url_kwargs": (UrlKwarg("mount_pk"),)}, "url_kwargs"),
        ({"query_params": (QueryParam("mount_q"),)}, "query_params"),
    ],
    ids=["url_kwargs", "query_params"],
)
def test_an_explicit_knob_still_wins_over_an_agent_contract_beside_it(
    knob: dict[str, Any], kind: str
) -> None:
    # The precedence the deprecated knobs had between themselves holds while
    # they last: a channel of one kind passed beside a contract replaces the
    # contract's channels of that kind.
    contract = OfflineContract(
        url_kwargs=(UrlKwarg("contract_pk"),), query_params=(QueryParam("contract_q"),)
    )
    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        binding = server.register_selector_tool(
            name="card", spec=_plain_selector(), agent_contract=contract, **knob
        )

    assert [entry.name for entry in getattr(binding, kind)] == [knob[kind][0].name]


def test_a_tool_definition_carrying_a_knob_warns_at_its_caller() -> None:
    server = _server()
    spec = _plain_selector()

    with pytest.warns(DeprecationWarning, match=r"'card'.*query_params=") as caught:
        register_tools(
            server,
            [ToolDefinition.selector(name="card", spec=spec, query_params=[QueryParam("fields")])],
        )

    deprecation = next(w for w in caught if issubclass(w.category, DeprecationWarning))
    assert deprecation.filename == __file__
