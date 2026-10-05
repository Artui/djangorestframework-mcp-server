"""``MCPServer(pool_seeds=)`` reaches every dispatch and every condition the server asks.

A registered seed is what hangs off ``request`` over HTTP and has no channel off
it -- a tenant, a locale, a clock. A spec reading one has to receive it wherever
this server runs the spec, or it works when ``dispatch_spec`` is called directly
and fails here. So each reach is driven on its own, because each builds its pool
or its context somewhere else:

- the four transport contexts (legacy and modern era, on the sync and the async
  viewset), the in-process ``call_tool`` / ``acall_tool`` context, and the task
  worker's;
- a selector tool, whose dispatch kwargs are shared by both of its siblings;
- a chain step, which assembles its own pool rather than calling ``dispatch_spec``;
- the availability check ``tools/list`` and ``unavailable_tools`` run;
- registration, which must neither refuse a callable for declaring a seed nor
  accept a ``UrlKwarg`` / ``QueryParam`` that dispatch would strip.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django.test import AsyncClient, Client, override_settings
from rest_framework import serializers
from rest_framework.permissions import AllowAny
from rest_framework_services import DEFAULT_POOL_SEEDS, PoolSeeds
from rest_framework_services.types.affordance import Affordance
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer, PromptArgument, QueryParam, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import TaskPolicy
from rest_framework_mcp.handlers.handle_completion_complete import handle_completion_complete
from rest_framework_mcp.handlers.handle_prompts_get import handle_prompts_get
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.tasks.create_task import create_task
from rest_framework_mcp.tasks.in_memory_task_store import InMemoryTaskStore
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.tasks.conftest import RecordingExecutor
from tests.testapp.urlconf_for import urlconf_for

MODERN = "2026-07-28"
LEGACY = "2025-11-25"

SEEDS: PoolSeeds = DEFAULT_POOL_SEEDS.extend(tenant=lambda: "acme")


def _whoami(*, tenant: str) -> dict[str, str]:
    # ``tenant`` has no default on purpose: registration has to count a
    # registered seed as a source, or this callable is refused before it runs.
    return {"tenant": tenant}


def _reports(*, progress: Any) -> dict[str, str]:
    progress(1, total=1)
    return {"reported": "yes"}


def _card(*, slug: str, tenant: str, progress: Any) -> dict[str, str]:
    # ``progress`` too: a resource read's pool is built through drf-services'
    # ``base_pool``, which supplies a no-op reporter.
    progress(1, total=1)
    return {"slug": slug, "tenant": tenant}


def _scope(*, tenant: str) -> dict[str, str]:
    return {"tenant": tenant}


def _tenant_is(expected: str) -> Affordance:
    return Affordance(
        code=f"not_{expected}",
        reason=f"Only {expected} may do this.",
        when=lambda *, tenant: tenant == expected,
    )


def _service(**kwargs: Any) -> ServiceSpec:
    return ServiceSpec(service=_whoami, atomic=False, permission_classes=[AllowAny], **kwargs)


def _selector() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_scope, permission_classes=[AllowAny])


def _server(**kwargs: Any) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        pool_seeds=SEEDS,
        **kwargs,
    )
    server.register_service_tool(
        name="tenant.whoami",
        spec=_service(),
        task_policy=TaskPolicy.OPTIONAL,
    )
    server.register_selector_tool(name="tenant.scope", spec=_selector())
    for code, expected in (("acme_only", "acme"), ("globex_only", "globex")):
        server.register_service_tool(
            name=f"tenant.{code}",
            spec=_service(affordances=[_tenant_is(expected)]),
        )
    server.register_chain_tool(
        name="tenant.chain",
        steps=[
            # ``inputs`` forwards the client's arguments whole, the natural way
            # to write one, so a client ``tenant`` is in what it provides.
            ChainStep(
                "who",
                _service(affordances=[_tenant_is("acme")]),
                inputs=lambda ctx: dict(ctx.args),
            ),
            # Declares ``progress``, which a chain step's pool carries because
            # it is built through drf-services' ``base_pool``.
            ChainStep(
                "reported",
                ServiceSpec(service=_reports, atomic=False, permission_classes=[AllowAny]),
            ),
        ],
        output_alias="who",
    )
    server.register_resource(
        name="tenant.card",
        uri_template="tenants://{slug}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE, selector=_card, permission_classes=[AllowAny]
        ),
    )
    return server


def _structured(result: Any) -> Any:
    assert not result.get("isError"), result
    return result["structuredContent"]


# ----- the transports' contexts -----


async def _awaited(response: Any) -> Any:
    return await response


def _post(era: str, is_async: bool, method: str, params: dict[str, Any]) -> Any:
    """One request to the mounted endpoint, in ``era``, on the chosen viewset."""
    client: Any = AsyncClient() if is_async else Client()
    headers: dict[str, str] = {"Mcp-Protocol-Version": era}
    body: dict[str, Any] = dict(params)
    if era == MODERN:
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
        elif method == "resources/read":
            headers["Mcp-Name"] = params["uri"]
        body["_meta"] = {
            "io.modelcontextprotocol/protocolVersion": MODERN,
            "io.modelcontextprotocol/clientInfo": {"name": "pytest", "version": "0"},
            "io.modelcontextprotocol/clientCapabilities": {},
        }
    else:
        opened = _send(
            client,
            is_async,
            "initialize",
            {
                "protocolVersion": LEGACY,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
            {},
        )
        assert opened.status_code == 200, opened.content
        headers["Mcp-Session-Id"] = opened["Mcp-Session-Id"]
    response = _send(client, is_async, method, body, headers)
    assert response.status_code == 200, response.content
    return json.loads(response.content)["result"]


def _send(
    client: Any, is_async: bool, method: str, params: dict[str, Any], headers: dict[str, str]
) -> Any:
    response = client.post(
        "/mcp/",
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}),
        content_type="application/json",
        headers=headers,
    )
    return async_to_sync(_awaited)(response) if is_async else response


_TRANSPORTS = pytest.mark.parametrize("is_async", [False, True], ids=["sync", "async"])
_ERAS = pytest.mark.parametrize("era", [LEGACY, MODERN], ids=["legacy", "modern"])


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_a_service_tool_reads_a_seed_over_the_wire(era: str, is_async: bool) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/call", {"name": "tenant.whoami", "arguments": {}})

    assert _structured(result) == {"tenant": "acme"}


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_the_listing_asks_a_condition_with_the_seeds_over_the_wire(
    era: str, is_async: bool
) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/list", {})

    names = [tool["name"] for tool in result["tools"]]
    assert "tenant.acme_only" in names
    assert "tenant.globex_only" not in names


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_a_resource_selector_reads_a_seed_over_the_wire(era: str, is_async: bool) -> None:
    server = _server()
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "resources/read", {"uri": "tenants://north"})

    assert json.loads(result["contents"][0]["text"]) == {"slug": "north", "tenant": "acme"}


# ----- in-process and the task worker -----


@pytest.mark.django_db
def test_call_tool_hands_the_seeds_to_a_service_and_a_selector() -> None:
    server = _server()

    assert _structured(server.call_tool("tenant.whoami", {}, user=None).to_dict()) == {
        "tenant": "acme"
    }
    assert _structured(server.call_tool("tenant.scope", {}, user=None).to_dict()) == {
        "tenant": "acme"
    }


@pytest.mark.django_db(transaction=True)
def test_acall_tool_hands_the_seeds_to_a_service_and_a_selector() -> None:
    server = _server()

    for name in ("tenant.whoami", "tenant.scope"):
        result = async_to_sync(server.acall_tool)(name, {}, user=None)
        assert _structured(result) == {"tenant": "acme"}


@pytest.mark.django_db
def test_a_task_worker_hands_the_seeds_to_the_tool_it_runs() -> None:
    store = InMemoryTaskStore()
    server = _server(task_store=store, task_executor=RecordingExecutor(store))
    task = create_task(
        store=store,
        executor=RecordingExecutor(store),
        tool_name="tenant.whoami",
        arguments={},
        token=TokenInfo(user=None),
        ttl_ms=60_000,
        poll_interval_ms=500,
    )

    server.run_task(task.task_id)

    assert store.get(task.task_id).task.result["structuredContent"] == {"tenant": "acme"}


# ----- client input cannot occupy a seed -----


@pytest.mark.parametrize("in_process", [True, False], ids=["call_tool", "acall_tool"])
@pytest.mark.django_db(transaction=True)
def test_a_client_argument_named_after_a_seed_does_not_reach_a_selector(in_process: bool) -> None:
    # No validator stands in front of a selector's spread, so this is the path
    # where an unreserved name would be client-controlled. ``call_tool`` and the
    # handlers dispatch a selector from two different places.
    server = _server()
    arguments = {"tenant": "globex"}

    result = (
        server.call_tool("tenant.scope", arguments, user=None).to_dict()
        if in_process
        else async_to_sync(server.acall_tool)("tenant.scope", arguments, user=None)
    )

    assert _structured(result) == {"tenant": "acme"}


# ----- chain steps -----


@pytest.mark.django_db
def test_a_chain_step_reads_a_seed_over_what_its_inputs_provide() -> None:
    server = _server()

    result = async_to_sync(server.acall_tool)("tenant.chain", {"tenant": "globex"}, user=None)

    assert _structured(result) == {"tenant": "acme"}


# ----- availability -----


@pytest.mark.django_db
def test_list_tools_and_unavailable_tools_ask_conditions_with_the_seeds() -> None:
    server = _server()

    listed: Any = server.list_tools(user=None)
    names = [tool["name"] for tool in listed["tools"]]
    assert "tenant.acme_only" in names
    assert "tenant.globex_only" not in names
    assert set(server.unavailable_tools(user=None)) == {"tenant.globex_only"}
    assert set(async_to_sync(server.aunavailable_tools)(user=None)) == {"tenant.globex_only"}


def _tenant_of(*, user: Any) -> Any:
    # The resolver the docs show: an anonymous caller resolves to no tenant.
    return getattr(user, "tenant", None)


def _anonymous_listing_server(resolver: Any) -> MCPServer:
    server = MCPServer(
        name="t",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        pool_seeds=DEFAULT_POOL_SEEDS.extend(tenant=resolver),
    )
    server.register_service_tool(name="tenant.plain", spec=_service())
    server.register_service_tool(
        name="tenant.members_only",
        spec=_service(
            affordances=[
                Affordance(
                    code="no_tenant",
                    reason="Sign in to a tenant first.",
                    when=lambda *, tenant: tenant is not None,
                )
            ]
        ),
    )
    return server


@_TRANSPORTS
@_ERAS
@pytest.mark.django_db(transaction=True)
def test_the_listing_resolves_the_seeds_for_an_anonymous_caller(era: str, is_async: bool) -> None:
    # ``AllowAnyBackend`` admits an ``AnonymousUser``, and one declared
    # condition is enough for ``tools/list`` to resolve the seeds for it: the
    # gated tool is hidden because the resolver answered ``None``.
    server = _anonymous_listing_server(_tenant_of)
    with override_settings(ROOT_URLCONF=urlconf_for(server, is_async=is_async)):
        result = _post(era, is_async, "tools/list", {})

    assert [tool["name"] for tool in result["tools"]] == ["tenant.plain"]


@pytest.mark.django_db
def test_a_resolver_that_raises_for_a_caller_fails_that_callers_listing() -> None:
    server = _anonymous_listing_server(lambda *, user: user.tenant)

    with pytest.raises(AttributeError, match="tenant"):
        server.list_tools(user=AnonymousUser())


# ----- registration -----


@pytest.mark.parametrize(
    ("channel", "declaration"),
    [("url_kwargs", UrlKwarg(name="tenant")), ("query_params", QueryParam(name="tenant"))],
)
@pytest.mark.parametrize("kind", ["service", "selector"])
def test_a_channel_named_after_a_seed_is_refused(kind: str, channel: str, declaration: Any) -> None:
    # Dispatch strips a reserved name from both channels, so the declaration
    # would otherwise be accepted here and dropped on every call.
    server = MCPServer(name="t", pool_seeds=SEEDS)
    register = server.register_service_tool if kind == "service" else server.register_selector_tool
    spec = _service() if kind == "service" else _selector()

    with pytest.raises(ImproperlyConfigured, match=rf"{channel} name\(s\) \['tenant'\] collide"):
        register(name="tenant.x", spec=spec, **{channel: [declaration]})


class _NoTenantInput(serializers.Serializer):
    """Declares no field, so nothing but a seed can supply ``tenant``."""


@pytest.mark.django_db
def test_a_seed_is_a_source_for_a_required_parameter_beside_an_input_serializer() -> None:
    # With an ``input_serializer`` a selector is out of trust mode, so each
    # required parameter needs a static source; a registered seed is one.
    server = MCPServer(name="t", pool_seeds=SEEDS)
    server.register_selector_tool(
        name="tenant.scope", spec=_selector(), input_serializer=_NoTenantInput
    )

    assert _structured(server.call_tool("tenant.scope", {}, user=None).to_dict()) == {
        "tenant": "acme"
    }


def test_a_uri_template_variable_named_after_a_seed_is_refused() -> None:
    server = MCPServer(name="t", pool_seeds=SEEDS)

    with pytest.raises(ImproperlyConfigured, match=r"variable name\(s\) \['tenant'\] collide"):
        server.register_resource(
            name="tenant.card",
            uri_template="tenants://{tenant}",
            selector=_selector(),
        )


class _AlwaysAllow:
    def has_permission(self, *_args: object, **_kwargs: object) -> bool:
        return True


def test_prompts_and_completion_do_not_receive_the_seeds_yet() -> None:
    # Pins what the docs state: a prompt's render and a completer are bare
    # callables, not specs, and are called with pools of their own. The day
    # either receives the seeds this fails, and the docs sentence goes.
    seen: dict[str, str] = {}

    def complete_topic(*, value: str, tenant: str = "unset") -> list[str]:
        seen["completer"] = tenant
        return [value]

    server = MCPServer(name="t", pool_seeds=SEEDS)
    server.register_prompt(
        name="brief",
        render=lambda *, topic, tenant="unset": f"{tenant}:{topic}",
        arguments=[PromptArgument(name="topic", required=True)],
        completions={"topic": complete_topic},
        permissions=[_AlwaysAllow()],
    )
    context = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=LEGACY,
        pool_seeds=server.pool_seeds,
    )

    rendered: Any = handle_prompts_get({"name": "brief", "arguments": {"topic": "q3"}}, context)
    handle_completion_complete(
        {
            "ref": {"type": "ref/prompt", "name": "brief"},
            "argument": {"name": "topic", "value": ""},
        },
        context,
    )

    assert rendered["messages"][0]["content"]["text"] == "unset:q3"
    assert seen == {"completer": "unset"}


def test_the_server_and_a_bare_context_default_to_drf_services_seeds() -> None:
    request = HttpRequest()
    bare = MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=MCPServer(name="t").tools,
        resources=MCPServer(name="t").resources,
        prompts=MCPServer(name="t").prompts,
        protocol_version=MODERN,
    )

    assert MCPServer(name="t").pool_seeds is DEFAULT_POOL_SEEDS
    assert MCPServer(name="t", pool_seeds=SEEDS).pool_seeds is SEEDS
    assert bare.pool_seeds is DEFAULT_POOL_SEEDS
