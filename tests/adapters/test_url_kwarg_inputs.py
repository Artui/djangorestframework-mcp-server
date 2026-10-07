"""A ``UrlKwarg`` may not take an input of a service tool that never receives it.

A ``UrlKwarg``'s value is popped from the arguments and seeded into
``view.kwargs``, which drf-services hands to a service tool's target lookup and
to its ``kwargs=`` provider, and never to the service. So a spread service's own
parameter of the same name registered, was advertised as required, and then
answered every call "Missing required argument(s): 'project_pk'" for an
argument the call carried; with a default it ran on the default, silently. An
``input_serializer`` field of the same name validates the arguments left once
the split has run, so it was answered "This field is required.". Registration
refuses both, reading the names off the sets the ``QueryParam`` refusal reads.

What does receive the value is called rather than assumed: a target lookup's
parameter, a parameter the service's own typed provider fills from
``view.kwargs``, and a ``UrlKwarg`` no input of the service takes.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec
from typing_extensions import TypedDict

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

_SPREADING = [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]

_REMEDIES = (
    "Fill the parameter from view.kwargs with a kwargs= provider whose TypedDict declares it",
    "take the value as a parameter of the spec's target lookup and drop the input",
    "drop the UrlKwarg",
)


_PROJECT = UrlKwarg("project_pk", required=True)


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _register(
    server: MCPServer,
    spec: ServiceSpec,
    *,
    url_kwarg: UrlKwarg = _PROJECT,
    **kwargs: Any,
) -> Any:
    return server.register_service_tool(
        name="archive",
        description="Archive a project.",
        spec=spec,
        url_kwargs=(url_kwarg,),
        **kwargs,
    )


def _assert_refused(caught: pytest.ExceptionInfo[ImproperlyConfigured], taken_by: str) -> None:
    """``taken_by`` opens the sentence the refusal names the inputs in."""
    message = str(caught.value)
    assert f"service tool 'archive': {taken_by} that the tool also declares as a UrlKwarg." in (
        message
    )
    positions = [message.index(remedy) for remedy in _REMEDIES]
    assert positions == sorted(positions)


# ---------- refused: the value never arrives ----------


def _archive_project(*, project_pk: int, reason: str = "") -> dict[str, Any]:
    return {"project_pk": project_pk, "reason": reason}


def _archive_project_defaulted(*, project_pk: int = 0, reason: str = "") -> dict[str, Any]:
    return {"project_pk": project_pk, "reason": reason}


@pytest.mark.parametrize(
    "service",
    [
        # Every call answered "Missing required argument(s): 'project_pk'".
        pytest.param(_archive_project, id="required"),
        # Every call archived project 0, whatever project it named.
        pytest.param(_archive_project_defaulted, id="defaulted"),
    ],
)
@pytest.mark.parametrize("binding", _SPREADING)
def test_a_spread_service_parameter_a_url_kwarg_takes_is_refused(
    service: Any, binding: ArgumentBinding
) -> None:
    spec = ServiceSpec(service=service, atomic=False)
    with pytest.raises(ImproperlyConfigured) as caught:
        _register(_server(), spec, argument_binding=binding)
    _assert_refused(caught, "the service declares parameter(s) ['project_pk']")


class _ArchiveIn(drf_serializers.Serializer):
    project_pk = drf_serializers.IntegerField()
    reason = drf_serializers.CharField(required=False)


def _archive(*, data: Any) -> Any:
    return data


@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, *_SPREADING])
def test_a_serializer_field_a_url_kwarg_takes_is_refused(binding: ArgumentBinding) -> None:
    # The serializer validates what the split leaves, so the field was answered
    # "This field is required." for an argument the call carried.
    spec = ServiceSpec(service=_archive, atomic=False, input_serializer=_ArchiveIn)
    with pytest.raises(ImproperlyConfigured) as caught:
        _register(_server(), spec, argument_binding=binding)
    _assert_refused(caught, "the service's input_serializer declares field(s) ['project_pk']")


# ---------- allowed: the value arrives ----------


def _project(*, project_pk: int) -> SimpleNamespace:
    return SimpleNamespace(pk=project_pk)


def _archive_instance(*, instance: Any) -> dict[str, Any]:
    return {"project_pk": instance.pk}


@pytest.mark.parametrize(
    "url_kwarg",
    [
        pytest.param(_PROJECT, id="required"),
        # A default is a name the server fills, so the lookup's reflection
        # leaves it out unless the sets are read without the declarations; read
        # with them, the name fell to the service's own group and was refused.
        pytest.param(UrlKwarg("project_pk", default=1), id="defaulted"),
    ],
)
@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, *_SPREADING])
async def test_a_target_lookup_parameter_a_url_kwarg_takes_is_served(
    binding: ArgumentBinding, url_kwarg: UrlKwarg
) -> None:
    # drf-services spreads ``view.kwargs`` into the lookup's pool, so the
    # lookup resolves the project the call named.
    server = _server()
    spec = ServiceSpec(
        service=_archive_instance,
        atomic=False,
        instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_project),
    )
    _register(server, spec, url_kwarg=url_kwarg, argument_binding=binding)
    out = await server.acall_tool("archive", {"project_pk": 8}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"project_pk": 8}


class _FromRoute(TypedDict):
    project_pk: int


def _project_from_route(view: Any) -> _FromRoute:
    return {"project_pk": int(view.kwargs["project_pk"])}


def _archive_by_pk(*, project_pk: int) -> dict[str, Any]:
    return {"project_pk": project_pk}


@pytest.mark.parametrize("binding", _SPREADING)
async def test_a_parameter_the_services_provider_fills_from_the_route_is_served(
    binding: ArgumentBinding,
) -> None:
    # The first remedy the refusal offers. Under ``SPREAD_CALLER_WINS`` the
    # schema still offers ``project_pk`` as the service's, so the subtraction of
    # what the provider fills is what exempts it there.
    server = _server()
    spec = ServiceSpec(service=_archive_by_pk, atomic=False, kwargs=_project_from_route)
    _register(server, spec, argument_binding=binding)
    out = await server.acall_tool("archive", {"project_pk": 8}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"project_pk": 8}


class _Scope(TypedDict):
    scope: str


def _scope_from_route(view: Any) -> _Scope:
    return {"scope": f"project-{view.kwargs['project_pk']}"}


def _archive_in_scope(*, scope: str = "") -> dict[str, Any]:
    return {"scope": scope}


@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, *_SPREADING])
async def test_a_url_kwarg_no_input_takes_registers(binding: ArgumentBinding) -> None:
    # The schema lists every ``UrlKwarg`` as a property, so a check reading it
    # with the declarations still in would count ``project_pk`` as the
    # service's and refuse the declaration the docs show.
    server = _server()
    spec = ServiceSpec(service=_archive_in_scope, atomic=False, kwargs=_scope_from_route)
    _register(server, spec, argument_binding=binding)
    out = await server.acall_tool("archive", {"project_pk": 8}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"scope": "project-8"}
