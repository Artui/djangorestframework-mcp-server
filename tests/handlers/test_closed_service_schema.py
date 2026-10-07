"""A service tool advertises the arguments dispatch admits, and closes where it refuses.

drf-mcp passes a service tool's ``unknown_arguments`` to drf-services' dispatch
as registered, on every binding: ``REJECT``, the default, refuses a name the
spec does not declare whether or not the spec has an ``input_serializer``.
The ``inputSchema`` lists the set drf-services declares for the spec and the
binding (``declared_input_keys``), the service's own parameters included under
a ``SPREAD_*`` binding, and stamps ``additionalProperties: false`` exactly
where dispatch refuses an undeclared name. A serializer-less service once had
``REJECT`` downgraded before dispatch and an open schema to match, so a
misspelled argument was dropped without a word.
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import Annotated, Any

import pytest
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import NotClientInput
from rest_framework_services.dispatch.utils import declared_input_keys
from rest_framework_services.types.reserved_pool_seeds import RESERVED_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import ArgumentBinding, UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import tool_error

_BINDINGS = (
    ArgumentBinding.BUNDLE,
    ArgumentBinding.SPREAD_AUTHOR_WINS,
    ArgumentBinding.SPREAD_CALLER_WINS,
)
_SPREAD = (ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS)

# A value for every name any spec below can advertise, so a call can send
# exactly the advertised set.
_VALUES: dict[str, Any] = {"pk": 1, "reason": "duplicate", "title": "t", "note": "n"}


def _get_task(*, pk: int) -> SimpleNamespace:
    return SimpleNamespace(pk=pk, archived=False)


def _get_task_in_tenant(*, pk: int, tenant: int = 1) -> SimpleNamespace:
    # Names ``tenant`` plainly; the precondition below is what hides it.
    return SimpleNamespace(pk=pk, archived=False)


def _same_tenant(*, tenant: Annotated[int, NotClientInput] = 1) -> None:
    return None


def _archive_task(*, instance: SimpleNamespace, reason: str = "") -> dict[str, Any]:
    return {"pk": instance.pk, "reason": reason}


def _rename(*, title: str = "", note: str = "") -> dict[str, Any]:
    return {"title": title, "note": note}


def _touch(*, title: str = "", **changes: Any) -> dict[str, Any]:
    return {"title": title, "changes": sorted(changes)}


def _bundled(*, data: Any = None) -> dict[str, Any]:
    return {"ok": True}


class _TitleInput(drf_serializers.Serializer):
    title = drf_serializers.CharField()


_SPECS: dict[str, Callable[[], ServiceSpec]] = {
    # Serializer-less, spreading into the service's own parameters.
    "spread": lambda: ServiceSpec(service=_rename, atomic=False, permission_classes=[AllowAny]),
    # The serializer declares the input, whatever the binding.
    "serializer": lambda: ServiceSpec(
        service=_bundled,
        input_serializer=_TitleInput,
        atomic=False,
        permission_classes=[AllowAny],
    ),
    # A bare ``**kwargs`` opens the set wherever the service is spread into.
    "var-keyword": lambda: ServiceSpec(service=_touch, atomic=False, permission_classes=[AllowAny]),
    "target-lookup": lambda: ServiceSpec(
        service=_archive_task,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_task),
        atomic=False,
        permission_classes=[AllowAny],
    ),
    "precondition-hides-a-lookup-key": lambda: ServiceSpec(
        service=_archive_task,
        instance_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_get_task_in_tenant
        ),
        preconditions=[_same_tenant],
        atomic=False,
        permission_classes=[AllowAny],
    ),
}


def _server(spec: ServiceSpec, *, binding: ArgumentBinding, policy: UnknownArguments) -> MCPServer:
    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    server.register_service_tool(
        name="t", spec=spec, argument_binding=binding, unknown_arguments=policy
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


def _input_schema(server: MCPServer) -> dict[str, Any]:
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    return out["tools"][0]["inputSchema"]


def _call(server: MCPServer, arguments: dict[str, Any]) -> dict[str, Any]:
    out = handle_tools_call({"name": "t", "arguments": arguments}, _ctx(server))
    assert isinstance(out, dict)
    return out


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("binding", "advertised"),
    [
        (ArgumentBinding.BUNDLE, {"pk"}),
        (ArgumentBinding.SPREAD_AUTHOR_WINS, {"pk", "reason"}),
        (ArgumentBinding.SPREAD_CALLER_WINS, {"pk", "reason"}),
    ],
)
def test_a_serializer_less_service_refuses_an_undeclared_argument(
    binding: ArgumentBinding, advertised: set[str]
) -> None:
    # The reproduction from the issue: ``archive_task(*, instance, reason)``
    # behind a ``pk`` lookup, with no ``input_serializer``, under the default
    # ``REJECT``. Spread, the service's ``reason`` is its input and is listed;
    # bundled, nothing reads it, so only the lookup's ``pk`` is.
    server = _server(_SPECS["target-lookup"](), binding=binding, policy=UnknownArguments.REJECT)

    schema = _input_schema(server)

    assert set(schema["properties"]) == advertised
    assert schema["additionalProperties"] is False
    sent = {key: _VALUES[key] for key in advertised}
    error = tool_error(_call(server, {**sent, "notify_owner": True}))
    assert error["type"] == "validation_error"
    assert error["detail"] == {"non_field_errors": ["Unexpected argument(s): 'notify_owner'."]}
    served = _call(server, {"pk": 1, "reason": "duplicate"})
    if binding in _SPREAD:
        # The service's own parameter is admitted and delivered.
        assert served.get("isError") is not True
        assert served["structuredContent"] == {"pk": 1, "reason": "duplicate"}
    else:
        # Bundled, nothing reads ``reason`` by name, so it is refused the same way.
        assert tool_error(served)["detail"] == {
            "non_field_errors": ["Unexpected argument(s): 'reason'."]
        }


@pytest.mark.django_db
@pytest.mark.parametrize("policy", list(UnknownArguments))
@pytest.mark.parametrize("binding", _BINDINGS)
@pytest.mark.parametrize("name", list(_SPECS))
def test_the_advertised_properties_are_the_keys_dispatch_admits(
    name: str, binding: ArgumentBinding, policy: UnknownArguments
) -> None:
    # The agreement ``advertises_closed_schema`` rests on, asked of drf-services
    # rather than restated: the properties are the set dispatch declares for
    # this spec and binding, and the schema is closed exactly where dispatch
    # refuses a name outside it. Closed, a call naming one undeclared key is
    # refused for that key; open, the same call is served.
    spec = _SPECS[name]()
    server = _server(spec, binding=binding, policy=policy)
    serializer = spec.input_serializer() if spec.input_serializer is not None else None
    admitted = declared_input_keys(spec, serializer=serializer, argument_binding=binding)

    schema = _input_schema(server)
    closed = schema["additionalProperties"] is False

    assert closed is (policy is UnknownArguments.REJECT and admitted is not None)
    if admitted is not None:
        assert set(schema.get("properties", {})) == admitted - RESERVED_POOL_SEEDS
    arguments = {key: _VALUES[key] for key in schema.get("properties", {})}
    out = _call(server, {**arguments, "undeclared": 1})
    if closed:
        error = tool_error(out)
        assert error["type"] == "validation_error"
        assert error["detail"] == {"non_field_errors": ["Unexpected argument(s): 'undeclared'."]}
    else:
        assert out.get("isError") is not True


@pytest.mark.django_db
@pytest.mark.parametrize("binding", _SPREAD)
def test_ignore_keeps_serving_an_undeclared_argument(binding: ArgumentBinding) -> None:
    # ``IGNORE`` is the opt-out: the schema lists the service's parameters and
    # stays open, and a call naming an undeclared key is served, with the
    # declared one delivered and the other dropped.
    server = _server(_SPECS["spread"](), binding=binding, policy=UnknownArguments.IGNORE)

    schema = _input_schema(server)
    out = _call(server, {"title": "kept", "typo": "dropped"})

    assert set(schema.get("properties", {})) == {"title", "note"}
    assert schema["additionalProperties"] is True
    assert out.get("isError") is not True
    assert out["structuredContent"] == {"title": "kept", "note": ""}


@pytest.mark.django_db
@pytest.mark.parametrize("binding", _SPREAD)
def test_a_bare_var_keyword_keeps_the_schema_open_and_receives_every_key(
    binding: ArgumentBinding,
) -> None:
    # drf-services treats a spread service's bare ``**kwargs`` as open, so
    # ``REJECT`` has nothing to refuse against: the schema names what the
    # service names, stays open, and the extra reaches ``**changes``.
    server = _server(_SPECS["var-keyword"](), binding=binding, policy=UnknownArguments.REJECT)

    schema = _input_schema(server)
    out = _call(server, {"title": "t", "colour": "red"})

    assert set(schema.get("properties", {})) == {"title"}
    assert schema["additionalProperties"] is True
    assert out["structuredContent"]["title"] == "t"
    assert "colour" in out["structuredContent"]["changes"]
