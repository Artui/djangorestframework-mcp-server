"""A tool that cannot finish without asking, and the contexts that ask it.

``rows.delete`` is deliberately a two-question service: past 100 rows it wants a
confirmation, past 1000 it also wants a reason. One service therefore exercises
the single-round case, the multi-round case, and the thing multi-round exists
for — that the first answer must survive the second question.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.http import HttpRequest
from rest_framework import serializers as drf_serializers
from rest_framework_services.exceptions.additional_input_required import AdditionalInputRequired
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.config.build_mcp_config import build_mcp_config
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

TOOL: str = "rows.delete"


class DeleteInput(drf_serializers.Serializer):
    count = drf_serializers.IntegerField()
    confirmed = drf_serializers.BooleanField(required=False, default=False)
    reason = drf_serializers.CharField(required=False, allow_blank=True, default="")


def delete_rows(*, data: dict[str, Any]) -> dict[str, Any]:
    if data["count"] > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{data['count']} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    if data["count"] > 1000 and not data["reason"]:
        raise AdditionalInputRequired(
            "Deleting more than 1000 rows needs a reason.",
            schema={"reason": {"type": "string", "title": "Why?"}},
        )
    return {"deleted": data["count"], "confirmed": data["confirmed"], "reason": data["reason"]}


def unspecific_service(*, data: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """A service that says it needs something without saying what."""
    raise AdditionalInputRequired("This needs something I cannot describe.")


def unrenderable_service(*, data: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
    """A service whose schema a client could not render — an author's bug."""
    raise AdditionalInputRequired(
        "Tell me about yourself.",
        schema={"profile": {"type": "object", "properties": {"name": {"type": "string"}}}},
    )


@pytest.fixture
def server() -> MCPServer:
    built = MCPServer(
        name="elicitation-fixture",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    built.register_service_tool(
        name=TOOL,
        description="Delete rows.",
        spec=ServiceSpec(service=delete_rows, input_serializer=DeleteInput, atomic=False),
    )
    built.register_service_tool(
        name="rows.vague",
        description="Needs something unnamed.",
        spec=ServiceSpec(service=unspecific_service, input_serializer=DeleteInput, atomic=False),
    )
    built.register_service_tool(
        name="rows.unrenderable",
        description="Asks for a shape no form can hold.",
        spec=ServiceSpec(service=unrenderable_service, input_serializer=DeleteInput, atomic=False),
    )
    return built


def context(
    server: MCPServer,
    *,
    capabilities: dict[str, Any] | None = None,
    protocol_version: str = "2026-07-28",
    user: Any = None,
    **config_overrides: Any,
) -> MCPCallContext:
    """A modern request context that can be asked, unless told otherwise.

    ``capabilities=None`` declares form elicitation — the ordinary case. Pass
    ``{}`` for a client that declared none, which is also what every legacy
    request looks like from a handler's side.
    """
    http_request = HttpRequest()
    http_request.method = "POST"
    return MCPCallContext(
        http_request=http_request,
        token=TokenInfo(user=user),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=protocol_version,
        client_capabilities={"elicitation": {}} if capabilities is None else capabilities,
        config=build_mcp_config(**config_overrides) if config_overrides else server._config,
    )
