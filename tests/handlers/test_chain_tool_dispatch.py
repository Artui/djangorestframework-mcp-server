"""End-to-end coverage for chain tools (``register_chain_tool``).

Exercises ``handle_tools_call`` for ``ChainToolBinding`` — multi-step
sequencing, cross-step input wiring (``ctx[alias]`` / ``ctx.args``), atomic
rollback, error mapping with ``failedStep``, the output modes, and the
resolved-data output context per step.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework.permissions import BasePermission
from rest_framework_services.exceptions.service_error import ServiceError
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
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


def _create(*, number: str, amount_cents: int) -> Invoice:
    return Invoice.objects.create(number=number, amount_cents=amount_cents)


def _mark_sent(*, pk: int) -> Invoice:
    inv = Invoice.objects.get(pk=pk)
    inv.sent = True
    inv.save(update_fields=["sent"])
    return inv


def _out_spec() -> SelectorSpec:
    return SelectorSpec(kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer)


def _call(server: MCPServer, args: dict[str, Any]) -> Any:
    return handle_tools_call({"name": "chain", "arguments": args}, _ctx(server))


# ---------- happy paths ----------


@pytest.mark.django_db
def test_retrieve_then_write_then_write_reads_prior_outputs() -> None:
    src = Invoice.objects.create(number="A", amount_cents=100)

    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep("src", SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: src)),
            ChainStep(
                "marked",
                ServiceSpec(service=_mark_sent, atomic=False),
                inputs=lambda ctx: {"pk": ctx["src"].pk},
            ),
            ChainStep(
                "copy",
                ServiceSpec(service=_create, atomic=False, output_selector_spec=_out_spec()),
                # derives from BOTH src (amount) and marked (sent flag)
                inputs=lambda ctx: {
                    "number": f"{ctx['src'].number}-copy-{ctx['marked'].sent}",
                    "amount_cents": ctx["src"].amount_cents,
                },
            ),
        ],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "A-copy-True"
    assert out["structuredContent"]["amount_cents"] == 100
    assert Invoice.objects.get(number="A").sent is True
    assert Invoice.objects.count() == 2


@pytest.mark.django_db
def test_first_step_passthrough_data_is_validated_args() -> None:
    """A first service step with no ``inputs`` receives ``data=ctx.args``."""
    seen: dict[str, Any] = {}

    def _svc(*, data: Any) -> Invoice:
        seen["data"] = data
        return Invoice.objects.create(number=data["number"], amount_cents=data["amount_cents"])

    server = _server()
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[
            ChainStep(
                "made", ServiceSpec(service=_svc, atomic=False, output_selector_spec=_out_spec())
            )
        ],
    )
    out = _call(server, {"number": "Z", "amount_cents": 5})
    assert isinstance(out, dict)
    assert seen["data"] == {"number": "Z", "amount_cents": 5}
    assert out["structuredContent"]["number"] == "Z"


@pytest.mark.django_db
def test_output_alias_selects_an_earlier_step() -> None:
    src = Invoice.objects.create(number="A", amount_cents=7)
    server = _server()
    server.register_chain_tool(
        name="chain",
        output_alias="src",
        steps=[
            ChainStep(
                "src",
                SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda: src,
                    output_serializer=InvoiceOutputSerializer,
                ),
            ),
            ChainStep(
                "noise",
                ServiceSpec(service=_create, atomic=False),
                inputs=lambda ctx: {"number": "B", "amount_cents": 0},
            ),
        ],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "A"  # rendered the src step


@pytest.mark.django_db
def test_output_all_renders_every_serializer_bearing_step() -> None:
    src = Invoice.objects.create(number="A", amount_cents=7)
    server = _server()
    server.register_chain_tool(
        name="chain",
        output_all=True,
        steps=[
            ChainStep(
                "src",
                SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=lambda: src,
                    output_serializer=InvoiceOutputSerializer,
                ),
            ),
            # No serializer → excluded from output_all.
            ChainStep(
                "side",
                ServiceSpec(service=_mark_sent, atomic=False),
                inputs=lambda ctx: {"pk": ctx["src"].pk},
            ),
            ChainStep(
                "copy",
                ServiceSpec(service=_create, atomic=False, output_selector_spec=_out_spec()),
                inputs=lambda ctx: {"number": "B", "amount_cents": 1},
            ),
        ],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert set(out["structuredContent"].keys()) == {"src", "copy"}
    assert out["structuredContent"]["src"]["number"] == "A"
    assert out["structuredContent"]["copy"]["number"] == "B"


@pytest.mark.django_db
def test_selector_list_step_rendered_many_true_with_page_extra() -> None:
    for i in range(3):
        Invoice.objects.create(number=f"INV-{i}", amount_cents=i)
    seen: dict[str, Any] = {}

    def _provider(view: Any, request: Any, *, page: Any) -> dict[str, Any]:  # noqa: ARG001
        seen["n"] = len(list(page))
        return {}

    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "all",
                SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: Invoice.objects.all().order_by("amount_cents"),
                    output_serializer=InvoiceOutputSerializer,
                    output_serializer_context=_provider,
                ),
            ),
        ],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert seen["n"] == 3
    assert [row["number"] for row in out["structuredContent"]] == ["INV-0", "INV-1", "INV-2"]


@pytest.mark.django_db
def test_service_step_output_context_receives_result_extra() -> None:
    seen: dict[str, Any] = {}

    class _Out(drf_serializers.ModelSerializer):
        class Meta:
            model = Invoice
            fields = ["id", "number"]

    def _provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:  # noqa: ARG001
        seen["pk"] = result.pk
        return {}

    server = _server()
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[
            ChainStep(
                "made",
                ServiceSpec(
                    service=_create,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        output_serializer=_Out,
                        output_serializer_context=_provider,
                    ),
                ),
                inputs=lambda ctx: ctx.args,
            )
        ],
    )
    out = _call(server, {"number": "Q", "amount_cents": 3})
    assert isinstance(out, dict)
    assert seen["pk"] == out["structuredContent"]["id"]


@pytest.mark.django_db
def test_service_step_output_selector_refetches() -> None:
    """A service step with an output_selector_spec.selector stores the
    re-fetched value under its alias."""
    server = _server()

    def _refetch(*, result: Invoice) -> Invoice:
        return Invoice.objects.get(pk=result.pk)

    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[
            ChainStep(
                "made",
                ServiceSpec(
                    service=_create,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE,
                        selector=_refetch,
                        output_serializer=InvoiceOutputSerializer,
                    ),
                ),
                inputs=lambda ctx: ctx.args,
            )
        ],
    )
    out = _call(server, {"number": "R", "amount_cents": 9})
    assert isinstance(out, dict)
    assert out["structuredContent"]["number"] == "R"


@pytest.mark.django_db
def test_passthrough_render_without_serializer() -> None:
    """Output step with no serializer renders its raw (dict) result."""
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("raw", ServiceSpec(service=lambda **_: {"ok": 1}, atomic=False))],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert out["structuredContent"] == {"ok": 1}


@pytest.mark.django_db
def test_passthrough_render_none_result_becomes_empty_object() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("raw", ServiceSpec(service=lambda **_: None, atomic=False))],
    )
    out = _call(server, {})
    assert isinstance(out, dict)
    assert out["structuredContent"] == {}


# ---------- atomicity + errors ----------


@pytest.mark.django_db
def test_atomic_rollback_on_service_error() -> None:
    server = _server()

    def _boom(**_: Any) -> None:
        raise ServiceError("kaboom")

    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "first",
                ServiceSpec(service=_create, atomic=False),
                inputs=lambda ctx: {"number": "A", "amount_cents": 1},
            ),
            ChainStep("second", ServiceSpec(service=_boom, atomic=False)),
        ],
    )
    out = _call(server, {})
    error = tool_error(out)
    assert error["type"] == "service_error"
    assert error["failedStep"] == "second"
    # first step's write rolled back
    assert Invoice.objects.count() == 0


@pytest.mark.django_db
def test_non_atomic_chain_does_not_roll_back() -> None:
    server = _server()

    def _boom(**_: Any) -> None:
        raise ServiceError("kaboom")

    server.register_chain_tool(
        name="chain",
        atomic=False,
        steps=[
            ChainStep(
                "first",
                ServiceSpec(service=_create, atomic=False),
                inputs=lambda ctx: {"number": "A", "amount_cents": 1},
            ),
            ChainStep("second", ServiceSpec(service=_boom, atomic=False)),
        ],
    )
    out = _call(server, {})
    assert tool_error(out)["type"] == "service_error"
    # No chain transaction → the first write persists.
    assert Invoice.objects.count() == 1


@pytest.mark.django_db
def test_step_service_validation_error_maps_to_invalid_params() -> None:
    server = _server()

    def _bad(**_: Any) -> None:
        raise ServiceValidationError({"number": ["taken"]})

    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("s", ServiceSpec(service=_bad, atomic=False))],
    )
    out = _call(server, {})
    error = tool_error(out)
    assert error["type"] == "validation_error"
    assert error["failedStep"] == "s"


@pytest.mark.django_db
def test_records_service_error_when_setting_enabled(settings: Any) -> None:
    settings.REST_FRAMEWORK_MCP = {"RECORD_SERVICE_EXCEPTIONS": True}
    server = _server()

    def _boom(**_: Any) -> None:
        raise ServiceError("kaboom")

    server.register_chain_tool(
        name="chain",
        steps=[ChainStep("s", ServiceSpec(service=_boom, atomic=False))],
    )
    out = _call(server, {})
    assert tool_error(out)["type"] == "service_error"


@pytest.mark.django_db
def test_chain_input_validation_error() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        input_serializer=InvoiceInputSerializer,
        steps=[ChainStep("made", ServiceSpec(service=_create, atomic=False))],
    )
    out = _call(server, {"number": "X"})  # missing amount_cents
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.INVALID_PARAMS


# ---------- auth / rate limit ----------


class _Deny(BasePermission):
    def has_permission(self, request: Any, view: Any) -> bool:
        return False


def test_step_permission_class_blocks_whole_chain() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                "s", ServiceSpec(service=lambda **_: {}, atomic=False, permission_classes=[_Deny])
            ),
        ],
    )
    out = handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


class _RateDeny:
    def consume(self, request: HttpRequest, token: TokenInfo) -> int | None:
        return 42


def test_rate_limited() -> None:
    server = _server()
    server.register_chain_tool(
        name="chain",
        rate_limits=[_RateDeny()],
        steps=[ChainStep("s", ServiceSpec(service=lambda **_: {}, atomic=False))],
    )
    out = handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))
    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.RATE_LIMITED
    assert out.data == {"retryAfter": 42}
