"""A selector's ``UrlKwarg`` that only a bare ``**kwargs`` could take is refused.

drf-services hands a catch-all the arguments its callable declares and the
seeds, and nothing else, so a route capture the selector names nowhere is in
``view.kwargs`` and never in ``kwargs``. In 0.52.1 the catch-all received it.
The tool registered, advertised the capture and dropped it on every call, so
registration refuses it, naming the tool and the capture, on every registrar.

What still registers, each case holding one condition of the guard
(``adapters.utils.validate_selector_url_kwargs_reach``): a selector naming the
capture as a parameter or a typed ``**extras: Unpack[TypedDict]`` key; one with
no catch-all at all, whose capture is there for ``view.kwargs`` readers; and one
whose ``kwargs=`` provider fills the name or whose ``spec_kwargs_provides=``
claims it.
"""

from __future__ import annotations

from typing import Annotated, Any

import django_filters
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from django_service_specs import SelectorKind, SpecRegistry
from rest_framework.permissions import AllowAny
from rest_framework_services import NotClientInput, SelectorSpec
from rest_framework_services import UrlKwarg as SpecUrlKwarg
from typing_extensions import NotRequired, TypedDict, Unpack

from rest_framework_mcp import UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.server.mcp_server import MCPServer
from tests.testapp.models import Invoice

_ROUTES = ["spec-reads", "mount-url-kwargs", "decorator", "register_specs"]
_MESSAGE = r"(?s)selector tool 'op'.*UrlKwarg\(s\) \['project_pk'\].*\*\*kwargs.*view\.kwargs"


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


def _register(
    server: MCPServer,
    selector: Any,
    *,
    route: str = "spec-reads",
    **spec_kwargs: Any,
) -> None:
    capture = SpecUrlKwarg("project_pk", "integer")
    if route == "decorator":
        server.selector_tool(
            name="op",
            kind=SelectorKind.LIST,
            url_kwargs=(UrlKwarg("project_pk", type="integer"),),
            permissions=[],
        )(selector)
        return
    reads = [] if route == "mount-url-kwargs" else [capture]
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=selector,
        permission_classes=[AllowAny],
        reads=reads,
        **spec_kwargs,
    )
    if route == "register_specs":
        registry = SpecRegistry()
        registry.register("op", spec)
        server.register_specs(registry, overrides={"op": {"permissions": []}})
        return
    mount: dict[str, Any] = {}
    if route == "mount-url-kwargs":
        mount["url_kwargs"] = (UrlKwarg("project_pk", type="integer"),)
    server.register_selector_tool(name="op", spec=spec, permissions=[], **mount)


def _call(server: MCPServer, arguments: dict[str, Any]) -> Any:
    ctx = MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )
    return handle_tools_call({"name": "op", "arguments": arguments}, ctx)


def _catch_all(**kwargs: Any) -> list[Any]:
    return []


@pytest.mark.filterwarnings("ignore::DeprecationWarning", "ignore::UserWarning")
@pytest.mark.parametrize("route", _ROUTES)
def test_a_capture_only_a_catch_all_could_take_is_refused(route: str) -> None:
    server = _server()

    with pytest.raises(ImproperlyConfigured, match=_MESSAGE):
        _register(server, _catch_all, route=route)

    assert server.tools.get("op") is None


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_an_annotated_catch_all_that_is_no_typed_dict_is_refused() -> None:
    # ``**kwargs: Any`` names no key, so it is as open as a bare one.
    def selector(**kwargs: Any) -> list[Any]:
        return []

    with pytest.raises(ImproperlyConfigured, match=_MESSAGE):
        _register(_server(), selector)


class _Route(TypedDict):
    project_pk: NotRequired[int]


@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore::UserWarning")
@pytest.mark.parametrize("shape", ["parameter", "typed-extras"])
def test_a_capture_the_selector_names_reaches_it(shape: str) -> None:
    seen: dict[str, Any] = {}

    def by_parameter(*, project_pk: int = 0, **kwargs: Any) -> list[Any]:
        seen["project_pk"] = project_pk
        return []

    def by_typed_extras(**extras: Unpack[_Route]) -> list[Any]:
        seen["project_pk"] = extras.get("project_pk")
        return []

    server = _server()
    _register(server, by_parameter if shape == "parameter" else by_typed_extras)

    _call(server, {"project_pk": 3})

    assert seen["project_pk"] == 3


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_selector_with_no_catch_all_registers() -> None:
    # The capture is there for what reads ``view.kwargs`` (a permission class,
    # the ``kwargs=`` provider), and a selector naming nothing expects nothing.
    def selector() -> list[Any]:
        return []

    server = _server()
    _register(server, selector)

    assert server.tools.get("op") is not None


class _FromRoute(TypedDict):
    project_pk: int


def _project_from_route(view: Any) -> _FromRoute:
    return {"project_pk": int(view.kwargs["project_pk"])}


@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_capture_the_provider_fills_reaches_the_catch_all() -> None:
    seen: dict[str, Any] = {}

    def selector(**kwargs: Any) -> list[Any]:
        seen.update(kwargs)
        return []

    server = _server()
    _register(server, selector, kwargs=_project_from_route)

    _call(server, {"project_pk": 3})

    assert seen["project_pk"] == 3


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_capture_spec_kwargs_provides_claims_registers() -> None:
    server = _server()
    spec = SelectorSpec(
        kind=SelectorKind.LIST,
        selector=_catch_all,
        permission_classes=[AllowAny],
        reads=[SpecUrlKwarg("project_pk", "integer")],
    )

    server.register_selector_tool(
        name="op", spec=spec, permissions=[], spec_kwargs_provides=("project_pk",)
    )

    assert server.tools.get("op") is not None


def _unreadable_provider(view):  # noqa: ANN001, ANN202 - no annotation on purpose
    return {"project_pk": int(view.kwargs["project_pk"])}


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_provider_whose_keys_cannot_be_read_is_not_second_guessed() -> None:
    # With no ``TypedDict`` to read, the provider may fill the capture, so the
    # catch-all may receive it.
    server = _server()
    _register(server, _catch_all, kwargs=_unreadable_provider)

    assert server.tools.get("op") is not None


@pytest.mark.django_db
@pytest.mark.filterwarnings("ignore::DeprecationWarning", "ignore::UserWarning")
def test_a_capture_a_server_owned_parameter_takes_reaches_it() -> None:
    # The reflection leaves out a parameter marked ``NotClientInput``, since no
    # caller sends it, but a mount's capture is the route's and reaches it.
    seen: dict[str, Any] = {}

    def selector(*, project_pk: Annotated[int, NotClientInput] = 0, **kwargs: Any) -> list[Any]:
        seen["project_pk"] = project_pk
        return []

    server = _server()
    _register(server, selector, route="mount-url-kwargs")

    _call(server, {"project_pk": 3})

    assert seen["project_pk"] == 3


class _ProjectFilter(django_filters.FilterSet):
    project_pk = django_filters.NumberFilter(field_name="pk")

    class Meta:
        model = Invoice
        fields: list[str] = []


@pytest.mark.filterwarnings("ignore::UserWarning")
def test_a_capture_named_like_a_filter_is_refused_too() -> None:
    # A filter reads the call's arguments, and a capture is removed from them
    # on its way to ``view.kwargs``, so a filter of its name filters nothing.
    with pytest.raises(ImproperlyConfigured, match=_MESSAGE):
        _register(_server(), _catch_all, filter_set=_ProjectFilter)
