"""``invalidates=`` — the write path announcing its own changes."""

from __future__ import annotations

from typing import Any

import pytest
from django.db import transaction
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.render_invalidations import render_invalidations
from rest_framework_mcp.subscriptions.utils import topic_for_resource
from tests.subscriptions.test_subscription_core import _context

# ----- rendering -----


def test_a_placeholder_is_filled_from_the_result() -> None:
    assert render_invalidations(
        ("invoices://{pk}",), payload={"structuredContent": {"pk": 7}}, arguments={}
    ) == ("invoices://7",)


def test_a_placeholder_is_filled_from_the_arguments_when_the_result_lacks_it() -> None:
    """The delete case: the call returns nothing and ``{pk}`` lives only in the
    input."""
    assert render_invalidations(
        ("invoices://{pk}",), payload={"structuredContent": {}}, arguments={"pk": 3}
    ) == ("invoices://3",)


def test_the_result_wins_over_the_arguments() -> None:
    """After a write the result is authoritative — a service that reassigns a
    key leaves the argument stale."""
    assert render_invalidations(
        ("invoices://{pk}",),
        payload={"structuredContent": {"pk": "new"}},
        arguments={"pk": "old"},
    ) == ("invoices://new",)


def test_a_template_with_no_placeholders_is_the_static_case() -> None:
    """Which is why there is one mechanism rather than a separate static list."""
    assert render_invalidations(("invoices://",), payload={}, arguments={}) == ("invoices://",)


def test_an_unfillable_template_is_dropped_not_raised() -> None:
    """⚠ By now the write has committed. Failing the call over a formatting
    mistake would report failure for work that succeeded."""
    assert render_invalidations(("invoices://{nope}",), payload={}, arguments={}) == ()


def test_a_null_value_is_as_unusable_as_a_missing_one() -> None:
    """Rendering it would publish the literal topic ``invoices://None``, which
    nobody could have subscribed to."""
    assert (
        render_invalidations(
            ("invoices://{pk}",), payload={"structuredContent": {"pk": None}}, arguments={}
        )
        == ()
    )


def test_one_unfillable_template_does_not_take_the_others_with_it() -> None:
    assert render_invalidations(("invoices://{nope}", "invoices://"), payload={}, arguments={}) == (
        "invoices://",
    )


def test_duplicates_collapse_and_order_is_kept() -> None:
    assert render_invalidations(
        ("b://{pk}", "a://", "b://{pk}"),
        payload={"structuredContent": {"pk": 1}},
        arguments={},
    ) == ("b://1", "a://")


def test_a_list_payload_contributes_no_keys() -> None:
    """``{pk}`` over many rows has no single answer, and guessing one would
    publish a URI for an arbitrary member."""
    assert (
        render_invalidations(
            ("invoices://{pk}",), payload={"structuredContent": [{"pk": 1}]}, arguments={}
        )
        == ()
    )


def test_a_non_dict_result_contributes_no_keys() -> None:
    assert render_invalidations(("a://{pk}",), payload="text", arguments={}) == ()


# ----- dispatch -----


def _service(**kwargs: Any) -> dict[str, Any]:
    return {"pk": kwargs.get("data", {}).get("pk", 1)}


def _failing(**_: Any) -> Any:
    raise ServiceError("nope")


def _server(broker: Any) -> MCPServer:
    server = MCPServer(name="inval", auth_backend=AllowAnyBackend(), subscription_broker=broker)
    server.register_service_tool(
        name="invoices.create",
        description="x",
        spec=ServiceSpec(service=_service, atomic=False),
        invalidates=("invoices://{pk}", "invoices://"),
    )
    server.register_service_tool(
        name="invoices.broken",
        description="x",
        spec=ServiceSpec(service=_failing, atomic=False),
        invalidates=("invoices://{pk}",),
    )
    server.register_service_tool(
        name="invoices.quiet",
        description="x",
        spec=ServiceSpec(service=_service, atomic=False),
    )
    return server


@pytest.mark.django_db(transaction=True)
async def test_a_successful_call_announces_both_declared_uris() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    instance = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    collection = await broker.subscribe(frozenset({topic_for_resource("invoices://")}))

    await handle_tools_call_async({"name": "invoices.create", "arguments": {}}, _context(server))
    assert instance.get_nowait()["params"]["uri"] == "invoices://1"
    assert collection.get_nowait()["params"]["uri"] == "invoices://"


@pytest.mark.django_db(transaction=True)
async def test_a_failed_tool_announces_nothing() -> None:
    """⚠ Checked on ``isError``, not on the result being present — a
    ``ServiceError`` produces a perfectly well-formed result, which is the
    package's whole error contract."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    result = await handle_tools_call_async(
        {"name": "invoices.broken", "arguments": {}}, _context(server)
    )
    assert result["isError"] is True
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
async def test_a_binding_that_declares_nothing_publishes_nothing() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = await broker.subscribe(frozenset({topic_for_resource("invoices://1")}))
    await handle_tools_call_async({"name": "invoices.quiet", "arguments": {}}, _context(server))
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
async def test_an_unknown_tool_does_not_reach_the_publisher() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    result = await handle_tools_call_async({"name": "nope", "arguments": {}}, _context(server))
    assert result.code == -32602


@pytest.mark.django_db(transaction=True)
def test_the_sync_path_announces_too() -> None:
    """The two ``tools/call`` handlers are parallel implementations, so the
    hook has to exist in both."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    handle_tools_call({"name": "invoices.create", "arguments": {}}, _context(server))
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
def test_nothing_is_published_until_the_transaction_commits() -> None:
    """⚠ The failure this prevents: a subscriber re-reads on the notification
    and sees the old value, having been told it was new. A missed notification
    is recovered by the next read; a wrong one teaches the client something
    false."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    with transaction.atomic():
        handle_tools_call({"name": "invoices.create", "arguments": {}}, _context(server))
        assert queue.qsize() == 0, "published before commit"
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
def test_a_rolled_back_write_announces_nothing() -> None:
    """The other half of the same rule, and the one that would actually lie."""
    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    from asgiref.sync import async_to_sync

    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))

    class _Rollback(Exception):
        pass

    with pytest.raises(_Rollback), transaction.atomic():
        handle_tools_call({"name": "invoices.create", "arguments": {}}, _context(server))
        raise _Rollback
    assert queue.qsize() == 0


@pytest.mark.django_db(transaction=True)
def test_the_async_path_announces_on_the_thread_that_did_the_write() -> None:
    """⚠ The bug this pins. Django connections are thread-local: under ASGI the
    ORM work runs on a ``sync_to_async`` worker while the coroutine resumes on
    the event loop thread. Checking the transaction from the loop reads a
    *different* connection, sees none open, and publishes a write that has not
    committed — the exact failure the commit hook exists to prevent.

    Driven from a sync test because the atomic block has to be real.
    """
    from asgiref.sync import async_to_sync

    broker = InMemorySubscriptionBroker()
    server = _server(broker)
    queue = async_to_sync(broker.subscribe)(frozenset({topic_for_resource("invoices://")}))
    with transaction.atomic():
        async_to_sync(handle_tools_call_async)(
            {"name": "invoices.create", "arguments": {}}, _context(server)
        )
        assert queue.qsize() == 0, "announced before commit"
    assert queue.qsize() == 1


@pytest.mark.django_db(transaction=True)
async def test_publishing_nothing_is_a_no_op_on_both_paths() -> None:
    from rest_framework_mcp.subscriptions.publish_invalidations import publish_invalidations

    broker = InMemorySubscriptionBroker()
    publish_invalidations(broker, ())
    publish_invalidations(None, ("a://1",))


@pytest.mark.django_db(transaction=True)
async def test_a_server_with_no_broker_is_unaffected() -> None:
    """``invalidates=`` on a server that pushes nothing is inert, not an
    error — otherwise the declaration could not be written once and deployed
    both ways."""
    server = MCPServer(name="nobroker", auth_backend=AllowAnyBackend())
    server.register_service_tool(
        name="invoices.create",
        description="x",
        spec=ServiceSpec(service=_service, atomic=False),
        invalidates=("invoices://{pk}",),
    )
    result = await handle_tools_call_async(
        {"name": "invoices.create", "arguments": {}}, _context(server)
    )
    assert result["structuredContent"] == {"pk": 1}


# ----- registration surface -----


def test_a_selector_tool_has_no_invalidates_kwarg() -> None:
    """A read changes nothing, and accepting the kwarg would invite using it."""
    server = MCPServer(name="sel", auth_backend=AllowAnyBackend())
    with pytest.raises(TypeError, match="invalidates"):
        server.register_selector_tool(
            name="s",
            description="x",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda: []),
            paginate=True,
            invalidates=("a://",),  # type: ignore[call-arg]
        )


def test_a_chain_tool_accepts_invalidates() -> None:
    """A chain is a sequence of writes by construction here, and it fires once
    after the whole chain succeeds rather than per step."""
    from rest_framework_mcp import ChainStep

    server = MCPServer(name="chain", auth_backend=AllowAnyBackend())
    binding = server.register_chain_tool(
        name="c",
        description="x",
        steps=[ChainStep(alias="one", spec=ServiceSpec(service=_service, atomic=False))],
        invalidates=("a://",),
    )
    assert binding.invalidates == ("a://",)
