"""End-to-end unknown-argument policy behaviour through the live transport.

Verifies the wire shape of ``REJECT``: an ``isError`` ``validation_error``
result with a ``non_field_errors`` detail. ``PASSTHROUGH`` has no wire shape any
more: it is refused when the tool is registered, so a server carrying it never
starts.
"""

from __future__ import annotations

import pytest
from django.core.exceptions import ImproperlyConfigured
from django_service_specs import SelectorKind, UnknownArguments
from rest_framework_services.types.selector_spec import SelectorSpec

from rest_framework_mcp import MCPServer
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from tests.conformance.mcp import _project_scoped_selector, _ProjectScopedArgs
from tests.utils import tool_error

pytestmark = pytest.mark.urls("tests.conformance.urls")


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_key_returns_a_validation_error_result(
    jsonrpc, initialized_session: str
) -> None:
    """Input validation, so a tool execution error the model can read, not ``-32602``."""
    response = jsonrpc(
        "tools/call",
        {
            "name": "conformance.reject_unknown",
            "arguments": {"project_id": "p1", "rogue": "x"},
        },
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert "error" not in body, body
    error = tool_error(body["result"])
    assert error["type"] == "validation_error"
    detail = error["detail"]
    assert "non_field_errors" in detail
    assert "rogue" in detail["non_field_errors"][0]


@pytest.mark.django_db(transaction=True)
def test_reject_unknown_accepts_known_only(jsonrpc, initialized_session: str) -> None:
    response = jsonrpc(
        "tools/call",
        {"name": "conformance.reject_unknown", "arguments": {"project_id": "p1"}},
        session_id=initialized_session,
    )
    assert response.status_code == 200, response.content
    body = response.json()
    assert "result" in body, body


def test_passthrough_is_refused_where_the_conformance_server_would_have_carried_it() -> None:
    """The selector the conformance server's ``REJECT`` tool serves, registered
    under ``PASSTHROUGH``: refused at registration, so no call can reach it."""
    server = MCPServer(name="conformance", auth_backend=AllowAnyBackend())

    with pytest.raises(ImproperlyConfigured, match="UnknownArguments.PASSTHROUGH is retired"):
        server.register_selector_tool(
            name="conformance.passthrough_unknown",
            spec=SelectorSpec(kind=SelectorKind.LIST, selector=_project_scoped_selector),
            input_serializer=_ProjectScopedArgs,
            unknown_arguments=UnknownArguments.PASSTHROUGH,
        )

    assert server.tools.get("conformance.passthrough_unknown") is None
