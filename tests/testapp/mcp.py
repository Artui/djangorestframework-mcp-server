from __future__ import annotations

from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
from tests.testapp.services import create_invoice, get_invoice, list_invoices


def build_server() -> MCPServer:
    """Construct the test MCP server with two tools and two resources.

    A factory rather than a module-level instance keeps tests independent —
    each test that needs its own configuration can build a fresh server.
    """
    server = MCPServer(name="testapp")

    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=create_invoice,
            input_serializer=InvoiceInputSerializer,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="Create an invoice.",
    )
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(selector=get_invoice, output_serializer=InvoiceOutputSerializer),
        description="Fetch a single invoice by id.",
    )
    server.register_resource(
        name="invoices",
        uri_template="invoices://",
        selector=SelectorSpec(selector=list_invoices, output_serializer=InvoiceOutputSerializer),
        description="List all invoices.",
    )
    return server
