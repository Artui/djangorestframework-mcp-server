"""A ``many=True`` service tool: its list travels under ``spec.many_argument``.

MCP ``arguments`` is always a JSON object, so the list a bulk service validates
cannot be the arguments themselves. Every service dispatch passes drf-services
``many_as_argument=True``, which reads the list out of that one argument, refuses
anything sent beside it, and keys every validation error under it with item
errors at their indexes. These tests hold each dispatch path to that, the listing
to advertising what the dispatch enforces, and the error wire to carrying the
index a client needs to find the item it sent.

Bindings are registered directly rather than through the adapter, so each test
exercises dispatch and listing whatever registration would have said.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest
from django.http import HttpRequest
from django.test import Client, override_settings
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.constants import JsonRpcErrorCode, UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.testing import assert_tool_result_conforms
from tests.testapp.serializers import InvoiceInputSerializer
from tests.testapp.urlconf_for import urlconf_for
from tests.utils import tool_error

_ROW: dict[str, Any] = {"number": "A-1", "amount_cents": 1}
_OTHER: dict[str, Any] = {"number": "A-2", "amount_cents": 2}
_BELOW_ZERO = "Ensure this value is greater than or equal to 0."


def _create(*, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Returns what it received, so a result equal to the rows sent proves the
    # service was handed the list itself rather than the arguments object.
    return [dict(item) for item in data]


def _spec(**overrides: Any) -> ServiceSpec[Any, Any, Any]:
    declared: dict[str, Any] = {
        "service": _create,
        "atomic": False,
        "many": True,
        "input_serializer": InvoiceInputSerializer,
        "output_selector_spec": SelectorSpec(
            kind=SelectorKind.RETRIEVE, output_serializer=InvoiceInputSerializer
        ),
        **overrides,
    }
    return ServiceSpec(**declared)


def _binding(spec: ServiceSpec[Any, Any, Any] | None = None, **kwargs: Any) -> ToolBinding:
    return ToolBinding(name="bulk", description=None, spec=spec or _spec(), **kwargs)


def _ctx(*bindings: ToolBinding) -> MCPCallContext:
    tools = ToolRegistry()
    for binding in bindings:
        tools.register(binding)
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


def _server(binding: ToolBinding, **config: Any) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        config=build_mcp_config(**config) if config else None,
    )
    server.tools.register(binding)
    return server


# ---------- the list reaches the service on every path ----------


def test_tools_call_hands_the_service_the_list() -> None:
    out: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [_ROW, _OTHER]}}, _ctx(_binding())
    )

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == [_ROW, _OTHER]


async def test_async_tools_call_hands_the_service_the_list() -> None:
    out: Any = await handle_tools_call_async(
        {"name": "bulk", "arguments": {"items": [_ROW, _OTHER]}}, _ctx(_binding())
    )

    assert out.get("isError") is not True, out
    assert out["structuredContent"] == [_ROW, _OTHER]


def test_call_tool_hands_the_service_the_list() -> None:
    result = _server(_binding()).call_tool("bulk", {"items": [_ROW, _OTHER]}, user=None)

    assert result.is_error is False
    assert result.structured_content == [_ROW, _OTHER]


def test_a_declared_argument_name_carries_the_list() -> None:
    binding = _binding(_spec(many_argument="rows"))

    out: Any = handle_tools_call({"name": "bulk", "arguments": {"rows": [_ROW]}}, _ctx(binding))

    assert out["structuredContent"] == [_ROW]


def _routed(*, data: list[dict[str, Any]], project: str, dry_run: str) -> list[dict[str, Any]]:
    return [{"number": item["number"], "project": project, "dry_run": dry_run} for item in data]


def _from_channels(*, view: Any, request: Any) -> dict[str, Any]:
    # Read off the two channels the values were routed to, not off the arguments.
    return {"project": view.kwargs["project_pk"], "dry_run": request.query_params["dry_run"]}


def test_url_kwargs_and_query_params_ride_beside_the_list() -> None:
    """Both are popped out of the arguments before dispatch, so they are not the
    arguments beside the list that dispatch refuses, and each reaches its channel."""
    binding = _binding(
        _spec(service=_routed, kwargs=_from_channels, output_selector_spec=None),
        url_kwargs=(UrlKwarg("project_pk"),),
        query_params=(QueryParam("dry_run"),),
    )

    out: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [_ROW], "project_pk": "7", "dry_run": "yes"}},
        _ctx(binding),
    )

    assert out["structuredContent"] == [{"number": "A-1", "project": "7", "dry_run": "yes"}]


# ---------- what dispatch refuses ----------


@pytest.mark.parametrize(
    "policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE, UnknownArguments.PASSTHROUGH]
)
def test_an_argument_beside_the_list_is_refused_whatever_the_policy(
    policy: UnknownArguments,
) -> None:
    out: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [_ROW], "note": "x"}},
        _ctx(_binding(unknown_arguments=policy)),
    )

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS
    assert out.data["detail"] == {"non_field_errors": ["Unexpected argument(s): 'note'."]}


def test_a_missing_list_is_refused_under_the_argument() -> None:
    out: Any = handle_tools_call({"name": "bulk", "arguments": {}}, _ctx(_binding()))

    assert isinstance(out, JsonRpcError)
    assert out.data["detail"] == {"items": ["This field is required."]}


def _post(server: MCPServer, method: str, params: dict[str, Any]) -> Any:
    headers: dict[str, str] = {"Mcp-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    if "name" in params:
        headers["Mcp-Name"] = params["name"]
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        response = Client().post(
            "/mcp/",
            data=json.dumps(
                {"jsonrpc": "2.0", "id": 1, "method": method, "params": {**params, "_meta": meta}}
            ),
            content_type="application/json",
            headers=headers,
        )
    assert response.status_code == 200, response.content
    return response.json()


def _post_tools_call(server: MCPServer, arguments: dict[str, Any]) -> Any:
    return _post(server, "tools/call", {"name": "bulk", "arguments": arguments})


def _resolve(detail: dict[str, Any], sent: dict[str, Any]) -> dict[str, Any]:
    """Walk every item error back into the arguments it names, as a client would.

    JSON object keys are strings, so an index arrives as ``"1"`` and a client turns
    it back into the position in the array it sent."""
    return {
        f"{argument}[{index}].{field}": sent[argument][int(index)][field]
        for argument, by_index in detail.items()
        for index, fields in by_index.items()
        for field in fields
    }


def test_an_invalid_item_is_served_at_its_index() -> None:
    """Read off the HTTP response rather than the handler's return value: the
    detail carries ``int`` keys until the encoder turns them into strings, and the
    echoed value is only useful if that path resolves into it."""
    arguments = {"items": [_ROW, {**_OTHER, "amount_cents": -1}]}
    body = _post_tools_call(_server(_binding(), include_validation_value=True), arguments)

    error = body["error"]
    assert error["code"] == JsonRpcErrorCode.INVALID_PARAMS
    assert error["data"]["detail"] == {"items": {"1": {"amount_cents": [_BELOW_ZERO]}}}
    assert _resolve(error["data"]["detail"], error["data"]["value"]) == {
        "items[1].amount_cents": -1
    }


def _refuse_second(*, data: list[dict[str, Any]]) -> None:
    # A bulk service naming the row it refuses in the shape dispatch gives item
    # errors, which is the natural thing for one to raise.
    raise ServiceValidationError({"items": {1: {"number": ["Already issued."]}}})


def test_a_service_refusing_an_item_by_index_is_served_as_a_tool_error() -> None:
    """The ``isError`` text is encoded with sorted keys, which an ``int`` key must
    survive, and the index must resolve into the echoed arguments there too."""
    arguments = {"items": [_ROW, _OTHER]}
    body = _post_tools_call(
        _server(_binding(_spec(service=_refuse_second)), include_validation_value=True),
        arguments,
    )

    error = tool_error(body["result"])
    assert error["type"] == "validation_error"
    assert error["detail"] == {"items": {"1": {"number": ["Already issued."]}}}
    assert _resolve(error["detail"], error["value"]) == {"items[1].number": "A-2"}


# ---------- the listing advertises what dispatch enforces ----------


def _listed_input_schema(binding: ToolBinding) -> dict[str, Any]:
    listed: Any = handle_tools_list({}, _ctx(binding))
    return listed["tools"][0]["inputSchema"]


_POLICIES = [
    pytest.param(UnknownArguments.REJECT, InvoiceInputSerializer, True, id="reject"),
    pytest.param(UnknownArguments.IGNORE, InvoiceInputSerializer, False, id="ignore"),
    pytest.param(UnknownArguments.PASSTHROUGH, InvoiceInputSerializer, False, id="passthrough"),
    pytest.param(UnknownArguments.REJECT, None, False, id="no-serializer"),
]


@pytest.mark.parametrize(("policy", "serializer", "_items_closed"), _POLICIES)
def test_the_arguments_are_closed_whatever_the_policy(
    policy: UnknownArguments, serializer: type | None, _items_closed: bool
) -> None:
    schema = _listed_input_schema(
        _binding(_spec(input_serializer=serializer), unknown_arguments=policy)
    )

    assert schema["additionalProperties"] is False


def _accept(*, data: Any = None) -> None:
    # ``data`` defaults because a serializer-less spec whose policy drops every key
    # is dispatched with no ``data`` at all.
    return None


@pytest.mark.parametrize(("policy", "serializer", "items_closed"), _POLICIES)
def test_each_item_is_closed_exactly_when_dispatch_refuses_an_unknown_key(
    policy: UnknownArguments, serializer: type | None, items_closed: bool
) -> None:
    """The policy governs the keys inside each item, so that is where it is
    advertised. Asserted against dispatch as well, so the schema and the refusal
    cannot come apart."""
    binding = _binding(
        _spec(service=_accept, input_serializer=serializer, output_selector_spec=None),
        unknown_arguments=policy,
    )

    schema = _listed_input_schema(binding)
    out: Any = handle_tools_call(
        {"name": "bulk", "arguments": {"items": [_ROW, {**_OTHER, "note": "x"}]}},
        _ctx(binding),
    )

    assert schema["properties"]["items"]["items"]["additionalProperties"] is (not items_closed)
    refused = isinstance(out, JsonRpcError)
    assert refused is items_closed
    if refused:
        assert out.data["detail"] == {
            "items": {1: {"non_field_errors": ["Unexpected argument(s): 'note'."]}}
        }


def test_a_fragment_replacing_the_properties_is_served_as_written() -> None:
    """``metadata["json_schema"]["input"]`` replaces the reflection's keys whole, so
    one declaring its own ``properties`` leaves no item schema to stamp. The listing
    serves the author's schema rather than failing every tool on the lookup."""
    declared: dict[str, Any] = {"rows": {"type": "array"}}
    binding = _binding(
        _spec(metadata={"json_schema": {"input": {"properties": declared}}}),
        unknown_arguments=UnknownArguments.REJECT,
    )

    schema = _listed_input_schema(binding)

    assert schema["properties"] == declared
    assert schema["additionalProperties"] is False


def test_arguments_that_dispatch_accepts_conform_to_the_advertised_schema() -> None:
    """The schema checked against a real validator in both directions: a call that
    succeeds conforms, and an argument beside the list, which dispatch refuses, does
    not."""
    schema = _listed_input_schema(
        _binding(url_kwargs=(UrlKwarg("project_pk"),), query_params=(QueryParam("dry_run"),))
    )

    jsonschema.validate({"items": [_ROW], "project_pk": "7", "dry_run": "yes"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"items": [_ROW], "note": "x"}, schema)


def test_the_served_list_conforms_to_the_advertised_output_schema() -> None:
    """Read off the wire. drf-services renders a list payload ``many=True``, so the
    output schema is an array; it said one object, taken from the ``RETRIEVE`` a
    bulk spec's output declaration carries by convention. ``structuredContent`` is
    the bare array, as for every unpaginated list result this server serves: the
    list is not wrapped in an object, and the text block encodes the same array."""
    server = _server(_binding(include_output_schema=True))

    listed: Any = _post(server, "tools/list", {})["result"]
    served: Any = _post_tools_call(server, {"items": [_ROW, _OTHER]})["result"]
    tool: Any = next(entry for entry in listed["tools"] if entry["name"] == "bulk")

    assert tool["outputSchema"]["type"] == "array"
    assert tool["outputSchema"]["items"]["properties"].keys() == {"number", "amount_cents"}
    assert served.get("isError") is not True, served
    assert served["structuredContent"] == [_ROW, _OTHER]
    assert json.loads(served["content"][0]["text"]) == [_ROW, _OTHER]
    assert_tool_result_conforms(tool, served)
