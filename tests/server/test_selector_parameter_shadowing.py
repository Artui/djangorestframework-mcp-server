"""``register_selector_tool`` refuses a selector parameter the transport takes away.

``page`` and ``limit`` belong to the read pipeline, and a ``QueryParam``'s value
is routed to ``request.query_params``; both are removed from the arguments
before the selector is called. A selector parameter of either name registers,
is advertised, and then never receives what the caller sent: a required one is
answered "This field is required." for an argument the call carried, a
defaulted one runs silently on its default. So registration refuses it, as it
already refuses a ``QueryParam`` / ``UrlKwarg`` named ``page`` or ``limit``,
except where the tool's ``input_serializer`` lays the name back over the
arguments, which is the one case the selector does receive the value.

The two refusals are worded apart, and each test asserts which one answered.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from rest_framework import serializers as drf_serializers
from rest_framework_dataclasses.serializers import DataclassSerializer
from rest_framework_services import OfflineContract, QueryParam
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer, UrlKwarg
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

_PAGINATION_WORDING = "belong to the read pipeline's pagination"
_QUERY_PARAM_WORDING = "also declares as a QueryParam"


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _register(selector: Any, *, kind: SelectorKind = SelectorKind.LIST, **kwargs: Any) -> Any:
    return _server().register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=kind, selector=selector),
        **kwargs,
    )


# ---------- ``page`` / ``limit`` ----------


def _recent_entries_page(*, page: int = 1) -> list[Any]:
    return [page]


def _recent_entries_limit(*, limit: int = 10) -> list[Any]:
    return [limit]


@pytest.mark.parametrize(
    ("selector", "name"),
    [(_recent_entries_page, "page"), (_recent_entries_limit, "limit")],
)
def test_a_defaulted_pagination_named_parameter_is_refused(selector: Any, name: str) -> None:
    # The silent case: every call runs on the default whatever the caller asks for.
    with pytest.raises(ImproperlyConfigured, match=rf"\['{name}'\]") as caught:
        _register(selector, paginate=True)
    message = str(caught.value)
    assert "selector tool 'read'" in message
    assert _PAGINATION_WORDING in message
    assert _QUERY_PARAM_WORDING not in message


def _page_of_log(*, page: int) -> list[Any]:
    return [page]


def test_a_required_pagination_named_parameter_is_refused_in_trust_mode() -> None:
    # No ``input_serializer``, so the source check counts ``page`` as the
    # caller's; this refusal is the one that answers.
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\]") as caught:
        _register(_page_of_log, paginate=True)
    assert _PAGINATION_WORDING in str(caught.value)


def test_a_pagination_named_parameter_is_refused_on_an_unpaginated_tool() -> None:
    # The names are stripped from the selector's arguments whether or not the
    # tool paginates, so ``paginate`` is no condition of the refusal.
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\]") as caught:
        _register(_recent_entries_page, kind=SelectorKind.RETRIEVE)
    assert _PAGINATION_WORDING in str(caught.value)


# ---------- a ``QueryParam`` of the same name ----------


def _tasks_by_status(*, status: str) -> list[Any]:
    return [status]


def _tasks_by_status_defaulted(*, status: str = "open") -> list[Any]:
    return [status]


@pytest.mark.parametrize(
    "selector",
    [
        pytest.param(_tasks_by_status, id="required"),
        pytest.param(_tasks_by_status_defaulted, id="defaulted"),
    ],
)
def test_a_parameter_a_query_param_shadows_is_refused(selector: Any) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"\['status'\]") as caught:
        _register(selector, paginate=True, query_params=(QueryParam("status"),))
    message = str(caught.value)
    assert _QUERY_PARAM_WORDING in message
    assert _PAGINATION_WORDING not in message


def test_a_query_param_from_the_agent_contract_shadows_too() -> None:
    # The check reads the tool's effective channels, so a QueryParam the
    # registry entry's contract supplies is held to it as an explicit one is.
    with pytest.raises(ImproperlyConfigured, match=_QUERY_PARAM_WORDING):
        _register(
            _tasks_by_status,
            paginate=True,
            agent_contract=OfflineContract(query_params=(QueryParam("status"),)),
        )


# ---------- what stays allowed ----------


def test_a_url_kwarg_sharing_a_parameter_name_is_allowed() -> None:
    # Its value reaches the selector through ``view.kwargs``.
    binding = _register(_tasks_by_status, paginate=True, url_kwargs=(UrlKwarg("status"),))
    assert binding.name == "read"


def _open_entries(**filters: Any) -> list[Any]:
    return [filters]


def _open_entries_named_limit(**limit: Any) -> list[Any]:
    return [limit]


def test_a_catch_all_selector_is_allowed() -> None:
    # ``**kwargs`` names no parameter the transport could take away.
    binding = _register(_open_entries, paginate=True, query_params=(QueryParam("status"),))
    assert binding.name == "read"


def test_a_catch_alls_own_name_is_not_a_parameter_name() -> None:
    # ``**limit`` receives whatever reaches the pool under its own keys; no
    # argument is ever bound to the name ``limit`` itself.
    binding = _register(_open_entries_named_limit, paginate=True)
    assert binding.name == "read"


# ---------- an ``input_serializer`` field of the same name ----------
#
# The validated values are laid back over the stripped arguments, so a name the
# serializer declares does reach the selector, and neither refusal describes it.
# Each test calls the tool, because what is exempt is decided by what arrives.


class _PageIn(drf_serializers.Serializer):
    page = drf_serializers.IntegerField(required=False)


class _StatusIn(drf_serializers.Serializer):
    status = drf_serializers.CharField()


async def test_a_pagination_named_parameter_the_input_serializer_declares_is_allowed() -> None:
    server = _server()
    server.register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_recent_entries_page),
        input_serializer=_PageIn,
    )
    out = await server.acall_tool("read", {"page": 3}, user=None)
    assert isinstance(out, dict)
    assert out["structuredContent"] == [3]


def _register_status_beside_serializer() -> MCPServer:
    server = _server()
    server.register_selector_tool(
        name="read",
        description="Read.",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_tasks_by_status),
        input_serializer=_StatusIn,
        query_params=(QueryParam("status"),),
    )
    return server


async def test_a_parameter_a_query_param_shadows_is_allowed_when_the_input_serializer_declares_it() -> (
    None
):
    out = await _register_status_beside_serializer().acall_tool(
        "read", {"status": "open"}, user=None
    )
    assert isinstance(out, dict)
    assert out["structuredContent"] == ["open"]


def test_call_tool_leaves_a_query_params_value_to_request_query_params() -> None:
    # ``call_tool`` does not run a selector tool's ``input_serializer``, so
    # nothing lays the popped value back and the required parameter is missing.
    result = _register_status_beside_serializer().call_tool("read", {"status": "open"}, user=None)
    assert result.is_error
    assert json.loads(result.content[0].text)["error"]["detail"] == {
        "status": ["This field is required."]
    }


class _TitleIn(drf_serializers.Serializer):
    title = drf_serializers.CharField(required=False)


def _titled_page(*, title: str = "", page: int = 1) -> list[Any]:
    return [title, page]


def _titled_status(*, title: str = "", status: str = "open") -> list[Any]:
    return [title, status]


@pytest.mark.parametrize(
    ("selector", "query_params", "wording", "other_wording"),
    [
        pytest.param(_titled_page, (), _PAGINATION_WORDING, _QUERY_PARAM_WORDING, id="page"),
        pytest.param(
            _titled_status,
            (QueryParam("status"),),
            _QUERY_PARAM_WORDING,
            _PAGINATION_WORDING,
            id="query-param",
        ),
    ],
)
def test_a_serializer_declaring_another_name_exempts_nothing(
    selector: Any, query_params: tuple[QueryParam, ...], wording: str, other_wording: str
) -> None:
    # The serializer declares ``title``, which the selector takes too so the
    # field check passes and this refusal is the one that answers.
    with pytest.raises(ImproperlyConfigured) as caught:
        _register(selector, input_serializer=_TitleIn, query_params=query_params)
    message = str(caught.value)
    assert wording in message
    assert other_wording not in message


@dataclasses.dataclass
class _PageFields:
    page: int = 1


class _PageDataclassIn(DataclassSerializer):
    class Meta:
        dataclass = _PageFields


class _ReadOnlyPageIn(drf_serializers.Serializer):
    page = drf_serializers.IntegerField(read_only=True)


class _PageElsewhereIn(drf_serializers.Serializer):
    page = drf_serializers.IntegerField(source="number", required=False)


@pytest.mark.parametrize(
    "input_serializer",
    [
        # Validates into a dataclass instance, which is not laid over the arguments.
        pytest.param(_PageFields, id="bare-dataclass"),
        pytest.param(_PageDataclassIn, id="dataclass-serializer"),
        # DRF keeps a read-only field out of the validated values altogether.
        pytest.param(_ReadOnlyPageIn, id="read-only-field"),
        # The value lands under ``number``, which the selector does not take.
        pytest.param(_PageElsewhereIn, id="source-elsewhere"),
    ],
)
def test_a_field_whose_value_is_not_laid_back_exempts_nothing(input_serializer: type) -> None:
    with pytest.raises(ImproperlyConfigured, match=r"\['page'\]") as caught:
        _register(_recent_entries_page, input_serializer=input_serializer)
    assert _PAGINATION_WORDING in str(caught.value)
