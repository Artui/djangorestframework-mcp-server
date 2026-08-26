"""``UrlKwarg`` — URL-derived values seeded into the off-HTTP ``view.kwargs``.

The MCP counterpart of a nested route's URL captures. A registered
:class:`~rest_framework_mcp.UrlKwarg` is advertised as a tool argument, popped at
dispatch, and seeded into ``build_offline_context(kwargs=…)`` /
``OfflineServiceView.kwargs`` — from where drf-services spreads it into the
dispatch pools. Its headline use is a scoping ``spec.kwargs`` provider that reads
``view.kwargs`` (otherwise empty over MCP, so it mis-scopes for every caller).
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_call_async import handle_tools_call_async
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.registry.prompt_registry import PromptRegistry
from rest_framework_mcp.registry.resource_registry import ResourceRegistry
from rest_framework_mcp.registry.tool_registry import ToolRegistry
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.registry.types.tool_binding import ToolBinding
from rest_framework_mcp.schema.selector_tool_schema import build_selector_tool_input_schema
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema


def _ctx(*, tools: ToolRegistry) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=_ALICE),
        tools=tools,
        resources=ResourceRegistry(),
        prompts=PromptRegistry(),
        protocol_version="2025-11-25",
    )


def _scope_provider(view: Any) -> dict[str, Any]:
    """A scoping provider that reads the URL-derived ``project_pk`` off the view."""
    return {"scope": view.kwargs.get("project_pk")}


def _echo_scope_service(*, scope: Any = None) -> dict[str, Any]:
    """Echo the provider-derived scope back so the test can assert delivery."""
    return {"scope": scope}


# ---------- UrlKwarg value object ----------


def test_url_kwarg_json_schema_defaults() -> None:
    assert UrlKwarg("project_pk").json_schema() == {"type": "string"}


def test_url_kwarg_json_schema_full() -> None:
    kwarg = UrlKwarg("project_pk", type="integer", description="owning project", default=1)
    assert kwarg.json_schema() == {
        "type": "integer",
        "description": "owning project",
        "default": 1,
    }


# ---------- schema advertisement ----------


def test_service_tool_schema_includes_url_kwargs() -> None:
    binding = ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=_echo_scope_service, atomic=False),
        url_kwargs=(UrlKwarg("project_pk", type="integer"),),
    )
    schema = build_service_tool_input_schema(binding)
    assert schema["properties"]["project_pk"] == {"type": "integer"}


def test_selector_tool_schema_includes_url_kwargs() -> None:
    binding = SelectorToolBinding(
        name="t",
        description=None,
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda user: []),
        url_kwargs=(UrlKwarg("project_pk", description="owning project"),),
    )
    schema = build_selector_tool_input_schema(binding)
    assert schema["properties"]["project_pk"] == {"type": "string", "description": "owning project"}


# ---------- service dispatch: the provider-read case ----------


def test_service_url_kwarg_reaches_scoping_provider_via_view_kwargs() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": "P1"}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "P1"}


async def test_service_url_kwarg_reaches_provider_async() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {"project_pk": "P2"}}, _ctx(tools=tools)
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "P2"}


def test_service_url_kwarg_default_seeded_when_omitted() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk", default="DEFLT"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "DEFLT"}


def test_service_url_kwarg_omitted_without_default_leaves_provider_unseeded() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    # No project_pk, no default → view.kwargs empty → provider yields None.
    assert out["structuredContent"] == {"scope": None}


class _TitleSerializer(serializers.Serializer):
    title = serializers.CharField()


def test_service_url_kwarg_popped_so_reject_serializer_ignores_it() -> None:
    # A closed service (REJECT + serializer): the url kwarg must be stripped from
    # the params dispatch validates, or it would be flagged as an unknown field.
    captured: dict[str, Any] = {}

    def svc(*, data: dict[str, Any], scope: Any = None) -> dict[str, Any]:
        captured["data"] = data
        return {"scope": scope, "title": data["title"]}

    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(
                service=svc,
                input_serializer=_TitleSerializer,
                atomic=False,
                kwargs=_scope_provider,
            ),
            unknown_arguments=UnknownArguments.REJECT,
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"title": "hi", "project_pk": "P9"}}, _ctx(tools=tools)
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "P9", "title": "hi"}
    # ``project_pk`` never entered the validated payload.
    assert "project_pk" not in captured["data"]


# ---------- selector dispatch ----------


def _scoped_list_selector(user: Any, ceiling: int = 0) -> list[int]:
    """A DB-free LIST selector scoped by a provider-supplied ceiling."""
    return [n for n in (1, 5, 15) if n <= ceiling]


def _ceiling_provider(view: Any) -> dict[str, Any]:
    pk = view.kwargs.get("project_pk")
    return {"ceiling": int(pk) if pk is not None else 0}


def test_selector_url_kwarg_scopes_via_provider() -> None:
    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(
                kind=SelectorKind.LIST, selector=_scoped_list_selector, kwargs=_ceiling_provider
            ),
            unknown_arguments=UnknownArguments.REJECT,
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": "10"}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    # ceiling=10 → only 1 and 5; project_pk was accepted (known), not rejected.
    assert out["structuredContent"] == [1, 5]


class _ProjectExtras(dict): ...


def _extras_list_selector(user: Any, **extras: Any) -> list[int]:
    """A selector reading the URL kwarg straight from its extras."""
    ceiling = extras.get("project_pk")
    return [n for n in (1, 5, 15) if ceiling is not None and n <= int(ceiling)]


def test_selector_url_kwarg_delivered_to_extras_reading_selector() -> None:
    # Dual channel: the selector reads ``project_pk`` from ``**extras`` (fed by
    # drf-services' authoritative view-kwargs spread), and it is registered as a
    # UrlKwarg so the model can supply it.
    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_extras_list_selector),
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": "5"}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == [1, 5]


# ---------- in-process call_spec_tool ----------


def test_call_spec_tool_seeds_url_kwargs_into_view() -> None:
    server = MCPServer(auth_backend=AllowAnyBackend())
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
        url_kwargs=(UrlKwarg("project_pk"),),
    )
    result = server.call_tool("t", {"project_pk": "P3"}, user="alice")
    assert result.structured_content == {"scope": "P3"}


# ---------- registration-time validation ----------


def test_reserved_url_kwarg_name_rejected_on_service() -> None:
    with pytest.raises(ImproperlyConfigured, match="reserved transport keys"):
        ToolRegistry()  # touch to keep import used
        from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool

        service_spec_to_tool(
            name="t",
            spec=ServiceSpec(service=_echo_scope_service, atomic=False),
            url_kwargs=(UrlKwarg("page"),),
        )


def test_reserved_url_kwarg_name_rejected_on_selector() -> None:
    from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool

    with pytest.raises(ImproperlyConfigured, match="reserved transport keys"):
        selector_spec_to_tool(
            name="t",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda user: []),
            url_kwargs=(UrlKwarg("user"),),
        )


def test_duplicate_url_kwarg_name_rejected() -> None:
    from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool

    with pytest.raises(ImproperlyConfigured, match="duplicate url_kwargs"):
        service_spec_to_tool(
            name="t",
            spec=ServiceSpec(service=_echo_scope_service, atomic=False),
            url_kwargs=(UrlKwarg("project_pk"), UrlKwarg("project_pk", type="integer")),
        )


# ---------- required URL kwargs (drf-services 0.28) ----------


def test_service_tool_schema_marks_a_required_url_kwarg() -> None:
    binding = ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=_echo_scope_service, atomic=False),
        url_kwargs=(UrlKwarg("project_pk", type="integer", required=True),),
    )
    schema = build_service_tool_input_schema(binding)
    assert schema["required"] == ["project_pk"]


def test_service_tool_schema_required_survives_a_partial_spec() -> None:
    # ``partial`` relaxes the *payload* the serializer validates; a URL kwarg is
    # not part of that payload, so it stays required.
    binding = ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=_echo_scope_service, atomic=False, partial=True),
        url_kwargs=(UrlKwarg("project_pk", required=True),),
    )
    assert build_service_tool_input_schema(binding)["required"] == ["project_pk"]


def test_service_tool_schema_omits_required_when_none_are() -> None:
    binding = ToolBinding(
        name="t",
        description=None,
        spec=ServiceSpec(service=_echo_scope_service, atomic=False),
        url_kwargs=(UrlKwarg("project_pk"),),
    )
    assert "required" not in build_service_tool_input_schema(binding)


def test_selector_tool_schema_marks_a_required_url_kwarg() -> None:
    binding = SelectorToolBinding(
        name="t",
        description=None,
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda user: []),
        url_kwargs=(UrlKwarg("project_pk", required=True),),
    )
    assert build_selector_tool_input_schema(binding)["required"] == ["project_pk"]


def test_omitting_a_required_url_kwarg_is_a_validation_tool_result() -> None:
    # Schema ``required`` is a hint models routinely ignore, so the runtime has
    # to fail legibly: an ``isError`` tool result the caller can act on, not a
    # transport-level error.
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk", required=True),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["isError"] is True
    assert "project_pk" in out["content"][0]["text"]


def test_supplying_a_required_url_kwarg_dispatches_normally() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk", required=True),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": "P9"}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "P9"}


def test_registration_rejects_required_together_with_a_default() -> None:
    server = MCPServer(auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="cannot also be required"):
        server.register_service_tool(
            name="t",
            spec=ServiceSpec(service=_echo_scope_service, atomic=False),
            url_kwargs=(UrlKwarg("project_pk", default="P1", required=True),),
            permissions=[],
        )


def test_registration_still_rejects_a_reserved_pool_seed() -> None:
    # ``collection`` is only reserved because the seed set is now the sister
    # repo's — the local copy this replaced would have let it through.
    server = MCPServer(auth_backend=AllowAnyBackend())
    with pytest.raises(ImproperlyConfigured, match="reserved transport keys"):
        server.register_service_tool(
            name="t",
            spec=ServiceSpec(service=_echo_scope_service, atomic=False),
            url_kwargs=(UrlKwarg("collection"),),
            permissions=[],
        )


def test_in_process_call_tool_maps_a_missing_required_url_kwarg() -> None:
    # ``call_tool`` builds its offline context before enforcing permissions, so
    # the split cannot sit inside the dispatch ``try`` — this pins that the
    # in-process surface still returns a tool result rather than raising.
    server = MCPServer(auth_backend=AllowAnyBackend())
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
        url_kwargs=(UrlKwarg("project_pk", required=True),),
        permissions=[],
    )
    result = server.call_tool("t", {}, user="alice")
    assert result.is_error is True
    assert "project_pk" in result.content[0].text


def test_in_process_call_tool_succeeds_when_supplied() -> None:
    server = MCPServer(auth_backend=AllowAnyBackend())
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
        url_kwargs=(UrlKwarg("project_pk", required=True),),
        permissions=[],
    )
    result = server.call_tool("t", {"project_pk": "P4"}, user="alice")
    assert result.is_error is False
    assert result.structured_content == {"scope": "P4"}


async def test_async_handler_maps_a_missing_required_url_kwarg() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk", required=True),),
        )
    )
    out = await handle_tools_call_async({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["isError"] is True


def test_selector_tool_maps_a_missing_required_url_kwarg() -> None:
    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=lambda user, **kw: []),
            url_kwargs=(UrlKwarg("project_pk", required=True),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert out["isError"] is True


def test_output_context_provider_sees_the_url_kwargs() -> None:
    """The render-time view is the dispatch-time view, ``kwargs`` included.

    On HTTP one view instance serves the request, so a context provider reading
    ``view.kwargs`` to scope its query sees the route captures. Over MCP the
    render step used to build a fresh, kwargs-less ``OfflineServiceView``.
    """
    seen: dict[str, Any] = {}

    class _Out(serializers.Serializer):
        scope = serializers.SerializerMethodField()

        def get_scope(self, _: Any) -> Any:
            return self.context["scope"]

    def _ctx_provider(view: Any) -> dict[str, Any]:
        seen["kwargs"] = dict(view.kwargs)
        return {"scope": view.kwargs.get("project_pk")}

    tools = ToolRegistry()
    tools.register(
        SelectorToolBinding(
            name="t",
            description=None,
            spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                selector=lambda **kw: {},
                output_serializer=_Out,
                output_serializer_context=_ctx_provider,
            ),
            url_kwargs=(UrlKwarg("project_pk", type="integer"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": 7}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    assert seen["kwargs"] == {"project_pk": 7}
    assert out["structuredContent"]["scope"] == 7


def test_url_kwarg_is_a_superset_of_a_reflected_extras_key() -> None:
    """A reflected ``**extras`` key never reaches ``view.kwargs``; a UrlKwarg does.

    The distinction consumers get wrong: marking a reflected key
    ``InputRequired`` is a *schema* statement and doesn't change where the value
    lands, so a ``spec.kwargs`` provider scoping off ``view.kwargs`` silently
    sees ``None``. Registering the same name as a ``UrlKwarg`` is a strict
    superset — the selector still receives it in its extras.
    """
    seen: dict[str, Any] = {}

    def selector(user: Any, scope: Any = None, **extras: Any) -> list[Any]:
        seen["selector_project_pk"] = extras.get("project_pk")
        seen["scope"] = scope
        return []

    def provider(view: Any) -> dict[str, Any]:
        return {"scope": view.kwargs.get("project_pk")}

    def call(url_kwargs: tuple[UrlKwarg, ...]) -> None:
        seen.clear()
        tools = ToolRegistry()
        tools.register(
            SelectorToolBinding(
                name="t",
                description=None,
                spec=SelectorSpec(kind=SelectorKind.LIST, selector=selector, kwargs=provider),
                url_kwargs=url_kwargs,
            )
        )
        handle_tools_call({"name": "t", "arguments": {"project_pk": 7}}, _ctx(tools=tools))

    # Undeclared: the selector gets it as an ordinary param, the provider doesn't.
    call(())
    assert seen == {"selector_project_pk": 7, "scope": None}

    # Declared: it routes through ``view.kwargs`` *and* still reaches the selector.
    call((UrlKwarg("project_pk", type="integer"),))
    assert seen == {"selector_project_pk": 7, "scope": 7}


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


# ---------- an explicit null is not a supplied value ----------


def _presence_provider(view: Any) -> dict[str, Any]:
    """Report whether the key reached ``view.kwargs`` at all, not just its value."""
    return {"scope": "present" if "project_pk" in view.kwargs else "absent"}


def _required_binding(**kwargs: Any) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_scope_provider),
            url_kwargs=(UrlKwarg("project_pk", **kwargs),),
        )
    )
    return tools


def test_explicit_null_does_not_satisfy_a_required_url_kwarg() -> None:
    # A route capture can never be null over HTTP; off-HTTP a model emitting
    # ``{"project_pk": null}`` is saying it has no value. Accepting it as
    # supplied both defeats ``required`` and reaches the ORM as ``IS NULL`` — an
    # unscoped read that answers successfully with the wrong rows.
    out = handle_tools_call(
        {"name": "t", "arguments": {"project_pk": None}},
        _ctx(tools=_required_binding(required=True)),
    )
    assert isinstance(out, dict)
    assert out["isError"] is True
    assert "project_pk" in out["content"][0]["text"]


async def test_explicit_null_does_not_satisfy_a_required_url_kwarg_async() -> None:
    out = await handle_tools_call_async(
        {"name": "t", "arguments": {"project_pk": None}},
        _ctx(tools=_required_binding(required=True)),
    )
    assert isinstance(out, dict)
    assert out["isError"] is True
    assert "project_pk" in out["content"][0]["text"]


def test_explicit_null_falls_through_to_the_declared_default() -> None:
    out = handle_tools_call(
        {"name": "t", "arguments": {"project_pk": None}},
        _ctx(tools=_required_binding(default="DEFLT")),
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "DEFLT"}


def test_explicit_null_without_a_default_never_reaches_view_kwargs() -> None:
    tools = ToolRegistry()
    tools.register(
        ToolBinding(
            name="t",
            description=None,
            spec=ServiceSpec(service=_echo_scope_service, atomic=False, kwargs=_presence_provider),
            url_kwargs=(UrlKwarg("project_pk"),),
        )
    )
    out = handle_tools_call({"name": "t", "arguments": {"project_pk": None}}, _ctx(tools=tools))
    assert isinstance(out, dict)
    # Seeding ``None`` here is what turns ``.filter(project_id=None)`` into an
    # unscoped ``IS NULL`` read, so the key stays out of the mapping entirely.
    assert out["structuredContent"] == {"scope": "absent"}


def test_a_falsy_but_real_url_kwarg_value_is_still_supplied() -> None:
    out = handle_tools_call(
        {"name": "t", "arguments": {"project_pk": 0}}, _ctx(tools=_required_binding(required=True))
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": 0}
