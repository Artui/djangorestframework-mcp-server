"""MCP server factory for the conformance suite.

Exercises every Phase 10 feature in one server so the conformance tests
can verify each binding contract end-to-end through the real Django
URL conf + transport stack. Each tool is named for the feature it
demonstrates so failing assertions point at the right binding.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers as drf_serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import (
    ArgumentBinding,
    MCPServer,
    SelectorDefaults,
    ServiceDefaults,
    ToolDefinition,
    UnknownArguments,
    register_tools,
)
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore
from tests.testapp.models import Invoice


class _ProjectScopedArgs(drf_serializers.Serializer):
    """One strictly-validated field, exercised by the unknown-args tests."""

    project_id = drf_serializers.CharField()


class _InvoiceOutSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents"]


# ---------- Tool callables ----------


def _merge_service(*, number: str, amount_cents: int) -> dict[str, Any]:
    """Declares its arguments as kwargs — proves ``MERGE`` binding wires them."""
    return {"number": number, "amount_cents": amount_cents}


def _project_scoped_selector(*, project_id: str, **rest: Any) -> Any:
    """Echoes the validated + (under PASSTHROUGH) unknown keys it received.

    ``**rest`` absorbs every pool kwarg the explicit ``project_id`` doesn't
    take — which includes the transport-controlled ``request`` / ``user``
    / ``data`` pool seeds. Filter those out before returning so the
    response is JSON-serialisable (the seeds are not).
    """
    serialisable_rest = {k: v for k, v in rest.items() if k not in {"request", "user", "data"}}
    return [{"project_id": project_id, "rest": serialisable_rest}]


def _gated_service() -> dict[str, str]:
    return {"ok": "true"}


def _bulk_list() -> list[dict[str, str]]:
    return [{"sentinel": "bulk"}]


# ---------- Server factory ----------


def build_conformance_server() -> MCPServer:
    """Build a server exercising every Phase 10 binding contract."""
    server = MCPServer(
        name="conformance",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )

    # 10a — argument_binding=MERGE on a service tool.
    server.register_service_tool(
        name="conformance.merge",
        spec=ServiceSpec(service=_merge_service, atomic=False),
        argument_binding=ArgumentBinding.MERGE,
    )

    # 10b — unknown-args REJECT on a strict-known selector.
    server.register_selector_tool(
        name="conformance.reject_unknown",
        spec=SelectorSpec(selector=_project_scoped_selector),
        input_serializer=_ProjectScopedArgs,
        unknown_arguments=UnknownArguments.REJECT,
    )
    # 10b — unknown-args PASSTHROUGH sibling.
    server.register_selector_tool(
        name="conformance.passthrough_unknown",
        spec=SelectorSpec(selector=_project_scoped_selector),
        input_serializer=_ProjectScopedArgs,
        unknown_arguments=UnknownArguments.PASSTHROUGH,
    )

    # 10c — declarative bulk registration with mixed kinds + defaults.
    register_tools(
        server,
        definitions=[
            ToolDefinition.selector(
                name="conformance.bulk_listed",
                spec=SelectorSpec(selector=_bulk_list),
                description="Registered via register_tools()",
            ),
            ToolDefinition.service(
                name="conformance.bulk_gated",
                spec=ServiceSpec(
                    service=_gated_service,
                    atomic=False,
                    permission_classes=[IsAuthenticated],
                ),
                description="Bulk registration honors spec.permission_classes",
            ),
        ],
        service_defaults=ServiceDefaults(unknown_arguments=UnknownArguments.IGNORE),
        selector_defaults=SelectorDefaults(),
    )

    # 10-pre / 10g — spec.permission_classes wiring on a non-bulk binding.
    server.register_service_tool(
        name="conformance.gated",
        spec=ServiceSpec(
            service=_gated_service, atomic=False, permission_classes=[IsAuthenticated]
        ),
    )

    return server
