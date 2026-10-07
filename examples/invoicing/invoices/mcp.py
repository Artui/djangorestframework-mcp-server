"""MCP server factory for the invoicing example.

All registrations live in one place so the wire surface is easy to
read in a single pass. Real projects can split this across multiple
modules (one per app) and combine them into a single ``MCPServer``.
"""

from __future__ import annotations

from rest_framework.permissions import AllowAny
from rest_framework_services import DEFAULT_POOL_SEEDS
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from invoices.filters import InvoiceFilterSet
from invoices.models import Invoice
from invoices.selectors import (
    find_invoice,
    get_invoice,
    invoice_by_number,
    list_invoices,
    outstanding_total,
)
from invoices.serializers import (
    InvoiceInputSerializer,
    InvoiceOutputSerializer,
    MarkSentInputSerializer,
    SelectableInvoiceSerializer,
    SetAmountInputSerializer,
)
from invoices.services import (
    apply_credit,
    create_invoice,
    mark_invoice_sent,
    set_invoice_amount,
)
from rest_framework_mcp import (
    AgentConventions,
    ArgumentBinding,
    MCPServer,
    PromptArgument,
    PromptMessage,
    QueryParam,
)
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
        # What every spec on this mount may read without it being a tool
        # argument: here a fixed currency, which ``invoices.outstanding``
        # reads. A real project resolves it per caller, e.g.
        # ``currency=lambda *, user: user.organisation.currency``. A
        # registered name is reserved, so a client cannot send its own.
        pool_seeds=DEFAULT_POOL_SEEDS.extend(currency=lambda: "EUR"),
        # The sentences this server writes for the model, rather than any one
        # tool. Each field left alone keeps the package's wording; this one
        # tells a model that left out an invoice's number where to find it,
        # so ``invoices.set_amount`` called without ``number`` says so.
        conventions=AgentConventions(
            missing_arguments=(
                "Missing required argument(s): {names}. "
                "Look the invoice up with `invoices.list` if you do not have it."
            ),
        ),
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

    server.register_service_tool(
        name="invoices.set_amount",
        spec=ServiceSpec(
            permission_classes=[AllowAny],
            service=set_invoice_amount,
            input_serializer=SetAmountInputSerializer,
            # The target is looked up by number. The tool's ``inputSchema``
            # advertises ``number`` (required, as the selector marks it) beside
            # ``amount_cents``, because dispatch hands the arguments to this
            # selector too.
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=invoice_by_number
            ),
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
            # Setting an amount twice is setting it once, so the tool lists
            # ``idempotentHint: true`` and a client may retry it freely.
            idempotent=True,
        ),
        description="Set the amount of the invoice with the given number.",
    )

    server.register_service_tool(
        name="invoices.apply_credit",
        spec=ServiceSpec(
            permission_classes=[AllowAny],
            service=apply_credit,
            # No input serializer: the arguments are spread into the service's
            # own parameters, so the ``inputSchema`` lists ``credit_cents``
            # beside the lookup's ``number``. Under the default
            # ``unknown_arguments=REJECT`` that set is closed
            # (``additionalProperties: false``), and a call naming anything else
            # is a ``validation_error`` rather than an argument dropped unread.
            instance_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE, selector=invoice_by_number
            ),
            # No ``selector`` here, so nothing is re-read: the service's own
            # return is presented, and a credit that settles the invoice
            # returns ``None``. ``allow_none=True`` says so, and the
            # ``outputSchema`` admits the ``{}`` that call is served as.
            output_selector_spec=SelectorSpec(
                kind=SelectorKind.RETRIEVE,
                output_serializer=InvoiceOutputSerializer,
            ),
            allow_none=True,
        ),
        argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
        description=(
            "Take a credit off the invoice with the given number; "
            "an empty object when the credit settles it."
        ),
    )

    # ----- Selector tool (read with filter / order / paginate / select) -----

    server.register_selector_tool(
        name="invoices.list",
        spec=SelectorSpec(
            permission_classes=[AllowAny],
            kind=SelectorKind.LIST,
            selector=list_invoices,
            output_serializer=SelectableInvoiceSerializer,
            filter_set=InvoiceFilterSet,
        ),
        description="List invoices, optionally filtered / ordered / paginated.",
        # Ordering is not a registration knob: ``InvoiceFilterSet`` declares an
        # ``OrderingFilter``, and that declaration is what the tool advertises.
        paginate=True,
        # Field selection, read by the serializer off ``request.query_params``.
        # Because the tool pages, the advertised description gains a sentence
        # saying the selection applies to each item, never to the envelope;
        # a selection that names the envelope anyway comes back as a
        # ``validation_error`` rather than as a page of empty rows.
        query_params=(
            QueryParam(
                "fields",
                description="Comma-separated invoice fields to return, e.g. id,number",
            ),
        ),
    )

    server.register_selector_tool(
        name="invoices.find",
        spec=SelectorSpec(
            permission_classes=[AllowAny],
            kind=SelectorKind.RETRIEVE,
            selector=find_invoice,
            output_serializer=InvoiceOutputSerializer,
            # A miss is an answer, not an error: the call succeeds with
            # ``structuredContent: {}``, which the ``outputSchema`` admits.
            allow_none=True,
        ),
        description="Find an invoice by number; an empty object when there is none.",
    )

    server.register_selector_tool(
        name="invoices.outstanding",
        spec=SelectorSpec(
            permission_classes=[AllowAny],
            kind=SelectorKind.RETRIEVE,
            # Reads ``currency`` from the server's ``pool_seeds``.
            selector=outstanding_total,
        ),
        description="The total of unsent invoices, in the account's currency.",
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
