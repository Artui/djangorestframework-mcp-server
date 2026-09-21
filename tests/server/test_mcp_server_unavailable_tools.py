"""``MCPServer.unavailable_tools`` and ``list_tools(include_unavailable=True)``.

The two halves of what an in-process consumer needs to keep a model's tools in
step with ``tools/list`` without re-listing every step: every definition once,
and then, per step, which of them a fresh listing would leave out and why.

What these hold is agreement. A name is reported exactly when ``list_tools``
omits it, with the condition that omitted it; a tool this caller may not see is
never named; ``always_listed`` is never reported; and the async twin answers as
the sync one does, taking no executor hop when no tool declares anything to ask.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.db.models import Q
from rest_framework_services import base_pool
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from tests.testapp.affordances import ARCHIVE_ORDER, order_selector_spec
from tests.testapp.models import Invoice

_POOL = "rest_framework_mcp.handlers.offered_tools.base_pool"
"""Where a pass looks ``base_pool`` up, so a spy there sees every pool it builds."""

_HOP = "rest_framework_mcp.server.mcp_server.sync_to_async"
"""Where the server looks ``sync_to_async`` up, so a spy there sees every hop."""

_CLOSED = Affordance(code="books_closed", reason="The books are closed.", when=lambda: False)
_FROZEN = Affordance(code="ledger_frozen", reason="The ledger is frozen.", when=lambda: False)
_OPEN = Affordance(code="books_open", reason="The books are open.", when=lambda: True)
_ROW_ONLY = Affordance(code="never", reason="Matches no row.", when=Q(pk__in=[]))


class _Deny:
    def has_permission(self, request: Any, token: Any) -> bool:  # noqa: ARG002
        return False

    def required_scopes(self) -> list[str]:
        return []


def _service(*affordances: Affordance) -> ServiceSpec[Any, Any, Any]:
    return ServiceSpec(
        service=lambda **_: {"status": "ran"},
        atomic=False,
        affordances=list(affordances) or None,
    )


def _server(**config: Any) -> MCPServer:
    return MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=None,
        config=build_mcp_config(**config) if config else None,
    )


def _listed(server: MCPServer, **kwargs: Any) -> list[str]:
    listing: Any = server.list_tools(user=None, **kwargs)
    return [tool["name"] for tool in listing["tools"]]


def _pool_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record every pool a pass builds, still building it for real."""
    built: list[dict[str, Any]] = []

    def spy(**kwargs: Any) -> dict[str, Any]:
        pool = base_pool(**kwargs)
        built.append(pool)
        return pool

    monkeypatch.setattr(_POOL, spy)
    return built


def _hop_spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every function the server hands to the executor, still handing it."""
    from asgiref.sync import sync_to_async

    hops: list[Any] = []

    def spy(func: Any, **kwargs: Any) -> Any:
        hops.append(func)
        return sync_to_async(func, **kwargs)

    monkeypatch.setattr(_HOP, spy)
    return hops


def _every_kind(server: MCPServer) -> None:
    """One of each thing a pass can meet, in an order that interleaves the answers."""
    server.register_service_tool(name="post_entry", spec=_service(), permissions=[])
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[])
    server.register_service_tool(name="open_books", spec=_service(_OPEN), permissions=[])
    server.register_service_tool(
        name="reopen_books", spec=_service(_CLOSED), permissions=[], always_listed=True
    )
    server.register_service_tool(name="void_invoice", spec=_service(_ROW_ONLY), permissions=[])
    server.register_selector_tool(
        name="get_order", spec=order_selector_spec(SelectorKind.RETRIEVE), permissions=[]
    )
    server.register_chain_tool(
        name="close_and_freeze",
        atomic=False,
        steps=[
            ChainStep("open", _service(_OPEN)),
            ChainStep("freeze", _service(_FROZEN)),
            ChainStep("close", _service(_CLOSED)),
        ],
    )


# ---------- agreement with the listing ----------


def test_each_left_out_tool_is_named_with_the_condition_that_left_it_out() -> None:
    server = _server()
    _every_kind(server)

    unavailable = server.unavailable_tools(user=None)

    assert unavailable == {"close_books": _CLOSED, "close_and_freeze": _FROZEN}
    assert (unavailable["close_books"].code, unavailable["close_books"].reason) == (
        "books_closed",
        "The books are closed.",
    )


def test_a_name_is_reported_exactly_when_the_listing_omits_it() -> None:
    """The two come from one pass, so they partition the tools the caller may see:
    everything listed with ``include_unavailable`` is either in the plain listing
    or reported here, never both and never neither."""
    server = _server()
    _every_kind(server)

    everything = _listed(server, include_unavailable=True)
    offered = _listed(server)
    unavailable = server.unavailable_tools(user=None)

    assert set(offered).isdisjoint(unavailable)
    assert sorted([*offered, *unavailable]) == sorted(everything)


def test_a_chain_reports_its_first_unmet_step_in_order() -> None:
    """``ledger_frozen`` is the refusal a call would meet first, since the steps run
    in declaration order and ``close`` is never reached."""
    server = _server()
    _every_kind(server)

    assert server.unavailable_tools(user=None)["close_and_freeze"].code == "ledger_frozen"


def test_always_listed_is_never_reported() -> None:
    """It is never left out, so there is nothing to explain; its condition is
    still enforced at the call."""
    server = _server()
    server.register_service_tool(
        name="reopen_books", spec=_service(_CLOSED), permissions=[], always_listed=True
    )

    assert _listed(server) == ["reopen_books"]
    assert server.unavailable_tools(user=None) == {}


def test_the_answer_is_asked_again_on_every_call() -> None:
    books: dict[str, bool] = {"open": False}
    server = _server()
    server.register_service_tool(
        name="close_books",
        spec=_service(
            Affordance(code="books_closed", reason="Closed.", when=lambda: books["open"])
        ),
        permissions=[],
    )

    before = set(server.unavailable_tools(user=None))
    books["open"] = True
    after = set(server.unavailable_tools(user=None))

    assert (before, after) == ({"close_books"}, set())


# ---------- what the caller may see ----------


def test_a_tool_hidden_from_the_caller_is_never_named(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under ``FILTER_LISTINGS_BY_PERMISSIONS`` a tool this caller may not see is not
    named, nor is its condition asked: its reason is written for whoever may call
    it, and naming it would tell another principal it exists."""
    built = _pool_spy(monkeypatch)
    server = _server(filter_listings_by_permissions=True)
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[_Deny()])

    assert server.unavailable_tools(user=None) == {}
    assert built == []


def test_with_the_permission_filter_off_every_tool_is_answered() -> None:
    """The flag is off by default, and then the listing shows the tool to anyone;
    what it omits for an unmet condition is reported whatever its permissions."""
    server = _server()
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[_Deny()])

    assert server.unavailable_tools(user=None) == {"close_books": _CLOSED}


# ---------- include_unavailable ----------


def test_include_unavailable_lists_every_tool_and_asks_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing about this caller was asked, so the listing is as public as one
    over tools declaring nothing."""
    built = _pool_spy(monkeypatch)
    server = _server()
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[])
    server.register_service_tool(name="open_books", spec=_service(_OPEN), permissions=[])

    listing: Any = server.list_tools(user=None, include_unavailable=True)

    assert [tool["name"] for tool in listing["tools"]] == ["close_books", "open_books"]
    assert listing["cacheScope"] == "public"
    assert built == []


def test_include_unavailable_still_applies_the_permission_filter() -> None:
    server = _server(filter_listings_by_permissions=True)
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[_Deny()])
    server.register_service_tool(name="open_books", spec=_service(_OPEN), permissions=[])

    assert _listed(server, include_unavailable=True) == ["open_books"]


def test_the_default_listing_still_leaves_unavailable_tools_out() -> None:
    server = _server()
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[])

    assert _listed(server) == []


async def test_alist_tools_passes_include_unavailable_through() -> None:
    server = _server()
    server.register_service_tool(name="close_books", spec=_service(_CLOSED), permissions=[])

    everything: Any = await server.alist_tools(user=None, include_unavailable=True)
    offered: Any = await server.alist_tools(user=None)

    assert [tool["name"] for tool in everything["tools"]] == ["close_books"]
    assert offered["tools"] == []


# ---------- the async twin ----------


async def test_aunavailable_tools_answers_as_the_sync_method_does() -> None:
    server = _server()
    _every_kind(server)

    assert await server.aunavailable_tools(user=None) == server.unavailable_tools(user=None)


async def test_a_server_declaring_nothing_to_ask_answers_without_a_hop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A consumer asks on every model step, so a server with nothing to ask should
    not cost a thread switch per step. Every tool here is one a pass skips: no
    affordance, a row condition, a selector projecting another operation, a chain
    of such steps, and an ``always_listed`` tool whose condition is never asked."""
    hops = _hop_spy(monkeypatch)
    server = _server()
    server.register_service_tool(name="post_entry", spec=_service(), permissions=[])
    server.register_service_tool(name="void_invoice", spec=_service(_ROW_ONLY), permissions=[])
    server.register_selector_tool(
        name="get_order", spec=order_selector_spec(SelectorKind.RETRIEVE), permissions=[]
    )
    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[
            ChainStep("entry", _service()),
            ChainStep(
                "order",
                order_selector_spec(SelectorKind.RETRIEVE, affordances={"archive": ARCHIVE_ORDER}),
            ),
        ],
    )
    server.register_service_tool(
        name="reopen_books", spec=_service(_CLOSED), permissions=[], always_listed=True
    )

    assert await server.aunavailable_tools(user=None) == {}
    assert hops == []


async def test_one_condition_to_ask_takes_the_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    hops = _hop_spy(monkeypatch)
    server = _server()
    server.register_service_tool(name="post_entry", spec=_service(), permissions=[])
    server.register_service_tool(name="open_books", spec=_service(_OPEN), permissions=[])

    assert await server.aunavailable_tools(user=None) == {}
    assert len(hops) == 1


@pytest.mark.django_db(transaction=True)
async def test_a_condition_that_queries_is_asked_off_the_event_loop() -> None:
    """Asked on the loop, the query would raise ``SynchronousOnlyOperation``."""
    server = _server()
    server.register_service_tool(
        name="close_books",
        spec=_service(
            Affordance(
                code="no_invoices",
                reason="There is nothing to close.",
                when=lambda: Invoice.objects.exists(),
            )
        ),
        permissions=[],
    )

    unavailable = await server.aunavailable_tools(user=None)

    assert {name: a.code for name, a in unavailable.items()} == {"close_books": "no_invoices"}
