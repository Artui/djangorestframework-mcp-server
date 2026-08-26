"""Outbound bounds: page-size clamp, result-size ceiling, dispatch deadline.

The inbound side has been bounded since ``MAX_REQUEST_BYTES``; these are the
mirrors on the way out. Registration-time coverage for the unpaginated-list
warning lives in ``tests/server/test_unbounded_list_warning.py``.
"""

from __future__ import annotations

import asyncio
import warnings
from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.async_dispatch import adispatch
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.handle_prompts_get_async import handle_prompts_get_async
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import resolve_bound, run_with_deadline
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.utils import tool_error

# ---------- helpers ----------


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer, config: MCPConfig | None = None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
        config=config if config is not None else build_mcp_config(),
    )


def _list_invoices(*, user: Any) -> Any:
    return Invoice.objects.all()


def _register_list(server: MCPServer, **kwargs: Any) -> Any:
    """Register a paginated LIST selector, silencing the unrelated warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_invoices,
                output_serializer=InvoiceOutputSerializer,
            ),
            paginate=True,
            **kwargs,
        )


def _seed(count: int) -> None:
    for i in range(count):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=100 + i, sent=False)


def _call(server: MCPServer, arguments: dict[str, Any], config: MCPConfig | None = None) -> Any:
    return handle_tools_call(
        {"name": "invoices.list", "arguments": arguments}, _ctx(server, config)
    )


# ---------- page-size clamp ----------


@pytest.mark.django_db
def test_model_supplied_limit_is_clamped_to_the_server_ceiling() -> None:
    """A model asking for more rows than the server allows gets the ceiling."""
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 1000}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert len(payload["items"]) == 2
    # The clamp stays honest because the envelope says there is more: 5 rows
    # over a clamped limit of 2 is 3 pages, not 1.
    assert (payload["totalPages"], payload["hasNext"]) == (3, True)


@pytest.mark.django_db
def test_a_limit_under_the_ceiling_is_untouched() -> None:
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 3}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 3


@pytest.mark.django_db
def test_per_binding_ceiling_overrides_the_server() -> None:
    _seed(5)
    server = _server()
    _register_list(server, max_page_size=1)
    out = _call(server, {"limit": 50}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 1


@pytest.mark.django_db
def test_per_binding_none_disables_the_clamp_the_server_configured() -> None:
    """``None`` is a deliberate answer, not "unset" — this tool serves any limit."""
    _seed(5)
    server = _server()
    _register_list(server, max_page_size=None)
    out = _call(server, {"limit": 50}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 5


@pytest.mark.django_db
def test_server_ceiling_of_none_disables_the_clamp() -> None:
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 50}, build_mcp_config(max_page_size=None))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 5


def test_tools_list_advertises_the_effective_ceiling() -> None:
    """The model is told the bound, so it can ask for something serveable."""
    server = _server()
    _register_list(server, max_page_size=25)
    out = handle_tools_list(None, _ctx(server, build_mcp_config(max_page_size=500)))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["properties"]["limit"]["maximum"] == 25


def test_tools_list_omits_maximum_when_unbounded() -> None:
    server = _server()
    _register_list(server, max_page_size=None)
    out = handle_tools_list(None, _ctx(server, build_mcp_config(max_page_size=500)))
    assert isinstance(out, dict)
    assert "maximum" not in out["tools"][0]["inputSchema"]["properties"]["limit"]


# ---------- the page ceiling on an unpaginated list ----------
#
# The ceiling is the most rows one selector-tool result carries, paged or not.
# Only the enforcement differs by arm: a page clamps and says so in its
# envelope, while an unpaginated result — which carries nothing that could say
# rows were dropped — refuses, exactly as the byte ceiling does.


def _register_unpaginated(server: MCPServer, **kwargs: Any) -> Any:
    """The same LIST tool, registered with the default ``paginate=False``."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_invoices,
                output_serializer=InvoiceOutputSerializer,
            ),
            **kwargs,
        )


@pytest.mark.django_db
def test_unpaginated_list_over_the_ceiling_is_refused() -> None:
    """Previously the whole table was fetched, serialised and returned."""
    _seed(5)
    server = _server()
    _register_unpaginated(server)
    out = _call(server, {}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    message = tool_error(out)["message"]
    assert "2 row ceiling" in message
    # Same contract as the byte ceiling: refuse, never truncate.
    assert "not truncated" in message
    assert tool_error(out)["type"] == "result_too_large"


@pytest.mark.django_db
def test_a_result_exactly_at_the_ceiling_is_served_whole() -> None:
    """The boundary is inclusive — the ceiling is a servable result."""
    _seed(2)
    server = _server()
    _register_unpaginated(server)
    out = _call(server, {}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 2


@pytest.mark.django_db
def test_the_ceiling_bounds_the_fetch_and_not_just_the_payload(
    django_assert_num_queries: Any,
) -> None:
    """The rows are bounded in SQL, so the table is never in memory.

    This is what the byte ceiling cannot do: ``MAX_RESULT_BYTES`` measures a
    payload that has already been fetched and rendered in full.
    """
    _seed(5)
    server = _server()
    _register_unpaginated(server)
    with django_assert_num_queries(1) as captured:
        _call(server, {}, build_mcp_config(max_page_size=2))
    # Ceiling + 1, the one extra row being how "over" is told from "exactly at".
    assert "LIMIT 3" in captured.captured_queries[0]["sql"]


@pytest.mark.django_db
def test_per_binding_none_serves_an_unpaginated_list_unbounded() -> None:
    """The existing opt-out covers the new arm: a tool can still be unbounded."""
    _seed(5)
    server = _server()
    _register_unpaginated(server, max_page_size=None)
    out = _call(server, {}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 5


@pytest.mark.django_db
def test_server_ceiling_of_none_serves_an_unpaginated_list_unbounded() -> None:
    _seed(5)
    server = _server()
    _register_unpaginated(server)
    out = _call(server, {}, build_mcp_config(max_page_size=None))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]) == 5


@pytest.mark.django_db
def test_a_non_orm_list_result_is_bounded_by_the_same_ceiling() -> None:
    """A selector returning a plain sequence is bounded without a queryset."""

    def _rows(*, user: Any) -> Any:  # noqa: ARG001
        return [{"number": f"N-{i}"} for i in range(5)]

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
        )
    out = _call(server, {}, build_mcp_config(max_page_size=2))
    assert isinstance(out, dict)
    assert "2 row ceiling" in tool_error(out)["message"]


@pytest.mark.django_db(transaction=True)
async def test_async_path_enforces_the_same_row_ceiling() -> None:
    from asgiref.sync import sync_to_async

    await sync_to_async(_seed)(5)
    server = _server()
    await sync_to_async(_register_unpaginated)(server)
    out = await handle_tools_call_async(
        {"name": "invoices.list", "arguments": {}},
        _ctx(server, build_mcp_config(max_page_size=2)),
    )
    assert isinstance(out, dict)
    assert "2 row ceiling" in tool_error(out)["message"]


# ---------- the page argument's upper clamp ----------


@pytest.mark.django_db
def test_a_page_past_the_end_clamps_to_the_last_page() -> None:
    """An unclamped ``page`` becomes an arbitrarily large SQL OFFSET.

    A backend either scans towards it or rejects it outright with a
    ``DatabaseError`` that is neither of the two exceptions this path catches,
    so it escaped as an unhandled 500. Clamping is the treatment the low end
    already had, and the envelope reports the page actually served.
    """
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"page": 10**20, "limit": 2}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert (payload["page"], payload["totalPages"], payload["hasNext"]) == (3, 3, False)
    assert [item["number"] for item in payload["items"]] == ["INV-4"]


@pytest.mark.django_db
def test_a_page_inside_the_range_is_untouched() -> None:
    _seed(5)
    server = _server()
    _register_list(server)
    out = _call(server, {"page": 2, "limit": 2}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert (payload["page"], payload["hasNext"]) == (2, True)
    assert [item["number"] for item in payload["items"]] == ["INV-2", "INV-3"]


@pytest.mark.django_db
def test_a_page_past_the_end_of_an_empty_result_stays_at_one() -> None:
    """Nothing to clamp to: page 1 of 1, empty, rather than page 0."""
    server = _server()
    _register_list(server)
    out = _call(server, {"page": 500}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert (payload["page"], payload["totalPages"], payload["items"]) == (1, 1, [])


@pytest.mark.django_db
def test_an_in_memory_paginated_result_clamps_its_page_too() -> None:
    """The sequence arm shares the clamp with the queryset arm."""

    def _rows(*, user: Any) -> Any:  # noqa: ARG001
        return [{"number": f"N-{i}"} for i in range(5)]

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_selector_tool(
            name="invoices.list",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_rows),
            paginate=True,
        )
    out = _call(server, {"page": 99, "limit": 2}, build_mcp_config(max_page_size=100))
    assert isinstance(out, dict)
    payload = out["structuredContent"]
    assert (payload["page"], payload["items"]) == (3, [{"number": "N-4"}])


# ---------- result-size ceiling ----------


@pytest.mark.django_db
def test_oversized_tool_result_becomes_an_actionable_error() -> None:
    """Over the ceiling the call fails loudly — it is never truncated."""
    _seed(20)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 20}, build_mcp_config(max_result_bytes=200))
    assert isinstance(out, dict)
    message = tool_error(out)["message"]
    assert "over this server's 200 byte ceiling" in message
    assert "not truncated" in message
    # No partial payload came back alongside the error.
    assert "items" not in str(out.get("structuredContent", ""))


@pytest.mark.django_db
def test_result_under_the_ceiling_is_returned_whole() -> None:
    _seed(3)
    server = _server()
    _register_list(server)
    out = _call(server, {"limit": 10}, build_mcp_config(max_result_bytes=1_000_000))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 3


@pytest.mark.django_db
def test_per_binding_result_ceiling_overrides_the_server() -> None:
    _seed(20)
    server = _server()
    _register_list(server, max_result_bytes=None)
    out = _call(server, {"limit": 20}, build_mcp_config(max_result_bytes=200))
    assert isinstance(out, dict)
    assert len(out["structuredContent"]["items"]) == 20


class _DenyAll(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


@pytest.mark.django_db
def test_a_protocol_error_passes_through_the_ceiling_untouched() -> None:
    """A JSON-RPC error keeps its envelope — it is not rewritten as a result."""
    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_selector_tool(
            name="invoices.denied",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_list_invoices,
                output_serializer=InvoiceOutputSerializer,
                permission_classes=[_DenyAll],
            ),
            paginate=True,
        )
    out = handle_tools_call(
        {"name": "invoices.denied", "arguments": {}},
        _ctx(server, build_mcp_config(max_result_bytes=1)),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db(transaction=True)
async def test_async_path_enforces_the_same_ceiling() -> None:
    from asgiref.sync import sync_to_async

    await sync_to_async(_seed)(20)
    server = _server()
    await sync_to_async(_register_list)(server)
    out = await handle_tools_call_async(
        {"name": "invoices.list", "arguments": {"limit": 20}},
        _ctx(server, build_mcp_config(max_result_bytes=200)),
    )
    assert isinstance(out, dict)
    assert "byte ceiling" in tool_error(out)["message"]


def _big_resource() -> dict[str, Any]:
    return {"rows": ["x" * 100 for _ in range(50)]}


def _register_big_resource(server: MCPServer) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_resource(
            name="big",
            uri_template="big://all",
            selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_big_resource),
        )


def test_oversized_resource_read_is_a_protocol_error() -> None:
    """A resource read has no ``isError`` envelope, so the ceiling errors out."""
    server = _server()
    _register_big_resource(server)
    out = handle_resources_read(
        {"uri": "big://all"}, _ctx(server, build_mcp_config(max_result_bytes=200))
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert "byte ceiling" in out.message
    assert "Resource 'big://all'" in out.message


def test_resource_read_under_the_ceiling_is_returned() -> None:
    server = _server()
    _register_big_resource(server)
    out = handle_resources_read(
        {"uri": "big://all"}, _ctx(server, build_mcp_config(max_result_bytes=1_000_000))
    )
    assert isinstance(out, dict)
    assert out["contents"][0]["uri"] == "big://all"


async def test_async_resource_read_enforces_the_same_ceiling() -> None:
    server = _server()
    _register_big_resource(server)
    out = await handle_resources_read_async(
        {"uri": "big://all"}, _ctx(server, build_mcp_config(max_result_bytes=200))
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR


# ---------- result-size ceiling: prompts/get ----------


def _register_big_prompt(server: MCPServer) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_prompt(name="big", render=lambda **_: "x" * 5_000)


def test_oversized_prompt_render_is_a_protocol_error() -> None:
    """A rendered prompt is a result surface like any other. It used to be the
    one method the ceiling skipped, so an operator who set MAX_RESULT_BYTES
    believed every surface was bounded while this one was not."""
    server = _server()
    _register_big_prompt(server)
    out = handle_prompts_get({"name": "big"}, _ctx(server, build_mcp_config(max_result_bytes=200)))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert "byte ceiling" in out.message
    assert "Prompt 'big'" in out.message


def test_prompt_render_under_the_ceiling_is_returned() -> None:
    server = _server()
    _register_big_prompt(server)
    out = handle_prompts_get(
        {"name": "big"}, _ctx(server, build_mcp_config(max_result_bytes=1_000_000))
    )
    assert isinstance(out, dict)
    assert out["messages"][0]["content"]["text"].startswith("xxx")


async def test_async_prompt_render_enforces_the_same_ceiling() -> None:
    server = _server()
    _register_big_prompt(server)
    out = await handle_prompts_get_async(
        {"name": "big"}, _ctx(server, build_mcp_config(max_result_bytes=200))
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR


# ---------- dispatch deadline ----------


def _slow_selector(*, user: Any) -> Any:
    """A selector that outlives any deadline a test would set."""
    import time

    time.sleep(0.5)
    return []


def _register_slow(server: MCPServer, **kwargs: Any) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return server.register_selector_tool(
            name="invoices.slow",
            spec=SelectorSpec(
                kind=SelectorKind.LIST,
                selector=_slow_selector,
                output_serializer=InvoiceOutputSerializer,
            ),
            paginate=True,
            **kwargs,
        )


@pytest.mark.django_db(transaction=True)
async def test_overrunning_tool_call_returns_a_terminal_error() -> None:
    """The client gets an answer instead of an open request that never resolves."""
    server = _server()
    _register_slow(server)
    out = await handle_tools_call_async(
        {"name": "invoices.slow", "arguments": {}},
        _ctx(server, build_mcp_config(dispatch_timeout=0.01)),
    )
    assert isinstance(out, dict)
    message = tool_error(out)["message"]
    # The message is honest that the work may still be running: cancelling the
    # task does not interrupt a thread parked in the DB driver.
    assert "dispatch deadline" in message
    assert "may still be running" in message


@pytest.mark.django_db(transaction=True)
async def test_per_binding_deadline_overrides_the_server() -> None:
    server = _server()
    _register_slow(server, dispatch_timeout=None)
    out = await handle_tools_call_async(
        {"name": "invoices.slow", "arguments": {}},
        _ctx(server, build_mcp_config(dispatch_timeout=0.01)),
    )
    assert isinstance(out, dict)
    # No deadline for this tool: it ran to completion despite the server's.
    assert out["structuredContent"]["items"] == []


@pytest.mark.django_db(transaction=True)
async def test_resources_read_deadline_is_a_protocol_error() -> None:
    """A resource has no ``isError`` envelope, so expiry is a JSON-RPC error."""

    def _slow_resource() -> Any:
        import time

        time.sleep(0.5)
        return {"ok": True}

    server = _server()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        server.register_resource(
            name="slow",
            uri_template="slow://all",
            selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_slow_resource),
        )
    out = await adispatch(
        "resources/read",
        {"uri": "slow://all"},
        _ctx(server, build_mcp_config(dispatch_timeout=0.01)),
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.SERVER_ERROR
    assert "dispatch deadline" in out.message


@pytest.mark.django_db(transaction=True)
async def test_tools_call_is_not_double_wrapped_by_the_dispatcher() -> None:
    """``adispatch`` leaves tools/call alone — it resolves its own deadline."""
    server = _server()
    _register_slow(server, dispatch_timeout=None)
    out = await adispatch(
        "tools/call",
        {"name": "invoices.slow", "arguments": {}},
        _ctx(server, build_mcp_config(dispatch_timeout=0.01)),
    )
    # A second timer at the dispatcher would have fired here regardless of the
    # binding's opt-out.
    assert isinstance(out, dict)
    assert out["structuredContent"]["items"] == []


async def test_run_with_deadline_without_a_deadline_awaits_normally() -> None:
    async def _work() -> str:
        return "done"

    assert await run_with_deadline(_work(), None) == "done"


async def test_run_with_deadline_raises_past_the_deadline() -> None:
    async def _work() -> str:
        await asyncio.sleep(0.5)
        return "done"  # pragma: no cover - the deadline fires first

    with pytest.raises(asyncio.TimeoutError):
        await run_with_deadline(_work(), 0.01)


# ---------- the UNSET / None distinction ----------


def test_resolve_bound_treats_unset_as_defer_and_none_as_disabled() -> None:
    from rest_framework_services import UNSET

    assert resolve_bound(UNSET, 500) == 500
    assert resolve_bound(None, 500) is None
    assert resolve_bound(10, 500) == 10


def test_build_mcp_config_distinguishes_omitted_from_disabled() -> None:
    assert build_mcp_config().max_page_size == 100
    assert build_mcp_config(max_page_size=None).max_page_size is None
    assert build_mcp_config(max_page_size=10).max_page_size == 10
    assert build_mcp_config(max_result_bytes=None).max_result_bytes is None
    assert build_mcp_config(max_result_bytes=7).max_result_bytes == 7
    assert build_mcp_config(dispatch_timeout=None).dispatch_timeout is None
    assert build_mcp_config(dispatch_timeout=1.5).dispatch_timeout == 1.5
    assert build_mcp_config().dispatch_timeout == 60.0
    assert build_mcp_config().max_result_bytes == 5_242_880
