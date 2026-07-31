"""The filter, the topics, the broker, and who is allowed to watch what."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, NotificationKind, SubscriptionFilter
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TASKS_EXTENSION_ID
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.subscriptions.grant_subscription import grant_subscription
from rest_framework_mcp.subscriptions.in_memory_subscription_broker import (
    InMemorySubscriptionBroker,
)
from rest_framework_mcp.subscriptions.utils import (
    topic_for_kind,
    topic_for_resource,
    topic_for_task,
)
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from tests.tasks.conftest import RecordingExecutor, slow_service

# ----- SubscriptionFilter -----


def test_only_true_opts_in() -> None:
    """Every type is opt-in and the server MUST NOT send what was not asked
    for, so anything other than an explicit ``true`` is a refusal."""
    parsed = SubscriptionFilter.from_params(
        {"toolsListChanged": True, "promptsListChanged": False, "resourcesListChanged": "yes"}
    )
    assert parsed.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_malformed_filter_is_empty_rather_than_an_error() -> None:
    assert SubscriptionFilter.from_params(None) == SubscriptionFilter()
    assert SubscriptionFilter.from_params("nope") == SubscriptionFilter()


def test_non_string_and_empty_uris_are_dropped() -> None:
    parsed = SubscriptionFilter.from_params({"resourceSubscriptions": ["a://1", 7, "", None]})
    assert parsed.resource_uris == ("a://1",)


def test_duplicate_uris_collapse_but_order_is_kept() -> None:
    parsed = SubscriptionFilter.from_params({"resourceSubscriptions": ["b://1", "a://1", "b://1"]})
    assert parsed.resource_uris == ("b://1", "a://1")


def test_task_ids_come_from_the_extensions_field() -> None:
    assert SubscriptionFilter.from_params({"taskIds": ["t1"]}).task_ids == ("t1",)


def test_the_wire_form_omits_everything_not_granted() -> None:
    """The acknowledgement reads as "these are the things you will receive", so
    a ``false`` or an empty list in it would read as a promise about
    something."""
    assert SubscriptionFilter().to_dict() == {}


def test_the_wire_form_round_trips() -> None:
    original = SubscriptionFilter(
        kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED}),
        resource_uris=("a://1",),
        task_ids=("t1",),
    )
    assert SubscriptionFilter.from_params(original.to_dict()) == original


def test_an_empty_filter_is_falsy() -> None:
    assert not SubscriptionFilter()
    assert SubscriptionFilter(task_ids=("t",))


def test_every_kind_has_a_distinct_filter_field_and_method() -> None:
    """The two are kept mechanically related so a new kind cannot be added to
    one and forgotten in the other."""
    fields = {kind.filter_field for kind in NotificationKind}
    methods = {kind.method for kind in NotificationKind}
    assert len(fields) == len(methods) == len(list(NotificationKind))
    assert all(m.startswith("notifications/") for m in methods)


# ----- topics -----


def test_topics_are_namespaced_so_a_uri_cannot_collide_with_a_task_id() -> None:
    assert topic_for_resource("x") != topic_for_task("x")
    assert topic_for_kind(NotificationKind.TOOLS_LIST_CHANGED).startswith("kind:")


def test_resource_topics_are_exact_not_prefixes() -> None:
    """⚠ A prefix rule would match ``invoices://1`` against ``invoices://11``,
    and would miss a tenant-scoped scheme entirely. Publishers name the
    collection explicitly instead."""
    assert topic_for_resource("invoices://1") != topic_for_resource("invoices://11")


# ----- the broker -----


async def test_a_payload_reaches_every_subscriber_of_a_topic() -> None:
    """The session broker replaces the previous subscriber; this one must not,
    or a second client watching the same resource silently disconnects the
    first."""
    broker = InMemorySubscriptionBroker()
    a = broker.subscribe(frozenset({"t"}))
    b = broker.subscribe(frozenset({"t"}))
    assert await broker.publish("t", {"n": 1}) == 2
    assert a.get_nowait() == {"n": 1}
    assert b.get_nowait() == {"n": 1}


async def test_one_subscription_watching_several_topics_reads_one_stream() -> None:
    broker = InMemorySubscriptionBroker()
    queue = broker.subscribe(frozenset({"a", "b"}))
    await broker.publish("a", 1)
    await broker.publish("b", 2)
    assert {queue.get_nowait(), queue.get_nowait()} == {1, 2}


async def test_publishing_to_nobody_is_not_an_error() -> None:
    assert await InMemorySubscriptionBroker().publish("quiet", {}) == 0


async def test_unsubscribing_stops_delivery_and_frees_the_topic() -> None:
    """Topics are caller-named and unbounded, so an emptied one is removed —
    otherwise a long-lived server accumulates an entry per URI ever watched."""
    broker = InMemorySubscriptionBroker()
    queue = broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(queue)
    assert await broker.publish("t", {}) == 0
    assert broker._by_topic == {}


async def test_unsubscribing_one_of_two_leaves_the_other() -> None:
    broker = InMemorySubscriptionBroker()
    a = broker.subscribe(frozenset({"t"}))
    b = broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(a)
    assert await broker.publish("t", {}) == 1
    assert b.qsize() == 1


async def test_unsubscribing_twice_is_a_no_op() -> None:
    """The stream's ``finally`` can run after an explicit teardown."""
    broker = InMemorySubscriptionBroker()
    queue = broker.subscribe(frozenset({"t"}))
    broker.unsubscribe(queue)
    broker.unsubscribe(queue)


async def test_a_subscription_naming_no_topics_still_gets_a_queue() -> None:
    assert isinstance(InMemorySubscriptionBroker().subscribe(frozenset()), asyncio.Queue)


# ----- grant_subscription: the authorization boundary -----


class _Denies:
    def has_permission(self, request: Any, token: Any) -> bool:
        return False

    def required_scopes(self) -> list[str]:
        return ["r:read"]


def _selector() -> list[dict[str, str]]:
    return [{"ok": "1"}]


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(name="subs", auth_backend=AllowAnyBackend(), **kwargs)
    server.register_resource(
        name="open",
        uri_template="open://thing",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
    )
    server.register_resource(
        name="gated",
        uri_template="gated://thing",
        selector=SelectorSpec(kind=SelectorKind.LIST, selector=_selector),
        permissions=[_Denies()],
    )
    return server


def _context(server: MCPServer, **overrides: Any) -> MCPCallContext:
    from django.http import HttpRequest

    request = HttpRequest()
    request.method = "POST"
    request.user = None
    base: dict[str, Any] = {
        "http_request": request,
        "token": TokenInfo(user=None),
        "tools": server._tools,
        "resources": server._resources,
        "prompts": server._prompts,
        "protocol_version": "2026-07-28",
        "client_capabilities": {"extensions": {TASKS_EXTENSION_ID: {}}},
        "tasks": server.task_store,
        "subscriptions": server.subscription_broker,
        "config": server._config,
    }
    base.update(overrides)
    return MCPCallContext(**base)


def test_a_readable_resource_is_granted() -> None:
    server = _server()
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("open://thing",)), _context(server)
    )
    assert granted.resource_uris == ("open://thing",)
    assert topics == frozenset({topic_for_resource("open://thing")})


def test_a_resource_the_caller_cannot_read_is_not_watchable() -> None:
    """⚠ Otherwise a subscription is a side channel around ``resources/read``:
    a caller denied the body still learns every time it changes, and *when*
    something changes is often the more sensitive signal."""
    server = _server()
    granted, topics = grant_subscription(
        SubscriptionFilter(resource_uris=("gated://thing",)), _context(server)
    )
    assert granted.resource_uris == ()
    assert topics == frozenset()


def test_an_unregistered_uri_is_refused_the_same_way_as_a_forbidden_one() -> None:
    """Indistinguishable on purpose — erroring on one and not the other would
    make the endpoint an oracle for which resources exist."""
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("nope://thing",)), _context(server)
    )
    assert granted.resource_uris == ()


def test_a_refused_entry_does_not_take_the_rest_of_the_subscription_with_it() -> None:
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(resource_uris=("gated://thing", "open://thing")), _context(server)
    )
    assert granted.resource_uris == ("open://thing",)


def test_a_list_changed_kind_needs_a_registry_behind_it() -> None:
    """Same rule as capability advertisement: promising an event this server
    cannot produce leaves the client waiting forever."""
    server = _server()
    granted, _ = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset(
                {NotificationKind.RESOURCES_LIST_CHANGED, NotificationKind.PROMPTS_LIST_CHANGED}
            )
        ),
        _context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.RESOURCES_LIST_CHANGED})


def test_tools_list_changed_is_granted_when_tools_exist() -> None:
    server = _server()
    server.register_service_tool(
        name="t", description="x", spec=ServiceSpec(service=slow_service, atomic=False)
    )
    granted, _ = grant_subscription(
        SubscriptionFilter(kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED})),
        _context(server),
    )
    assert granted.kinds == frozenset({NotificationKind.TOOLS_LIST_CHANGED})


def test_a_task_is_watchable_only_by_the_principal_that_created_it() -> None:
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    task = create_task(
        store=store,
        executor=RecordingExecutor(store),
        tool_name="t",
        arguments={},
        token=TokenInfo(user=None),
        ttl_ms=None,
        poll_interval_ms=None,
    )

    class _Other:
        pk = 99

    mine, _ = grant_subscription(SubscriptionFilter(task_ids=(task.task_id,)), _context(server))
    theirs, _ = grant_subscription(
        SubscriptionFilter(task_ids=(task.task_id,)),
        _context(server, token=TokenInfo(user=_Other())),
    )
    assert mine.task_ids == (task.task_id,)
    assert theirs.task_ids == ()


def test_an_unknown_task_is_not_watchable() -> None:
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("nope",)), _context(server))
    assert granted.task_ids == ()


def test_a_server_running_no_tasks_grants_no_task_subscriptions() -> None:
    granted, _ = grant_subscription(SubscriptionFilter(task_ids=("t",)), _context(_server()))
    assert granted.task_ids == ()


def test_granted_and_topics_cannot_disagree() -> None:
    """They are produced together precisely so one cannot promise what the
    other does not deliver."""
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    server.register_service_tool(
        name="t", description="x", spec=ServiceSpec(service=slow_service, atomic=False)
    )
    granted, topics = grant_subscription(
        SubscriptionFilter(
            kinds=frozenset({NotificationKind.TOOLS_LIST_CHANGED}),
            resource_uris=("open://thing", "gated://thing"),
        ),
        _context(server),
    )
    expected = {topic_for_kind(k) for k in granted.kinds} | {
        topic_for_resource(u) for u in granted.resource_uris
    }
    assert topics == expected


# ----- the publish API -----


@pytest.mark.asyncio
async def test_notify_resource_updated_reaches_a_watcher() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(subscription_broker=broker)
    queue = broker.subscribe(frozenset({topic_for_resource("open://thing")}))
    assert await server.notify_resource_updated("open://thing") == 1
    frame = queue.get_nowait()
    assert frame["method"] == "notifications/resources/updated"
    assert frame["params"]["uri"] == "open://thing"


@pytest.mark.asyncio
async def test_notifying_on_a_server_with_no_broker_is_a_no_op() -> None:
    """Safe to call unconditionally from a service, which is what makes it
    usable as the write path's own trigger."""
    assert await _server().notify_resource_updated("open://thing") == 0


@pytest.mark.asyncio
async def test_notify_list_changed_publishes_the_kind_topic() -> None:
    broker = InMemorySubscriptionBroker()
    server = _server(subscription_broker=broker)
    queue = broker.subscribe(frozenset({topic_for_kind(NotificationKind.RESOURCES_LIST_CHANGED)}))
    await server.notify_list_changed(NotificationKind.RESOURCES_LIST_CHANGED)
    assert queue.get_nowait()["method"] == "notifications/resources/list_changed"


def test_a_server_gets_no_broker_unless_one_is_named() -> None:
    """⚠ No default. A quietly-constructed in-process broker would advertise
    support and then deliver nothing as soon as a second worker existed."""
    assert _server().subscription_broker is None
