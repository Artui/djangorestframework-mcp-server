from __future__ import annotations

from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.serializers import InvoiceInputSerializer, InvoiceOutputSerializer
from tests.testapp.services import create_invoice, get_invoice, list_invoices


def build_server(*, config: MCPConfig | None = None) -> MCPServer:
    """Construct the test MCP server with two tools and two resources.

    A factory rather than a module-level instance keeps tests independent —
    each test that needs its own configuration can build a fresh server.

    ``config`` overrides the scalars. Since they are resolved once in
    ``MCPServer.__init__``, a test that needs non-default scalars must build its
    own server (and mount it via ``urlconf_for``) rather than mutating
    ``settings.REST_FRAMEWORK_MCP`` around an already-mounted one.
    """
    server = MCPServer(
        name="testapp",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
        config=config,
    )

    server.register_service_tool(
        name="invoices.create",
        spec=ServiceSpec(
            service=create_invoice,
            input_serializer=InvoiceInputSerializer,
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
        ),
        description="Create an invoice.",
    )
    server.register_resource(
        name="invoice",
        uri_template="invoices://{pk}",
        selector=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            selector=get_invoice,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="Fetch a single invoice by id.",
    )
    server.register_resource(
        name="invoices",
        uri_template="invoices://",
        selector=SelectorSpec(
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=InvoiceOutputSerializer,
        ),
        description="List all invoices.",
    )
    return server
