"""Mount a purpose-built server as a throwaway URL conf.

Scalar config is resolved once, in ``MCPServer.__init__``. That is what lets two
servers in one project differ — and it means a test can no longer mutate
``settings.REST_FRAMEWORK_MCP`` and expect an already-mounted server to notice.
Tests that need custom scalars build their own server and mount it here::

    server = build_server(config=build_mcp_config(allowed_origins=["https://ok"]))
    with override_settings(ROOT_URLCONF=urlconf_for(server)):
        ...

Django accepts a module object for ``ROOT_URLCONF`` (its resolver does
``getattr(urlconf_module, "urlpatterns", ...)``), so no file on disk is needed
and each test gets an isolated conf.
"""

from __future__ import annotations

import types

from django.urls import path

from rest_framework_mcp import MCPServer


def urlconf_for(
    server: MCPServer, *, prefix: str = "mcp/", is_async: bool = False
) -> types.ModuleType:
    """Return a throwaway URL-conf module mounting ``server`` at ``prefix``."""
    module = types.ModuleType("tests.testapp._dynamic_urlconf")
    module.urlpatterns = [path(prefix, server.async_urls if is_async else server.urls)]  # type: ignore[attr-defined]
    return module


__all__ = ["urlconf_for"]
