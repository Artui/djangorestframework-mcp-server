"""Object-level permissions and preconditions on the paths that skip ``dispatch_spec``.

Two of this package's dispatch paths call ``run_selector`` / ``run_service``
directly rather than through ``dispatch_spec``: ``resources/read``, whose
binding holds a bare callable and not the spec, and chain steps, which own their
own transaction and pool. On both, a spec's ``permission_classes`` used to be
evaluated for their class-level half only — ``has_object_permission`` never ran
anywhere in the package — and a chain step's ``preconditions`` never ran at all.
These tests hold the resolved-target guard and the precondition hook on both.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework.permissions import BasePermission
from rest_framework_services.exceptions.service_validation_error import ServiceValidationError
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.constants import JsonRpcErrorCode
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_resources_read_async import handle_resources_read_async
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice
from tests.testapp.serializers import InvoiceOutputSerializer
from tests.utils import tool_error


class IsAliceInvoice(BasePermission):
    """Class-level check passes for everyone; ownership lives per row.

    The exact shape the guard exists for: a spec whose authorization is
    expressed on the object, which over HTTP runs in
    ``check_object_permissions`` and off HTTP in ``enforce_permissions``.
    """

    message = "Not your invoice"

    def has_permission(self, request: Any, view: Any) -> bool:
        return True

    def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
        return str(obj.number).startswith("alice")


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


def _get_invoice(*, pk: str) -> Invoice:
    return Invoice.objects.get(pk=int(pk))


def _register_invoice_resource(server: MCPServer) -> None:
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_invoice,
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[IsAliceInvoice],
        ),
    )


# ---------- resources/read ----------


@pytest.mark.django_db
def test_resource_read_refuses_a_row_the_object_permission_denies() -> None:
    """The failure this guard exists for: the class-level check passes, the row
    belongs to someone else, and the body used to be serialised out anyway."""
    theirs = Invoice.objects.create(number="bob-1", amount_cents=100)
    server = _server()
    _register_invoice_resource(server)

    out = handle_resources_read({"uri": f"invoices://{theirs.pk}"}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db
def test_resource_read_returns_a_row_the_object_permission_allows() -> None:
    mine = Invoice.objects.create(number="alice-1", amount_cents=100)
    server = _server()
    _register_invoice_resource(server)

    out = handle_resources_read({"uri": f"invoices://{mine.pk}"}, _ctx(server))

    assert isinstance(out, dict)
    assert '"number": "alice-1"' in out["contents"][0]["text"]


@pytest.mark.django_db(transaction=True)
async def test_async_resource_read_refuses_the_same_row() -> None:
    from asgiref.sync import sync_to_async

    theirs = await sync_to_async(Invoice.objects.create)(number="bob-2", amount_cents=1)
    server = _server()
    _register_invoice_resource(server)

    out = await handle_resources_read_async({"uri": f"invoices://{theirs.pk}"}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN


@pytest.mark.django_db
def test_resource_read_with_no_drf_permissions_skips_the_guard() -> None:
    """A resource guarded only by a per-binding MCP permission has no
    object-level half to run, and must not be refused for the lack of one."""

    class _AlwaysAllow:
        def has_permission(self, *_args: object, **_kwargs: object) -> bool:
            return True

    mine = Invoice.objects.create(number="bob-3", amount_cents=1)
    server = _server()
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=_get_invoice,
            output_serializer=InvoiceOutputSerializer,
        ),
        permissions=[_AlwaysAllow()],
    )

    out = handle_resources_read({"uri": f"invoices://{mine.pk}"}, _ctx(server))

    assert isinstance(out, dict)


@pytest.mark.django_db
def test_resource_read_of_a_list_runs_only_the_class_level_check() -> None:
    """A ``LIST`` resource resolves a set, which is authorized per-set: the
    guard must not call ``has_object_permission`` with a queryset."""

    class _ExplodesOnObjects(BasePermission):
        def has_permission(self, request: Any, view: Any) -> bool:
            return True

        def has_object_permission(self, request: Any, view: Any, obj: Any) -> bool:
            raise AssertionError("object permissions must not run against a queryset")

    Invoice.objects.create(number="bob-4", amount_cents=1)
    server = _server()
    server.register_resource(
        name="invoices",
        uri_template="invoices://all",
        selector=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=lambda: Invoice.objects.all(),
            output_serializer=InvoiceOutputSerializer,
            permission_classes=[_ExplodesOnObjects],
        ),
    )

    out = handle_resources_read({"uri": "invoices://all"}, _ctx(server))

    assert isinstance(out, dict)


# ---------- chain steps ----------


def _mark_sent(*, instance: Invoice) -> Invoice:
    instance.sent = True
    instance.save(update_fields=["sent"])
    return instance


@pytest.mark.django_db
def test_chain_selector_step_object_permission_blocks_the_write_behind_it() -> None:
    """The audit's scenario: a RETRIEVE step feeding a mutating step. The
    class-level check passes at binding time, so only the resolved-row guard
    can stop the chain reaching another tenant's invoice."""
    theirs = Invoice.objects.create(number="bob-5", amount_cents=100)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="target",
                spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_get_invoice,
                    permission_classes=[IsAliceInvoice],
                ),
                inputs=lambda ctx: {"pk": ctx.args["pk"]},
            ),
            ChainStep(
                alias="sent",
                spec=ServiceSpec(
                    service=_mark_sent,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
                    ),
                ),
                inputs=lambda ctx: {"instance": ctx["target"]},
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {"pk": str(theirs.pk)}}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    theirs.refresh_from_db()
    assert theirs.sent is False


@pytest.mark.django_db
def test_chain_service_step_object_permission_guards_the_resolved_instance() -> None:
    """A write step's target is whatever ``inputs`` resolved as ``instance``,
    which is the row an object-level rule judges — as it would on the HTTP path."""
    theirs = Invoice.objects.create(number="bob-6", amount_cents=100)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="target",
                spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_invoice),
                inputs=lambda ctx: {"pk": ctx.args["pk"]},
            ),
            ChainStep(
                alias="sent",
                spec=ServiceSpec(
                    service=_mark_sent,
                    atomic=False,
                    permission_classes=[IsAliceInvoice],
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
                    ),
                ),
                inputs=lambda ctx: {"instance": ctx["target"]},
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {"pk": str(theirs.pk)}}, _ctx(server))

    assert isinstance(out, JsonRpcError)
    assert out.code == JsonRpcErrorCode.FORBIDDEN
    theirs.refresh_from_db()
    assert theirs.sent is False


@pytest.mark.django_db
def test_chain_step_allows_a_row_the_object_permission_accepts() -> None:
    mine = Invoice.objects.create(number="alice-2", amount_cents=100)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="target",
                spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_get_invoice,
                    permission_classes=[IsAliceInvoice],
                ),
                inputs=lambda ctx: {"pk": ctx.args["pk"]},
            ),
            ChainStep(
                alias="sent",
                spec=ServiceSpec(
                    service=_mark_sent,
                    atomic=False,
                    output_selector_spec=SelectorSpec(
                        kind=SelectorKind.RETRIEVE, output_serializer=InvoiceOutputSerializer
                    ),
                ),
                inputs=lambda ctx: {"instance": ctx["target"]},
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {"pk": str(mine.pk)}}, _ctx(server))

    assert isinstance(out, dict)
    mine.refresh_from_db()
    assert mine.sent is True


@pytest.mark.django_db
def test_chain_selector_step_runs_its_preconditions_against_the_resolved_row() -> None:
    """A precondition is a state rule over the row a step resolved. It ran on
    every other transport and silently did not run inside a chain."""

    def reject_locked(*, instance: Invoice) -> None:
        if instance.sent:
            raise ServiceValidationError({"invoice": ["already sent"]})

    locked = Invoice.objects.create(number="alice-3", amount_cents=100, sent=True)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="target",
                spec=SelectorSpec(
                    kind=SelectorKind.RETRIEVE,
                    selector=_get_invoice,
                    preconditions=[reject_locked],
                ),
                inputs=lambda ctx: {"pk": ctx.args["pk"]},
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {"pk": str(locked.pk)}}, _ctx(server))

    assert tool_error(out)["failedStep"] == "target"


@pytest.mark.django_db
def test_chain_selector_step_precondition_sees_a_list_as_a_collection() -> None:
    """``LIST`` resolves a set, so the precondition pool names it ``collection``
    — the key ``dispatch_spec`` uses, so one rule works on either transport."""
    seen: dict[str, Any] = {}

    def record(*, collection: Any) -> None:
        seen["count"] = collection.count()

    Invoice.objects.create(number="alice-4", amount_cents=1)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="all",
                spec=SelectorSpec(
                    kind=SelectorKind.LIST,
                    selector=lambda: Invoice.objects.all(),
                    output_serializer=InvoiceOutputSerializer,
                    preconditions=[record],
                ),
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {}}, _ctx(server))

    assert isinstance(out, dict)
    assert seen["count"] == 1


@pytest.mark.django_db
def test_chain_service_step_runs_its_preconditions_before_the_service() -> None:
    """Ordering matters: the rule aborts the write rather than reporting after."""
    ran: list[str] = []

    def reject(*, instance: Invoice) -> None:
        ran.append("precondition")
        raise ServiceValidationError({"invoice": ["locked"]})

    def service(*, instance: Invoice) -> Invoice:  # pragma: no cover - must not run
        ran.append("service")
        return instance

    invoice = Invoice.objects.create(number="alice-5", amount_cents=100)
    server = _server()
    server.register_chain_tool(
        name="chain",
        steps=[
            ChainStep(
                alias="target",
                spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=_get_invoice),
                inputs=lambda ctx: {"pk": ctx.args["pk"]},
            ),
            ChainStep(
                alias="write",
                spec=ServiceSpec(service=service, atomic=False, preconditions=[reject]),
                inputs=lambda ctx: {"instance": ctx["target"]},
            ),
        ],
    )

    out = handle_tools_call({"name": "chain", "arguments": {"pk": str(invoice.pk)}}, _ctx(server))

    assert tool_error(out)["failedStep"] == "write"
    assert ran == ["precondition"]
