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


# ---------- a selector is never handed ``data`` or ``serializer`` ----------

# drf-services' selector dispatch seeds neither name and strips both from the
# spread, whatever the binding, so a selector requiring one registered and then
# raised ``TypeError`` on every call; under ``BUNDLE`` no validated value reaches
# a selector at all, so a ``**kwargs`` one ran with none of the payload.

_NEVER_HANDED = "A selector is never handed `data` or `serializer`"


class _Status(drf_serializers.Serializer):
    status = drf_serializers.CharField(default="open")


class _FieldNamedData(drf_serializers.Serializer):
    data = drf_serializers.CharField()


# Each takes ``status``, so the field check passes and the source check is the
# one that answers.
def _status_and_data(*, status: str, data: Any) -> Any: ...  # noqa: ARG001


def _status_and_serializer(*, status: str, serializer: Any) -> Any: ...  # noqa: ARG001


def _takes_anything(**kwargs: Any) -> Any: ...  # noqa: ARG001


def _register_selector(selector: Any, input_serializer: Any, binding: ArgumentBinding) -> Any:
    return _server().register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=selector),
        input_serializer=input_serializer,
        argument_binding=binding,
    )


@pytest.mark.parametrize(
    ("selector", "input_serializer", "missing"),
    [
        pytest.param(_status_and_data, _Status, "data", id="data"),
        pytest.param(_status_and_serializer, _Status, "serializer", id="serializer"),
        # The spread strips the reserved name, so a field declared under it is
        # no source either.
        pytest.param(_needs_data, _FieldNamedData, "data", id="a-field-named-data"),
    ],
)
@pytest.mark.parametrize("binding", _SPREADING)
def test_a_spreading_selector_requiring_data_or_serializer_is_refused(
    binding: ArgumentBinding, selector: Any, input_serializer: Any, missing: str
) -> None:
    with pytest.raises(ImproperlyConfigured, match=rf"parameter\(s\) \['{missing}'\]") as caught:
        _register_selector(selector, input_serializer, binding)
    assert _NEVER_HANDED in str(caught.value)
    # The service remedy would misdirect: declaring an input_serializer fills
    # nothing for a selector.
    assert _DATA_HINT not in str(caught.value)


@pytest.mark.parametrize("binding", [ArgumentBinding.BUNDLE, *_SPREADING])
def test_a_selector_requiring_data_without_a_serializer_gets_the_selector_remedy(
    binding: ArgumentBinding,
) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['data'\]") as caught:
        _register_selector(_needs_data, None, binding)
    assert _NEVER_HANDED in str(caught.value)
    assert _DATA_HINT not in str(caught.value)


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(_needs_data, id="data"),
        pytest.param(_needs_serializer, id="serializer"),
        pytest.param(_takes_anything, id="var-keyword"),
    ],
)
def test_a_bundled_selector_with_an_input_serializer_is_refused(selector: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match="argument_binding=BUNDLE") as caught:
        _register_selector(selector, _Status, ArgumentBinding.BUNDLE)
    assert "no way to reach the selector" in str(caught.value)


def test_a_bundled_selector_without_an_input_serializer_still_registers() -> None:
    # Nothing validated, so nothing to lose: its arguments come from the route
    # and the provider, as under ``BUNDLE`` they always do.
    _register_selector(lambda **kwargs: None, None, ArgumentBinding.BUNDLE)


def test_a_bundled_service_with_an_input_serializer_still_needs_a_way_to_take_it() -> None:
    # ``_validate_data_only`` is a service's rule still.
    def _no_payload(*, other: Any = None) -> Any: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured, match="requires the callable to declare a `data`"):
        service_spec_to_tool(
            name="note",
            spec=ServiceSpec(service=_no_payload, input_serializer=_NoteIn, atomic=False),
        )


def test_a_spreading_selector_taking_the_fields_as_parameters_registers() -> None:
    async def _ok(*, status: str) -> Any: ...  # noqa: ARG001

    _register_selector(_ok, _Status, ArgumentBinding.SPREAD_AUTHOR_WINS)


@pytest.mark.parametrize("binding", _SPREADING)
def test_a_selectors_data_parameter_does_not_take_the_fields_it_leaves_out(
    binding: ArgumentBinding,
) -> None:
    # A defaulted ``data`` passes the source check, and once exempted the
    # selector from the field check too, as if the payload arrived under it.
    async def _data_defaulted(*, data: Any = None) -> Any: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured, match=r"declares field\(s\) \['status'\]") as caught:
        _register_selector(_data_defaulted, _Status, binding)
    assert _NEVER_HANDED in str(caught.value)


def test_a_services_data_parameter_still_takes_the_payload_whole() -> None:
    def _bundled(*, data: Any) -> Any: ...  # noqa: ARG001

    service_spec_to_tool(
        name="note",
        spec=ServiceSpec(service=_bundled, input_serializer=_NoteIn, atomic=False),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    )


def test_the_field_check_offers_a_service_no_selector_remedy() -> None:
    def _other(*, other: Any = None) -> Any: ...  # noqa: ARG001

    with pytest.raises(ImproperlyConfigured, match=r"declares field\(s\) \['text'\]") as caught:
        service_spec_to_tool(
            name="note",
            spec=ServiceSpec(service=_other, input_serializer=_NoteIn, atomic=False),
            argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        )
    assert _NEVER_HANDED not in str(caught.value)


# ---------- a positional-only parameter is never filled ----------


def _positional(x: Any, /, *, status: str = "a") -> Any: ...  # noqa: ARG001


def _positional_beside_var_keyword(x: Any, /, **kwargs: Any) -> Any: ...  # noqa: ARG001


def _positional_with_a_default(x: Any = 1, /, *, status: str = "a") -> Any: ...  # noqa: ARG001


@pytest.mark.parametrize(
    "callable_",
    [
        pytest.param(_positional, id="keyword-only-beside"),
        # A catch-all takes the argument named ``x`` into ``kwargs``, never
        # into the positional slot, so it is no source either.
        pytest.param(_positional_beside_var_keyword, id="var-keyword-beside"),
    ],
)
@pytest.mark.parametrize("kind", ["selector", "service"])
def test_a_required_positional_only_parameter_is_refused(kind: str, callable_: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"positional-only parameter\(s\) \['x'\]"):
        if kind == "selector":
            _register_selector(callable_, None, ArgumentBinding.SPREAD_AUTHOR_WINS)
        else:
            service_spec_to_tool(
                name="t",
                spec=ServiceSpec(service=callable_, atomic=False),
                argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
            )


async def test_a_defaulted_positional_only_parameter_registers_and_runs_on_its_default() -> None:
    seen: list[Any] = []

    def _read(x: Any = 1, /, *, status: str = "a") -> Any:
        seen.append((x, status))
        return {"x": x}

    server = _server()
    server.register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_read),
    )
    out = await server.acall_tool("read", {"status": "b"}, user=None)
    assert isinstance(out, dict)
    assert seen == [(1, "b")]


def _status_and_instance(*, status: str, instance: Any) -> Any: ...  # noqa: ARG001


def test_the_selector_remedy_accompanies_only_data_or_serializer() -> None:
    with pytest.raises(ImproperlyConfigured, match=r"parameter\(s\) \['instance'\]") as caught:
        _register_selector(_status_and_instance, _Status, ArgumentBinding.SPREAD_AUTHOR_WINS)
    assert _NEVER_HANDED not in str(caught.value)
