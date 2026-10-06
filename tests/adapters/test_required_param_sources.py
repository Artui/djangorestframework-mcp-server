"""What registration counts as the source of a required callable parameter.

``validate_input_serializer_against_callable`` refuses a callable whose required
parameter nothing on the MCP transport fills, because every call to such a tool
raises ``TypeError`` out of dispatch. These tests hold the names it must *not*
count -- a reserved pool seed in trust mode, ``data`` with no
``input_serializer`` -- and the one it must: a ``UrlKwarg`` that is in the
selector's pool on every call that dispatches.

The ones that register are called as well, so a source counted here is a value
the callable really receives.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.adapters.selector_to_tool import selector_spec_to_tool
from rest_framework_mcp.adapters.service_to_tool import service_spec_to_tool
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

_SPREADING = [ArgumentBinding.SPREAD_AUTHOR_WINS, ArgumentBinding.SPREAD_CALLER_WINS]

# The sentence the refusal adds when ``data`` is among the missing names.
_DATA_HINT = "Nothing fills `data` without an input_serializer"


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


# ---------- trust mode: a reserved seed is not something the caller supplies ----------


def _rename_task(*, instance: Any, title: str) -> Any: ...  # noqa: ARG001


@pytest.mark.parametrize("binding", _SPREADING)
def test_trust_mode_does_not_count_a_reserved_seed_as_the_callers(binding: Any) -> None:
    # No ``input_serializer`` and a spreading binding: the client's arguments are
    # spread verbatim, so ``title`` is the caller's to send. ``instance`` is not --
    # drf-services strips every reserved name from the spread -- and no lookup
    # resolves one, so every call would raise ``TypeError``.
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['instance'\]"):
        service_spec_to_tool(
            name="rename",
            spec=ServiceSpec(service=_rename_task, atomic=False),
            argument_binding=binding,
        )


def _needs_instance(*, instance: Any) -> None: ...  # noqa: ARG001


def _needs_collection(*, collection: Any) -> None: ...  # noqa: ARG001


def _needs_serializer(*, serializer: Any) -> None: ...  # noqa: ARG001


def _needs_data(*, data: Any) -> None: ...  # noqa: ARG001


@pytest.mark.parametrize(
    ("selector", "seed"),
    [
        (_needs_instance, "instance"),
        (_needs_collection, "collection"),
        (_needs_serializer, "serializer"),
        (_needs_data, "data"),
    ],
)
@pytest.mark.parametrize("binding", _SPREADING)
def test_a_trust_mode_selector_requiring_a_seed_nothing_fills_is_refused(
    binding: Any, selector: Any, seed: str
) -> None:
    # A selector tool resolves no target and has no ``input_serializer`` here, so
    # none of these reaches its pool, whatever the caller sends.
    with pytest.raises(ImproperlyConfigured, match=rf"parameter\(s\) \['{seed}'\]"):
        selector_spec_to_tool(
            name="read",
            spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
            argument_binding=binding,
        )


# ---------- ``data`` is a source only beside an ``input_serializer`` ----------


def _create_note(*, data: Any) -> Any:
    return {"saved": data}


def test_a_bundled_service_requiring_data_without_an_input_serializer_is_refused() -> None:
    # ``BUNDLE`` with no serializer: drf-services seeds ``data`` only from a
    # validated serializer or from ``PASSTHROUGH`` extras, and this transport
    # forwards no extras for that binding, so ``data`` never arrives.
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['data'\]") as caught:
        service_spec_to_tool(name="note", spec=ServiceSpec(service=_create_note, atomic=False))
    assert _DATA_HINT in str(caught.value)


@pytest.mark.parametrize("binding", _SPREADING)
def test_a_trust_mode_service_requiring_data_is_refused(binding: Any) -> None:
    # Spread with no serializer, ``data`` is only the arguments the call happened
    # to carry, so a call with none leaves it unfilled.
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['data'\]") as caught:
        service_spec_to_tool(
            name="note",
            spec=ServiceSpec(service=_create_note, atomic=False),
            argument_binding=binding,
        )
    assert _DATA_HINT in str(caught.value)


def test_the_data_hint_accompanies_only_a_missing_data() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['instance'\]") as caught:
        service_spec_to_tool(name="t", spec=ServiceSpec(service=_needs_instance, atomic=False))
    assert _DATA_HINT not in str(caught.value)


class _NoteIn(drf_serializers.Serializer):
    text = drf_serializers.CharField()


async def test_a_bundled_service_requiring_data_registers_beside_a_serializer() -> None:
    server = _server()
    server.register_service_tool(
        name="note",
        description="Save a note.",
        spec=ServiceSpec(service=_create_note, input_serializer=_NoteIn, atomic=False),
    )
    out = await server.acall_tool("note", {"text": "hello"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"saved": {"text": "hello"}}


# ---------- a ``UrlKwarg`` present on every dispatch is a selector source ----------


class _TaskFilter(drf_serializers.Serializer):
    status = drf_serializers.ChoiceField(choices=["open", "done"], default="open")


def _tasks_in_project(*, project_pk: Any, status: str) -> list[dict[str, Any]]:
    return [{"project": project_pk, "status": status}]


def _register_tasks(server: MCPServer, url_kwarg: UrlKwarg) -> None:
    server.register_selector_tool(
        name="tasks",
        description="List a project's tasks.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_tasks_in_project),
        input_serializer=_TaskFilter,
        paginate=True,
        url_kwargs=(url_kwarg,),
    )


async def test_a_required_url_kwarg_fills_a_required_selector_parameter() -> None:
    # A call omitting it is refused before dispatch, so every call that reaches
    # the selector carries it through the ``view.kwargs`` spread.
    server = _server()
    _register_tasks(server, UrlKwarg("project_pk", required=True))
    out = await server.acall_tool("tasks", {"project_pk": "7", "status": "done"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"]["items"] == [{"project": "7", "status": "done"}]


async def test_a_defaulted_url_kwarg_fills_a_required_selector_parameter() -> None:
    server = _server()
    _register_tasks(server, UrlKwarg("project_pk", default=3))
    out = await server.acall_tool("tasks", {}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"]["items"] == [{"project": 3, "status": "open"}]


@pytest.mark.parametrize(
    "url_kwarg",
    [
        pytest.param(UrlKwarg("project_pk"), id="neither-required-nor-defaulted"),
        # The splits seed no ``None`` default, so the parameter would be unfilled.
        pytest.param(UrlKwarg("project_pk", default=None), id="defaults-to-none"),
    ],
)
def test_a_url_kwarg_a_call_may_omit_is_no_source(url_kwarg: UrlKwarg) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['project_pk'\]"):
        _register_tasks(_server(), url_kwarg)


def _move_task(*, project_pk: Any, data: Any) -> Any: ...  # noqa: ARG001


class _MoveIn(drf_serializers.Serializer):
    title = drf_serializers.CharField()


def test_a_url_kwarg_is_no_source_for_a_service_parameter() -> None:
    # drf-services spreads ``view.kwargs`` into a target lookup's pool, never into
    # the service's, so a service parameter it names stays unfilled.
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['project_pk'\]"):
        _server().register_service_tool(
            name="move",
            description="Move a task.",
            spec=ServiceSpec(service=_move_task, input_serializer=_MoveIn, atomic=False),
            url_kwargs=(UrlKwarg("project_pk", required=True),),
        )


# ---------- only a parameter a keyword can fill is counted ----------


def _tag_note(*tags: Any, data: Any) -> Any:
    return {"tags": list(tags), "saved": data}


async def test_a_var_positional_parameter_needs_no_source() -> None:
    # ``*tags`` has no default, so a check counting every parameter would call
    # it required with no source; dispatch binds by keyword and leaves it empty.
    server = _server()
    server.register_service_tool(
        name="note",
        description="Save a note.",
        spec=ServiceSpec(service=_tag_note, input_serializer=_NoteIn, atomic=False),
    )
    out = await server.acall_tool("note", {"text": "hello"}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"tags": [], "saved": {"text": "hello"}}
