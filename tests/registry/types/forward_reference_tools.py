"""A decorated spread service whose ``Unpack[...]`` names a ``TypedDict`` declared below it.

Not a test module: ``test_tool_binding_reject`` imports it, and the import is
what fails. The decorator registers the tool while the module is still running,
before ``Changes`` exists, so the annotation does not resolve at registration
though it would once the module had imported.
"""

from __future__ import annotations

from typing import Any

from rest_framework.permissions import AllowAny
from typing_extensions import TypedDict, Unpack

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.constants import ArgumentBinding, UnknownArguments

server = MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=None)


@server.service_tool(
    name="touch",
    description="Touch a task.",
    permissions=[AllowAny],
    argument_binding=ArgumentBinding.SPREAD_AUTHOR_WINS,
    unknown_arguments=UnknownArguments.REJECT,
)
def touch(**changes: Unpack[Changes]) -> dict[str, Any]:
    return dict(changes)


class Changes(TypedDict, total=False):
    title: str
