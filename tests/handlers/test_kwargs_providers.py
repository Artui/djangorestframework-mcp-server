"""``ServiceSpec.kwargs`` / ``SelectorSpec.kwargs`` providers wired through MCP.

The sister repo ships ``kwargs`` as a per-spec callable that returns extra
kwargs to merge into the dispatch pool. MCP dispatches through the sister
repo's :class:`~rest_framework_services.OfflineServiceView` so providers see
the right shape (request, action name, URI-template variables on resources).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.request import Request as DRFRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.server.mcp_server import MCPServer


def _ctx(*, tools=None, resources=None) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_ALICE),
        tools=tools or ToolRegistry(),
        resources=resources or ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


# ---------- ServiceSpec.kwargs (tools/call) ----------


def test_service_spec_kwargs_provider_merged_into_pool() -> None:
    """The provider's return dict reaches the service callable as kwargs."""
    captured: dict[str, Any] = {}

    def svc(*, tenant_id: int) -> dict:
        captured["tenant_id"] = tenant_id
        return {"tenant_id": tenant_id}

    def kwargs_provider(view, request) -> dict[str, Any]:
        # The provider sees both the synthesised view and the request — record
        # what arrived so the test can assert on the wire shape.
        captured["view_action"] = view.action
        captured["view_kwargs"] = view.kwargs
        return {"tenant_id": 42}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t.x",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    out = handle_tools_call({"name": "t.x", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tenant_id": 42}
    assert captured["tenant_id"] == 42
    assert captured["view_action"] == "t.x"
    assert captured["view_kwargs"] == {}


def test_service_spec_kwargs_provider_receives_drf_request() -> None:
    seen: dict[str, Any] = {}

    def svc(*, label: str) -> dict:
        return {"label": label}

    def kwargs_provider(view, request) -> dict[str, Any]:
        seen["request_user"] = getattr(request, "user", None)
        seen["request_is_drf"] = isinstance(request, DRFRequest)
        return {"label": "ok"}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert seen["request_user"] == "alice"
    assert seen["request_is_drf"] is True


async def test_async_service_spec_kwargs_provider_merged_into_pool() -> None:
    def svc(*, tenant_id: int) -> dict:
        return {"tenant_id": tenant_id}

    def kwargs_provider(view, request) -> dict[str, Any]:
        return {"tenant_id": 7}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=svc, atomic=False, kwargs=kwargs_provider),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tenant_id": 7}


# ---------- SelectorSpec acceptance + kwargs (resources/read) ----------


def test_register_resource_accepts_selector_spec() -> None:
    """A ``SelectorSpec`` flows through ``register_resource`` end-to-end."""
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )

    def get_invoice(*, pk: str) -> dict:
        return {"pk": pk, "via": "spec"}

    spec = SelectorSpec(kind=SelectorKind.RETRIEVE, selector=get_invoice)
    binding = server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=spec,  # type: ignore[arg-type],
    )
    assert binding.selector is get_invoice
    assert binding.kwargs_provider is None  # spec had no kwargs


def test_selector_spec_with_none_selector_is_rejected() -> None:
    """A spec with no concrete callable can't be dispatched — fail loudly."""
    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    with pytest.raises(ValueError, match="selector=None"):
        server.register_resource(
            name="empty",
            uri_template="x://",
            selector=SelectorSpec(kind=SelectorKind.LIST, selector=None),  # type: ignore[arg-type],
        )


def test_selector_spec_output_serializer_used_when_caller_omits() -> None:
    """The spec's ``output_serializer`` fills in when the caller doesn't pass one."""
    from rest_framework import serializers

    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    class OutSer(serializers.Serializer):
        pk = serializers.CharField()

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    spec = SelectorSpec(
        kind=SelectorKind.RETRIEVE, selector=lambda *, pk: {"pk": pk}, output_serializer=OutSer
    )
    binding = server.register_resource(
        name="r",
        uri_template="r://{pk}",
        selector=spec,  # type: ignore[arg-type],
    )
    assert binding.output_serializer is OutSer


def test_selector_spec_caller_output_serializer_wins() -> None:
    """Explicit caller arg trumps the spec's value (intentional override)."""
    from rest_framework import serializers

    from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
    from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

    class FromSpec(serializers.Serializer):
        pass

    class FromCaller(serializers.Serializer):
        pass

    server = MCPServer(
        name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore()
    )
    spec = SelectorSpec(kind=SelectorKind.LIST, selector=lambda: {}, output_serializer=FromSpec)
    binding = server.register_resource(
        name="r",
        uri_template="r://",
        selector=spec,  # type: ignore[arg-type],
        output_serializer=FromCaller,
    )
    assert binding.output_serializer is FromCaller


def test_selector_spec_kwargs_provider_invoked_on_read() -> None:
    """``SelectorSpec.kwargs`` runs on every ``resources/read`` and merges into the pool."""
    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    seen: dict[str, Any] = {}

    def selector(*, pk: str, tenant_id: int) -> dict:
        seen["selector_pk"] = pk
        seen["selector_tenant"] = tenant_id
        return {"pk": pk, "tenant": tenant_id}

    def provider(view, request) -> dict[str, Any]:
        # URI-template variables are exposed through ``view.kwargs`` so
        # providers can branch on them without parsing the URI again.
        seen["view_kwargs"] = view.kwargs
        return {"tenant_id": 99}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=selector,
            kind=SelectorKind.RETRIEVE,
            kwargs_provider=provider,
        )
    )
    out = handle_resources_read({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert seen["selector_pk"] == "7"
    assert seen["selector_tenant"] == 99
    assert seen["view_kwargs"] == {"pk": "7"}


async def test_async_selector_spec_kwargs_provider_invoked_on_read() -> None:
    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    def selector(*, pk: str, tenant_id: int) -> dict:
        return {"pk": pk, "tenant": tenant_id}

    def provider(view, request) -> dict[str, Any]:
        return {"tenant_id": 11}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=selector,
            kind=SelectorKind.RETRIEVE,
            kwargs_provider=provider,
        )
    )
    out = await handle_resources_read_async({"uri": "r://5"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    text = out["contents"][0]["text"]
    import json

    assert json.loads(text) == {"pk": "5", "tenant": 11}


def test_resource_kwargs_provider_declaring_request_only_is_bound_by_name() -> None:
    """``def provider(request)`` — bound through the keyword pool, as on HTTP.

    Forwarding ``(view, request)`` positionally raised ``TypeError`` for any
    provider that didn't lead with exactly those two parameters.
    """
    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    seen: dict[str, Any] = {}

    def provider(request: DRFRequest) -> dict[str, Any]:
        seen["user"] = request.user
        return {"tenant_id": 5}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=lambda *, pk, tenant_id: {"pk": pk, "tenant": tenant_id},
            kind=SelectorKind.RETRIEVE,
            kwargs_provider=provider,
        )
    )
    out = handle_resources_read({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert seen["user"] == "alice"
    assert '"tenant": 5' in out["contents"][0]["text"]


def test_resource_render_has_the_request_in_the_serializer_context() -> None:
    """A resource's ``output_serializer`` gets DRF's baseline context too."""
    from rest_framework import serializers

    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    class _Out(serializers.Serializer):
        who = serializers.SerializerMethodField()

        def get_who(self, _: Any) -> str:
            return str(self.context["request"].user)

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=lambda *, pk: {"pk": pk},
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
        )
    )
    out = handle_resources_read({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert '"who": "alice"' in out["contents"][0]["text"]


async def test_async_resource_render_has_the_request_in_the_serializer_context() -> None:
    from rest_framework import serializers

    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    class _Out(serializers.Serializer):
        who = serializers.SerializerMethodField()

        def get_who(self, _: Any) -> str:
            return str(self.context["request"].user)

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=lambda *, pk: {"pk": pk},
            kind=SelectorKind.RETRIEVE,
            output_serializer=_Out,
        )
    )
    out = await handle_resources_read_async({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert '"who": "alice"' in out["contents"][0]["text"]


# ---------- the async resources path stays off the event loop ----------
#
# ``resources/read`` renders a bare selector's return through the binding's
# ``output_serializer``, and a selector returning a queryset returns it *lazy* —
# the serializer is what evaluates it. Both tests query inside the callable under
# test, so they raise ``SynchronousOnlyOperation`` if it runs on the loop.


@pytest.mark.django_db(transaction=True)
async def test_async_list_resource_renders_a_lazy_queryset_off_loop() -> None:
    from rest_framework import serializers

    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
    from tests.testapp.models import Invoice

    await Invoice.objects.acreate(number="A", amount_cents=1)

    class _Out(serializers.ModelSerializer):
        class Meta:
            model = Invoice
            fields = ["id", "number"]

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://all",
            description=None,
            selector=lambda: Invoice.objects.all(),
            kind=SelectorKind.LIST,
            output_serializer=_Out,
        )
    )
    out = await handle_resources_read_async({"uri": "r://all"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert '"number": "A"' in out["contents"][0]["text"]


@pytest.mark.django_db(transaction=True)
async def test_async_resource_kwargs_provider_may_query() -> None:
    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding
    from tests.testapp.models import Invoice

    await Invoice.objects.acreate(number="A", amount_cents=1)

    def provider(view: Any) -> dict[str, Any]:
        # The headline use of a kwargs provider: a scoping lookup.
        return {"seen": Invoice.objects.count()}

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{pk}",
            description=None,
            selector=lambda *, pk, seen: {"pk": pk, "seen": seen},
            kind=SelectorKind.RETRIEVE,
            kwargs_provider=provider,
        )
    )
    out = await handle_resources_read_async({"uri": "r://7"}, _ctx(resources=resources))
    assert isinstance(out, dict)
    assert '"seen": 1' in out["contents"][0]["text"]


class _IdentifiedUser(str):
    """A user that is both a value and an identity.

    These tests assert on the user *value* (``== "alice"``, and it is rendered
    into tool output), while ``principal_for_token`` needs a ``pk`` — an
    authenticated caller without one is refused, because every such caller would
    share the ``"anonymous"`` principal and therefore each other's sessions.
    A ``str`` subclass satisfies both without changing a single assertion.
    """

    pk = "alice"


_ALICE = _IdentifiedUser("alice")


# ---------- the provider's UNSET decline contract (resources/read) ----------


def _declining_resource(provider: Any) -> ResourceRegistry:
    """A templated resource whose provider owns the same key the URI carries."""
    from rest_framework_mcp.registry.types.resource_binding import ResourceBinding

    resources = ResourceRegistry()
    resources.register(
        ResourceBinding(
            name="r",
            uri_template="r://{project_pk}",
            description=None,
            selector=lambda *, project_pk: {"project_pk": project_pk},
            kind=SelectorKind.RETRIEVE,
            kwargs_provider=provider,
        )
    )
    return resources


def test_a_provider_returning_unset_declines_rather_than_overriding() -> None:
    """``UNSET`` means "I am not setting this key", not "set it to the
    sentinel" — so the URI's own value stands, as it does on the HTTP and tool
    paths. Handing the sentinel to the selector turns a well-formed read into
    an internal error, or worse silently drops a scope."""
    from rest_framework_services import UNSET

    resources = _declining_resource(lambda view: {"project_pk": UNSET})

    out = handle_resources_read({"uri": "r://42"}, _ctx(resources=resources))

    assert isinstance(out, dict)
    assert json.loads(out["contents"][0]["text"]) == {"project_pk": "42"}


def test_a_provider_returning_a_value_still_wins() -> None:
    """Declining is opt-in; a resolved value is still authoritative."""
    resources = _declining_resource(lambda view: {"project_pk": "provider"})

    out = handle_resources_read({"uri": "r://42"}, _ctx(resources=resources))

    assert isinstance(out, dict)
    assert json.loads(out["contents"][0]["text"]) == {"project_pk": "provider"}


async def test_async_provider_returning_unset_declines_too() -> None:
    from rest_framework_services import UNSET

    resources = _declining_resource(lambda view: {"project_pk": UNSET})

    out = await handle_resources_read_async({"uri": "r://42"}, _ctx(resources=resources))

    assert isinstance(out, dict)
    assert json.loads(out["contents"][0]["text"]) == {"project_pk": "42"}
