"""Coverage: ``UnknownArguments`` policy on validation + ``tools/list``.

The three policies live on the binding and influence two layers:

- runtime validation (``handlers/utils.validate_input_against_serializer``)
- ``tools/list`` schema (``additionalProperties`` toggle)

These tests pin both behaviours from a real ``MCPServer`` so the wire
shape is what end users see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from django.http import HttpRequest
from django_service_specs import SelectorKind
from rest_framework import serializers as drf_serializers
from rest_framework_services import ArgumentBinding
from rest_framework_services.dispatch.utils import refuse_retired_modes
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import validate_input_against_serializer
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.utils import tool_error


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    return MCPCallContext(
        http_request=HttpRequest(),
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )


class _OneFieldInput(drf_serializers.Serializer):
    """Serializer that declares a single ``known`` field."""

    known = drf_serializers.CharField()


def _svc(*, data: dict[str, Any]) -> dict[str, Any]:
    return dict(data)


# ---------- inputSchema additionalProperties ----------


def test_reject_schema_carries_additional_properties_false() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    assert schema["additionalProperties"] is False


def test_ignore_schema_carries_additional_properties_true() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.IGNORE,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is True


def _svc_no_input(*, user: Any = None) -> dict[str, Any]:
    return {"ok": True}


def test_serializerless_service_schema_is_closed_under_reject() -> None:
    # A service with no input_serializer keeps REJECT at dispatch, and bundled
    # with no target lookup it declares no argument at all, so its schema is
    # closed and lists nothing.
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc_no_input, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    assert schema["additionalProperties"] is False
    assert not schema.get("properties")


@pytest.mark.django_db
def test_serializerless_service_refuses_unknown_key_under_reject() -> None:
    # The runtime side of the same contract: an unknown key is refused for a
    # serializer-less service, matching the closed schema above.
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc_no_input, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call({"name": "t", "arguments": {"rogue": "v"}}, _ctx(server))
    assert tool_error(out)["detail"] == {"non_field_errors": ["Unexpected argument(s): 'rogue'."]}


# ---------- Runtime behaviour ----------


def test_reject_rejects_unknown_key_with_a_validation_error_result() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"known": "ok", "unknown": "x"}}, _ctx(server)
    )
    error = tool_error(out)
    assert error["type"] == "validation_error"
    detail = error["detail"]
    assert "non_field_errors" in detail
    assert "unknown" in detail["non_field_errors"][0]


def test_reject_accepts_when_only_known_keys_present() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call({"name": "t", "arguments": {"known": "ok"}}, _ctx(server))
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"known": "ok"}


def test_ignore_drops_unknown_keys() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.IGNORE,
    )
    out = handle_tools_call({"name": "t", "arguments": {"known": "ok", "extra": 99}}, _ctx(server))
    assert isinstance(out, dict)
    # ``extra`` was silently dropped; only the validated ``known`` remains.
    assert out["structuredContent"] == {"known": "ok"}


def test_reject_does_not_flag_pool_seed_keys() -> None:
    """Pool-seed keys (``user`` / ``request`` / ``data``) are silently dropped, not flagged."""
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"known": "ok", "user": "evil"}}, _ctx(server)
    )
    # ``user`` is reserved — not flagged as unknown.
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"known": "ok"}


# ---------- Selector tool: filter / ordering / pagination keys are "known" ----------


def _list(**_kwargs: Any) -> list[dict[str, str]]:
    # ``**kwargs`` absorbs whatever flows through the spread so the
    # registration-time field/param check is happy regardless of which
    # input_serializer this fixture is paired with.
    return [{"id": "1"}]


def test_selector_tool_reject_treats_post_fetch_keys_as_known() -> None:
    """``ordering`` / ``page`` / ``limit`` aren't rejected even under REJECT.

    Pagination itself is exercised in
    :mod:`tests.handlers.test_selector_tool_dispatch`; here we only verify
    that the validator doesn't flag pipeline-knob keys as unknown. The
    selector returns a list (not a queryset) so the post-fetch
    paginator no-ops and we sidestep the queryset-vs-list ``count()``
    quirk.
    """
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call(
        {"name": "x", "arguments": {"ordering": "id", "page": 1, "limit": 10}},
        _ctx(server),
    )
    assert not isinstance(out, JsonRpcError)


def test_selector_tool_reject_rejects_truly_unknown_keys() -> None:
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        paginate=True,
        unknown_arguments=UnknownArguments.REJECT,
        input_serializer=_OneFieldInput,
    )
    out = handle_tools_call({"name": "x", "arguments": {"known": "ok", "rogue": "v"}}, _ctx(server))
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert "rogue" in error["detail"]["non_field_errors"][0]


def _selector_tool_under_reject(seen: dict[str, Any]) -> MCPServer:
    """A selector tool under ``REJECT`` whose selector records what reaches it.

    It takes a bare ``**kwargs`` rather than naming the keys these tests send: a
    name the selector declares is known for that reason alone, so declaring them
    would let each test pass without the seed set it is about.
    """

    def _recording(*, known: str, **kwargs: Any) -> list[dict[str, str]]:
        seen.update(kwargs)
        return [{"id": "1"}]

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_recording),
        unknown_arguments=UnknownArguments.REJECT,
        input_serializer=_OneFieldInput,
    )
    return server


@pytest.mark.parametrize("name", ["result", "queryset"])
def test_a_kernel_seed_is_not_refused_as_unknown(name: str) -> None:
    """Every reserved seed is known, ``result`` and ``queryset`` included: dispatch
    drops a caller's key of a seed's name rather than refusing it, so admitting it
    here delivers nothing."""
    seen: dict[str, Any] = {}
    out = handle_tools_call(
        {"name": "x", "arguments": {"known": "ok", name: "v"}},
        _ctx(_selector_tool_under_reject(seen)),
    )
    assert isinstance(out, dict)
    assert "isError" not in out, out
    assert seen.get(name) != "v"


@pytest.mark.parametrize("name", ["request", "serializer"])
def test_a_name_drf_services_drops_is_not_refused_as_unknown(name: str) -> None:
    """A selector tool's input serializer admits what the service path drops.

    Neither name is a kernel seed any more, but drf-services drops a caller's key
    of either one on every path, so refusing it here alone would answer the same
    call two ways depending on which kind of tool received it. Admitting it opens
    nothing: the caller's value never reaches the selector.
    """
    seen: dict[str, Any] = {}
    out = handle_tools_call(
        {"name": "x", "arguments": {"known": "ok", name: "v"}},
        _ctx(_selector_tool_under_reject(seen)),
    )
    assert isinstance(out, dict)
    assert "isError" not in out, out
    assert seen.get(name) != "v"


# ---------- A service tool whose declared key set is not enumerable ----------


def _widget_by_pk(user: Any, pk: Any, **kwargs: Any) -> dict[str, Any]:
    """A nested instance selector taking a bare ``**kwargs`` — an open surface.

    The sister package cannot enumerate what such a callable declares, so its
    unknown-argument check has nothing to compare against and lets every
    undeclared key through, silently.
    """
    return {"pk": pk}


def test_service_schema_stays_open_when_the_declared_key_set_is_not_enumerable() -> None:
    # REJECT plus a serializer is not on its own enough for a *service* tool:
    # the enforcement runs in the sister package against the key set the spec
    # declares, and a nested selector with a bare ``**kwargs`` leaves that set
    # open. Advertising ``additionalProperties: false`` there tells the client
    # a typo'd field will be refused while the server accepts and drops it.
    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(
            service=_svc,
            input_serializer=_OneFieldInput,
            atomic=False,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widget_by_pk),
        ),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is True


def test_the_open_service_schema_matches_what_dispatch_actually_does() -> None:
    """The advertisement and the runtime must agree — which way round is secondary."""
    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(
            service=_svc,
            input_serializer=_OneFieldInput,
            atomic=False,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widget_by_pk),
        ),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call(
        {"name": "x", "arguments": {"known": "k", "pk": 1, "typo": "?"}}, _ctx(server)
    )
    # Accepted and dropped, not refused: exactly the behaviour the open schema
    # now advertises.
    assert isinstance(out, dict)
    assert "isError" not in out


def test_service_schema_closes_when_the_nested_selector_declares_its_keys() -> None:
    def _widget(user: Any, pk: Any) -> dict[str, Any]:
        return {"pk": pk}

    server = _server()
    server.register_service_tool(
        name="x",
        spec=ServiceSpec(
            service=_svc,
            input_serializer=_OneFieldInput,
            atomic=False,
            instance_selector_spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_widget),
        ),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is False


# ---------- Selector schema reflects the policy on the merged inputSchema ----------


def test_selector_tool_schema_additional_properties_false_under_reject() -> None:
    # A closed schema is honest only with a serializer to validate against —
    # the runtime then really rejects unknown keys under REJECT.
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        input_serializer=_OneFieldInput,
        paginate=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is False


def test_selector_tool_schema_stays_open_under_a_permissive_policy() -> None:
    # The same serializer as the closed case above: it is the policy, not the
    # serializer, that leaves an undeclared key served here, so the schema
    # stays open.
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        input_serializer=_OneFieldInput,
        paginate=True,
        unknown_arguments=UnknownArguments.IGNORE,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is True


def test_selector_tool_schema_additional_properties_true_when_serializerless_reject() -> None:
    # A serializer-less binding can't reject unknown keys (the read-path
    # validator short-circuits when there is no serializer), so even under REJECT
    # its schema must stay open rather than falsely advertise a closed contract.
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        paginate=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is True


def test_a_bare_var_keyword_selector_stays_open_and_drops_an_undeclared_key_under_reject() -> None:
    # The open schema is the truth about refusal, not about delivery: nothing
    # refuses the undeclared key, and drf-services drops it before the
    # selector runs, so its bare ``**kwargs`` receives the seeds and nothing the
    # caller named. 0.52.1 handed it ``colour``.
    seen: list[set[str]] = []

    def rows(**kwargs: Any) -> list[Any]:
        seen.append(set(kwargs))
        return []

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=rows),
        unknown_arguments=UnknownArguments.REJECT,
    )
    listed = handle_tools_list(None, _ctx(server))
    assert isinstance(listed, dict)

    result = server.call_tool("x", {"colour": "red"}, user=None).to_dict()

    assert listed["tools"][0]["inputSchema"]["additionalProperties"] is True
    assert result.get("isError") is not True, result
    assert len(seen) == 1
    assert "colour" not in seen[0]


# ---------- Validator-level unit edge cases ----------


def test_selector_dataclass_input_serializer_is_wrapped() -> None:
    """A selector's ``input_serializer`` may be a bare dataclass (auto-wrapped).

    Exercises the read-path validator's dataclass wrap: the undeclared ``extra``
    is dropped under ``IGNORE`` and the call is served.
    """

    @dataclass
    class _DC:
        known: str

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        input_serializer=_DC,
        unknown_arguments=UnknownArguments.IGNORE,
    )
    out = handle_tools_call({"name": "x", "arguments": {"known": "ok", "extra": 1}}, _ctx(server))
    assert not isinstance(out, JsonRpcError)
    assert isinstance(out, dict)
    assert out["structuredContent"] == [{"id": "1"}]


@pytest.mark.parametrize("policy", [UnknownArguments.REJECT, UnknownArguments.IGNORE])
def test_no_input_serializer_short_circuits_policy(policy: UnknownArguments) -> None:
    """With no ``input_serializer``, the validator returns None and policy is a no-op."""
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=lambda: {}, atomic=False),
        unknown_arguments=policy,
    )
    out = handle_tools_call({"name": "t", "arguments": {"anything": 1}}, _ctx(server))
    assert isinstance(out, dict)


@pytest.mark.parametrize("serializer", [_OneFieldInput, None], ids=["serializer", "none"])
def test_a_chain_under_passthrough_raises_as_drf_services_dispatch_does(
    serializer: type | None,
) -> None:
    """A chain validates its arguments here and never calls ``dispatch_spec``, so
    this validator is the one place a retired ``PASSTHROUGH`` can be refused on
    that path. Without the refusal it would run as ``IGNORE`` without a word."""
    with pytest.raises(ValueError) as caught:
        validate_input_against_serializer(
            {"known": "ok", "extra": 1},
            serializer,
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )
    with pytest.raises(ValueError) as upstream:
        refuse_retired_modes(ArgumentBinding.AUTO, UnknownArguments.PASSTHROUGH)
    # drf-services' own words, so the message cannot drift from dispatch's.
    assert str(caught.value) == str(upstream.value)
