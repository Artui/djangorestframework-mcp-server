"""MCP server factory for the invoicing example.

All registrations live in one place so the wire surface is easy to
read in a single pass. Real projects can split this across multiple
modules (one per app) and combine them into a single ``MCPServer``.
"""

from __future__ import annotations

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
from rest_framework_mcp import MCPServer
from rest_framework_mcp.protocol.prompt_argument import PromptArgument
from rest_framework_mcp.protocol.prompt_message import PromptMessage


def build_server() -> MCPServer:
    """Construct and populate the example MCP server."""
    server = MCPServer(name="invoicing", description="Demo invoicing MCP surface")

    # ----- Service tools (mutations) -----

    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=create_invoice,
            input_serializer=InvoiceInputSerializer,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="Create a new invoice with a unique number and a positive amount.",
    )

    server.register_service_tool(
        name="invoices.mark_sent",
        spec=ServiceSpec(
            service=mark_invoice_sent,
            input_serializer=MarkSentInputSerializer,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="Flip an invoice's ``sent`` flag.",
    )

    # ----- Selector tool (read with filter / order / paginate) -----

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            selector=list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="List invoices, optionally filtered / ordered / paginated.",
        filter_set=InvoiceFilterSet,
        ordering_fields=["created_at", "amount_cents"],
        paginate=True,
    )

    # ----- Resource (single invoice by PK via URI template) -----

    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            selector=get_invoice,
            output_serializer=InvoiceOutputSerializer,
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
    )

    return server
