"""MCP server factory for the invoicing example.

All registrations live in one place so the wire surface is easy to
read in a single pass. Real projects can split this across multiple
modules (one per app) and combine them into a single ``MCPServer``.
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from invoices.filters import InvoiceFilterSet
from invoices.models import Invoice
from invoices.selectors import get_invoice, list_invoices
from invoices.serializers import (
    InvoiceInputSerializer,
    InvoiceOutputSerializer,
    MarkSentInputSerializer,
)
from invoices.services import create_invoice, mark_invoice_sent
from rest_framework_mcp import MCPServer, PromptArgument, PromptMessage
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.permissions.drf_permission_adapter import DRFPermissionAdapter
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore


def build_server() -> MCPServer:
    """Construct and populate the example MCP server."""
    server = MCPServer(
        name="invoicing-example",
        version="0.0.1",
        description="Demo invoicing MCP surface",
        # Dev-only: accepts any caller. Swap for the default
        # DjangoOAuthToolkitBackend (or your own) in production.
        auth_backend=AllowAnyBackend(),
        # Fine for single-process dev. The default DjangoCacheSessionStore
        # works across workers.
        session_store=InMemorySessionStore(),
    )

    # Permissions are **required** since 0.25.0: registering a tool without
    # them raises. DRF viewset-level and REST_FRAMEWORK defaults do not reach
    # MCP, so an omission here is an open tool rather than an inherited policy.
    # ``AllowAny`` is the honest choice for a demo — it says "deliberately
    # open" out loud, which is the whole point of the strict default. Swap it
    # for ``IsAuthenticated`` (or your own) in anything real.

    # ----- Service tools (mutations) -----

    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            permission_classes=[AllowAny],
            service=create_invoice,
            input_serializer=InvoiceInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
        description="Create a new invoice with a unique number and a positive amount.",
    )

    server.register_service_tool(
        name="invoices.mark_sent",
        spec=ServiceSpec(
            permission_classes=[AllowAny],
            service=mark_invoice_sent,
            input_serializer=MarkSentInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
        description="Flip an invoice's ``sent`` flag.",
    )

    # ----- Selector tool (read with filter / order / paginate) -----

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            permission_classes=[AllowAny],
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=InvoiceOutputSerializer,
            filter_set=InvoiceFilterSet,
        ),
        description="List invoices, optionally filtered / ordered / paginated.",
        ordering_fields=["created_at", "amount_cents"],
        paginate=True,
    )

    # ----- Resource (single invoice by PK via URI template) -----

    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_invoice,
            output_serializer=InvoiceOutputSerializer,
            # A resource is as reachable as a tool, so it declares its
            # permissions the same way. Swap AllowAny for the real gate.
            permission_classes=[AllowAny],
        ),
        description="A single invoice by primary key.",
    )

    # ----- Prompt (renders an email body for an invoice) -----

    def compose_invoice_email(*, pk: str) -> list[PromptMessage]:
        """Render an email body for a single invoice — illustrates a
        prompt that pulls live data from the database."""
        invoice = Invoice.objects.get(pk=int(pk))
        body: str = (
            f"Hello,\n\n"
            f"Invoice {invoice.number} for ${invoice.amount_cents / 100:.2f} "
            f"is now ready. Please remit at your convenience.\n\n"
            f"— Accounting"
        )
        return [PromptMessage.text(role="user", text=body)]

    server.register_prompt(
        name="compose_invoice_email",
        render=compose_invoice_email,
        description="Render a customer email body for an invoice.",
        arguments=[
            PromptArgument(name="pk", description="Invoice primary key", required=True),
        ],
        # A prompt reads the database here, so it is gated like everything
        # else on this server. Swap AllowAny for the real gate.
        permissions=[DRFPermissionAdapter(AllowAny)],
    )

    return server
