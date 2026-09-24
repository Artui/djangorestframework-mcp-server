"""A read-shaping ``QueryParam`` refused while the result is rendered.

A field-selection param (django-restql's ``query``) is read by the *output
serializer*, one row at a time, after ``dispatch_spec`` has returned. Every
``except`` that decides whether a failure is the caller's wrapped only the
dispatch, so a selection the serializer refused escaped as whatever the
transport made of an unhandled exception: a bare DRF body in JSON mode (not a
JSON-RPC response at all), a ``-32603`` in a stream, a raised ``ValidationError``
from ``acall_tool``. On a paged tool the likeliest bad selection is the
envelope, ``{items{id, number}}``, because that is exactly the shape the tool's
``outputSchema`` shows.

It is now the caller's ``isError`` + ``validation_error`` whenever the caller
supplied a read-shaping value on the call, and still raises when they did not:
with nothing caller-controlled in play the model cannot change the outcome, so it
is a server bug and must stay loud.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest
from asgiref.sync import sync_to_async
from django.test import AsyncClient, Client, override_settings
from django_restql.mixins import DynamicFieldsMixin
from rest_framework import serializers
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, QueryParam
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.constants import TaskStatus
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.utils import read_shaping_error_result
from rest_framework_mcp.schema.agent_conventions import PAGED_QUERY_PARAM_SCOPE
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.conftest import post_jsonrpc
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.models import Invoice
from tests.testapp.urlconf_for import urlconf_for

ENVELOPE_SELECTION = "{items{id, number}}"
REFUSED = "`items` field is not found"
PAGED_MESSAGE = (
    f"`query` was rejected while rendering the result: {REFUSED}. {PAGED_QUERY_PARAM_SCOPE}"
)

# ---------- serializers ----------


def _top_level_names(selection: str) -> list[str]:
    """The top-level names of a restql-style ``{a, b{c}}`` selection."""
    names: list[str] = []
    depth = 0
    current = ""
    for char in selection.strip()[1:-1]:
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        elif char == "," and depth == 0:
            names.append(current.strip())
            current = ""
        elif depth == 0:
            current += char
    names.append(current.strip())
    return [name for name in names if name]


class StrictSelectionInvoice(serializers.ModelSerializer):
    """Stand-in for django-restql's strict (upstream default) selection.

    Raises exactly what django-restql 0.18.0 raises for a name the serializer
    does not have — ``ValidationError("`x` field is not found",
    code="not_found")``, from ``to_representation`` — and
    ``test_the_double_raises_what_restql_raises`` holds it to that producer.
    Top-level names only, which is all these tests select.
    """

    class Meta:
        model = Invoice
        fields = ["id", "number"]

    def to_representation(self, instance: Any) -> Any:
        data = super().to_representation(instance)
        raw = self.context["request"].query_params.get("query")
        if not raw:
            return data
        wanted = _top_level_names(raw)
        for name in wanted:
            if name not in data:
                raise serializers.ValidationError(f"`{name}` field is not found", code="not_found")
        return {key: value for key, value in data.items() if key in wanted}


class RestqlInvoice(DynamicFieldsMixin, serializers.ModelSerializer):
    """Real django-restql, strict as it ships."""

    class Meta:
        model = Invoice
        fields = ["id", "number"]


class AlwaysRefuses(serializers.ModelSerializer):
    """A render that fails whatever the caller sent: a server bug, not theirs."""

    class Meta:
        model = Invoice
        fields = ["id", "number"]

    def to_representation(self, instance: Any) -> Any:
        raise serializers.ValidationError("misconfigured serializer")


# ---------- servers ----------

_QUERY = QueryParam("query", description="django-restql fieldset, e.g. {id, number}")


def _new_server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=build_mcp_config(**config) if config else None,
    )


def _selector_server(
    serializer: type = StrictSelectionInvoice,
    *,
    kind: SelectorKind = SelectorKind.LIST,
    paginate: bool = True,
    query_params: tuple[QueryParam, ...] = (_QUERY,),
    **config: Any,
) -> MCPServer:
    server = _new_server(**config)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_selector_tool(
            name="invoices.list",
            description="List invoices.",
            spec=SelectorSpec(
                kind=kind,
                selector=lambda: Invoice.objects.order_by("pk"),
                output_serializer=serializer,
            ),
            paginate=paginate,
            query_params=query_params,
        )
    return server


def _first_invoice() -> Invoice:
    return Invoice.objects.order_by("pk").first()


def _service_server(
    serializer: type = StrictSelectionInvoice,
    *,
    query_params: tuple[QueryParam, ...] = (_QUERY,),
    **config: Any,
) -> MCPServer:
    server = _new_server(**config)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_service_tool(
            name="invoices.touch",
            description="Touch an invoice.",
            spec=ServiceSpec(
                service=_first_invoice,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=serializer
                ),
            ),
            query_params=query_params,
        )
    return server


def _seed() -> None:
    Invoice.objects.create(number="A")
    Invoice.objects.create(number="B")


def _error(result: dict[str, Any]) -> dict[str, Any]:
    """The ``error`` object an ``isError`` result carries in ``content[0]``."""
    assert result.get("isError") is True, result
    return json.loads(result["content"][0]["text"])["error"]


def _call(server: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    return handle_tools_call(
        {"name": name, "arguments": arguments}, server._call_context(user=None)
    )


async def _acall(server: MCPServer, name: str, arguments: dict[str, Any]) -> Any:
    return await handle_tools_call_async(
        {"name": name, "arguments": arguments}, server._call_context(user=None)
    )


# ---------- the wire: JSON response mode, both transports ----------


def _session(client: Client) -> str:
    response = post_jsonrpc(
        client,
        method="initialize",
        params={
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "0.0"},
        },
        protocol_version=None,
    )
    assert response.status_code == 200, response.content
    return response["Mcp-Session-Id"]


def _post_call(server: MCPServer, arguments: dict[str, Any], *, is_async: bool) -> Any:
    client = Client(raise_request_exception=False)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        return post_jsonrpc(
            client,
            session_id=_session(client),
            method="tools/call",
            params={"name": "invoices.list", "arguments": arguments},
        )


@pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
@pytest.mark.django_db(transaction=True)
def test_json_mode_answers_an_envelope_selection_with_a_validation_result(
    is_async: bool,
) -> None:
    """The reported case: a JSON-RPC response carrying the caller's ``isError``.

    Before, HTTP 400 whose body was DRF's ``["`items` field is not found"]`` —
    no ``jsonrpc``, no ``id``, nothing a spec-compliant client could parse.
    """
    _seed()
    response = _post_call(_selector_server(), {"query": ENVELOPE_SELECTION}, is_async=is_async)

    assert response.status_code == 200, response.content
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 1
    error = _error(body["result"])
    assert error == {
        "type": "validation_error",
        "message": PAGED_MESSAGE,
        "detail": {"query": [REFUSED]},
    }


@pytest.mark.django_db
def test_real_restql_envelope_selection_is_a_validation_result() -> None:
    """The producer itself, not the double, through the sync transport."""
    _seed()
    response = _post_call(
        _selector_server(RestqlInvoice), {"query": ENVELOPE_SELECTION}, is_async=False
    )

    assert response.status_code == 200, response.content
    error = _error(response.json()["result"])
    assert error["type"] == "validation_error"
    assert error["message"] == PAGED_MESSAGE
    assert error["detail"] == {"query": [REFUSED]}


@pytest.mark.django_db
def test_a_row_selection_still_renders() -> None:
    """The per-item selection the scope sentence points at is the one that works."""
    _seed()
    response = _post_call(_selector_server(RestqlInvoice), {"query": "{number}"}, is_async=False)

    result = response.json()["result"]
    assert result.get("isError") in (None, False)
    assert result["structuredContent"]["items"] == [{"number": "A"}, {"number": "B"}]


def test_the_double_raises_what_restql_raises() -> None:
    """The stand-in is held to django-restql 0.18.0: same message, same code."""
    request = Request(APIRequestFactory().get("/", {"query": ENVELOPE_SELECTION}))
    instance = Invoice(pk=1, number="A")

    raised: list[Any] = []
    for serializer_class in (RestqlInvoice, StrictSelectionInvoice):
        with pytest.raises(serializers.ValidationError) as info:
            _ = serializer_class(instance, context={"request": request}).data
        raised.append(info.value.detail)

    restql, double = raised
    assert double == restql
    assert [detail.code for detail in double] == [detail.code for detail in restql]
    assert restql[0].code == "not_found"


# ---------- the wire: streamed mode ----------


@pytest.mark.django_db(transaction=True)
async def test_streamed_mode_answers_with_the_same_result() -> None:
    """A ``progressToken`` makes the reply SSE; the result rides in the stream.

    Before, an in-stream ``-32603`` reading ``ValidationError: [ErrorDetail(...)]``.
    """
    await sync_to_async(_seed)()
    server = _selector_server()
    meta = {
        "progressToken": "p1",
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    params = {"name": "invoices.list", "arguments": {"query": ENVELOPE_SELECTION}, "_meta": meta}
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=True)):
        response = await AsyncClient().post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}),
            content_type="application/json",
            headers={
                "Mcp-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "invoices.list",
            },
        )
        assert response.streaming
        body = b"".join([chunk async for chunk in response.streaming_content]).decode()

    frames = [
        json.loads(line[len("data: ") :]) for line in body.splitlines() if line.startswith("data: ")
    ]
    final = frames[-1]
    assert "error" not in final, final
    error = _error(final["result"])
    assert error["type"] == "validation_error"
    assert error["message"] == PAGED_MESSAGE


# ---------- in process ----------


@pytest.mark.django_db(transaction=True)
async def test_acall_tool_returns_the_result_instead_of_raising() -> None:
    """django-pydantic-agent's toolset route, which maps this result to a retry."""
    await sync_to_async(_seed)()
    result = await _selector_server().acall_tool(
        "invoices.list", {"query": ENVELOPE_SELECTION}, user=None
    )

    error = _error(result)
    assert error["type"] == "validation_error"
    assert error["message"] == PAGED_MESSAGE
    assert error["detail"] == {"query": [REFUSED]}


@pytest.mark.django_db
def test_call_tool_returns_the_result_without_the_page_sentence() -> None:
    """``call_tool`` never pages, so there is no envelope to explain."""
    _seed()
    result = _selector_server().call_tool("invoices.list", {"query": "{bogus}"}, user=None)

    assert result.is_error is True
    error = json.loads(result.content[0].text)["error"]
    assert error == {
        "type": "validation_error",
        "message": "`query` was rejected while rendering the result: `bogus` field is not found.",
        "detail": {"query": ["`bogus` field is not found"]},
    }


@pytest.mark.parametrize(
    ("kind", "paginate"),
    [(SelectorKind.RETRIEVE, False), (SelectorKind.LIST, False)],
    ids=["retrieve", "unpaginated-list"],
)
@pytest.mark.django_db
def test_an_unpaged_selector_tool_gets_no_page_sentence(kind: SelectorKind, paginate: bool) -> None:
    _seed()
    result = _call(
        _selector_server(kind=kind, paginate=paginate), "invoices.list", {"query": "{bogus}"}
    )

    assert _error(result)["message"] == (
        "`query` was rejected while rendering the result: `bogus` field is not found."
    )


@pytest.mark.django_db
def test_a_service_tool_sync() -> None:
    _seed()
    result = _call(_service_server(), "invoices.touch", {"query": "{bogus}"})

    error = _error(result)
    assert error["type"] == "validation_error"
    assert error["message"] == (
        "`query` was rejected while rendering the result: `bogus` field is not found."
    )
    assert error["detail"] == {"query": ["`bogus` field is not found"]}


@pytest.mark.django_db(transaction=True)
async def test_a_service_tool_async() -> None:
    await sync_to_async(_seed)()
    result = await _acall(_service_server(), "invoices.touch", {"query": "{bogus}"})

    error = _error(result)
    assert error["type"] == "validation_error"
    assert error["detail"] == {"query": ["`bogus` field is not found"]}


@pytest.mark.django_db
def test_a_task_augmented_call_stores_the_result_rather_than_failing() -> None:
    """The worker reaches the render through ``handle_tools_call``.

    Before, the escaping ``ValidationError`` failed the task with "The task
    raised an unhandled exception"; the model's mistake is a completed call
    whose result says what to fix.
    """
    _seed()
    store = InMemoryTaskStore()
    executor = RecordingExecutor(store)
    server = MCPServer(
        name="tasks",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        task_store=store,
        task_executor=executor,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_service_tool(
            name="invoices.touch",
            description="Touch an invoice.",
            spec=ServiceSpec(
                service=_first_invoice,
                atomic=False,
                output_selector_spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE, output_serializer=StrictSelectionInvoice
                ),
            ),
            query_params=(_QUERY,),
        )
    task = create_task(
        store=store,
        executor=executor,
        tool_name="invoices.touch",
        arguments={"query": "{bogus}"},
        token=TokenInfo(user=None),
        ttl_ms=60_000,
        poll_interval_ms=500,
    )
    server.run_task(task.task_id)

    record = store.get(task.task_id)
    assert record.status is TaskStatus.COMPLETED
    assert _error(record.task.result)["type"] == "validation_error"


# ---------- only what the caller shaped is theirs ----------


@pytest.mark.django_db
def test_render_error_with_nothing_supplied_still_raises() -> None:
    """No read-shaping value on the call: nothing the model sends can fix it.

    It escapes the handler exactly as before; what the transport then makes of
    an escaping exception is the viewset's concern, not this classification's.
    """
    _seed()
    with pytest.raises(serializers.ValidationError, match="misconfigured serializer"):
        _call(_selector_server(AlwaysRefuses), "invoices.list", {})


@pytest.mark.django_db(transaction=True)
async def test_render_error_with_nothing_supplied_still_raises_async() -> None:
    await sync_to_async(_seed)()
    with pytest.raises(serializers.ValidationError, match="misconfigured serializer"):
        await _acall(_service_server(AlwaysRefuses), "invoices.touch", {})


@pytest.mark.django_db
def test_render_error_with_nothing_supplied_still_raises_from_call_tool() -> None:
    _seed()
    with pytest.raises(serializers.ValidationError, match="misconfigured serializer"):
        _service_server(AlwaysRefuses).call_tool("invoices.touch", {}, user=None)


@pytest.mark.django_db
def test_a_seeded_default_is_not_supplied() -> None:
    """A ``QueryParam.default`` is seeded when the caller omits the argument.

    That value was never sent, so a bad one is a configuration bug and stays
    loud. Reading "supplied" off the routed values would call it the caller's.
    """
    _seed()
    seeded = QueryParam("query", default=ENVELOPE_SELECTION)
    server = _selector_server(query_params=(seeded,))

    with pytest.raises(serializers.ValidationError):
        _call(server, "invoices.list", {})
    # The same value, *sent*, is the caller's to fix.
    assert _error(_call(server, "invoices.list", {"query": ENVELOPE_SELECTION}))["detail"] == {
        "query": [REFUSED]
    }


@pytest.mark.django_db
def test_an_explicit_null_is_not_supplied() -> None:
    """``{"query": null}`` is how a model says it chose not to fill the param."""
    _seed()
    with pytest.raises(serializers.ValidationError, match="misconfigured serializer"):
        _call(_selector_server(AlwaysRefuses), "invoices.list", {"query": None})


@pytest.mark.django_db
def test_an_explicit_null_on_a_strict_restql_tool_renders_unselected() -> None:
    """A declined param must not reach restql as the string ``None``.

    If it did, restql would refuse it as a malformed selection, and because a
    null is not the caller's value that refusal would escape as a server fault
    for a call that asked for nothing.
    """
    _seed()
    result = _call(_selector_server(RestqlInvoice), "invoices.list", {"query": None})

    assert result.get("isError") in (None, False), result
    assert [row["number"] for row in result["structuredContent"]["items"]] == ["A", "B"]


@pytest.mark.django_db
def test_a_non_validation_error_is_never_the_callers() -> None:
    """An ``AttributeError`` in a serializer is a bug whatever the caller sent."""

    class Broken(serializers.ModelSerializer):
        class Meta:
            model = Invoice
            fields = ["id"]

        def to_representation(self, instance: Any) -> Any:
            raise AttributeError("no such thing")

    _seed()
    with pytest.raises(AttributeError):
        _call(_selector_server(Broken), "invoices.list", {"query": "{id}"})


# ---------- detail keying ----------


_TWO_PARAMS = (QueryParam("fields"), QueryParam("query"))


@pytest.mark.django_db
def test_one_supplied_name_keys_the_detail_under_that_name() -> None:
    """Two declared, one sent: the one sent is the one the render read."""
    _seed()
    result = _call(
        _selector_server(AlwaysRefuses, paginate=False, query_params=_TWO_PARAMS),
        "invoices.list",
        {"fields": "id"},
    )

    error = _error(result)
    assert error["detail"] == {"fields": ["misconfigured serializer"]}
    assert error["message"].startswith("`fields` was rejected")


@pytest.mark.django_db
def test_several_supplied_names_key_under_non_field_errors_and_are_all_named() -> None:
    """Nothing says which one the serializer refused, so none is singled out."""
    _seed()
    result = _call(
        _selector_server(AlwaysRefuses, paginate=False, query_params=_TWO_PARAMS),
        "invoices.list",
        {"fields": "id", "query": "{id}"},
    )

    error = _error(result)
    assert error["detail"] == {"non_field_errors": ["misconfigured serializer"]}
    assert error["message"] == (
        "`fields` or `query` was rejected while rendering the result: misconfigured serializer."
    )


# ---------- the helper's own shapes ----------


_ONE = (QueryParam("query"),)


def _helper_error(
    exc: serializers.ValidationError | ServiceValidationError,
    arguments: dict[str, Any],
    **config: Any,
) -> dict[str, Any]:
    result = read_shaping_error_result(
        exc,
        query_params=_ONE,
        arguments=arguments,
        paginated=False,
        config=build_mcp_config(**config),
    )
    return _error(result.to_dict())


def test_a_service_validation_error_string_becomes_a_one_item_list() -> None:
    error = _helper_error(ServiceValidationError("Unknown field: bogus."), {"query": "bogus"})

    assert error["detail"] == {"query": ["Unknown field: bogus."]}
    # Already a sentence, so no second full stop.
    assert error["message"] == (
        "`query` was rejected while rendering the result: Unknown field: bogus."
    )


def test_a_per_field_detail_is_read_as_prose_not_a_repr() -> None:
    exc = serializers.ValidationError({"number": ["Not selectable", "Hidden"]})

    error = _helper_error(exc, {"query": "{number}"})

    assert error["detail"] == {"query": {"number": ["Not selectable", "Hidden"]}}
    assert error["message"] == (
        "`query` was rejected while rendering the result: `number`: Not selectable. Hidden."
    )
    assert "ErrorDetail" not in error["message"]


def test_the_value_is_echoed_only_when_the_server_opts_in() -> None:
    exc = serializers.ValidationError("nope")

    assert "value" not in _helper_error(exc, {"query": "x"})
    echoed = _helper_error(exc, {"query": "x"}, include_validation_value=True)
    assert echoed["value"] == {"query": "x"}
