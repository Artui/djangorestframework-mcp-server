"""Phase 10b coverage: ``UnknownArguments`` policy on validation + ``tools/list``.

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
from rest_framework import serializers as drf_serializers
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import UnknownArguments
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


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


def test_passthrough_schema_carries_additional_properties_true() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    schema = out["tools"][0]["inputSchema"]
    assert schema["additionalProperties"] is True


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


# ---------- Runtime behaviour ----------


def test_reject_rejects_unknown_key_with_minus_32602() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_call(
        {"name": "t", "arguments": {"known": "ok", "unknown": "x"}}, _ctx(server)
    )
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602
    detail = out.data["detail"]
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


def test_passthrough_merges_unknown_keys_into_validated() -> None:
    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_OneFieldInput, atomic=False),
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_call({"name": "t", "arguments": {"known": "ok", "extra": 99}}, _ctx(server))
    assert isinstance(out, dict)
    # ``extra`` survived validation and reached the service via ``data=``.
    assert out["structuredContent"] == {"known": "ok", "extra": 99}


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
    assert isinstance(out, JsonRpcError)
    assert out.code == -32602


# ---------- Selector schema reflects the policy on the merged inputSchema ----------


def test_selector_tool_schema_additional_properties_false_under_reject() -> None:
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        paginate=True,
        unknown_arguments=UnknownArguments.REJECT,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is False


def test_selector_tool_schema_additional_properties_true_under_passthrough() -> None:
    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        paginate=True,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_list(None, _ctx(server))
    assert isinstance(out, dict)
    assert out["tools"][0]["inputSchema"]["additionalProperties"] is True


# ---------- Validator-level unit edge cases ----------


def test_passthrough_does_not_override_validated_field() -> None:
    """If raw and validated both define ``known``, the validated value wins."""

    class _Echo(drf_serializers.Serializer):
        known = drf_serializers.CharField()

        def validate_known(self, value: str) -> str:
            return value.upper()

    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=_svc, input_serializer=_Echo, atomic=False),
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_call({"name": "t", "arguments": {"known": "ok", "extra": 1}}, _ctx(server))
    assert isinstance(out, dict)
    # ``known`` reflects the serializer-coerced uppercase, not the raw value.
    assert out["structuredContent"]["known"] == "OK"
    assert out["structuredContent"]["extra"] == 1


def test_selector_dataclass_input_serializer_passthrough() -> None:
    """A selector's ``input_serializer`` may be a bare dataclass (auto-wrapped).

    ``validated`` is then a dataclass instance, not a dict, so ``PASSTHROUGH``
    has no dict to merge unknown keys onto — the extra reaches the selector via
    the spread instead. Exercises the dataclass-wrap + non-dict passthrough
    branches of the read-path validator.
    """

    @dataclass
    class _DC:
        known: str

    server = _server()
    server.register_selector_tool(
        name="x",
        spec=SelectorSpec(kind=SelectorKind.LIST, selector=_list),
        input_serializer=_DC,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_call({"name": "x", "arguments": {"known": "ok", "extra": 1}}, _ctx(server))
    assert not isinstance(out, JsonRpcError)
    assert isinstance(out, dict)
    assert out["structuredContent"] == [{"id": "1"}]


@pytest.mark.parametrize("policy", [UnknownArguments.REJECT, UnknownArguments.PASSTHROUGH])
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


def test_passthrough_no_op_when_validated_is_dataclass_instance() -> None:
    """``DataclassSerializer`` returns a dataclass instance — unknown keys can't merge."""
    from dataclasses import dataclass

    @dataclass
    class _Input:
        known: str

    captured: dict[str, Any] = {}

    def svc(*, data: _Input) -> dict[str, Any]:
        captured["data"] = data
        # ``extra`` never reaches the callable when validated is a dataclass —
        # there's no merge target.
        return {"known": data.known}

    server = _server()
    server.register_service_tool(
        name="t",
        spec=ServiceSpec(service=svc, input_serializer=_Input, atomic=False),
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )
    out = handle_tools_call({"name": "t", "arguments": {"known": "k", "extra": 99}}, _ctx(server))
    assert isinstance(out, dict)
    assert isinstance(captured["data"], _Input)
    assert captured["data"].known == "k"
    assert not hasattr(captured["data"], "extra")
