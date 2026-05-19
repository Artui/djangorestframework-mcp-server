"""Micro-benchmark for ``tools/call`` overhead.

Compares three call paths for the same trivial service callable:

1. **Direct** — call the Python function with already-validated kwargs.
2. **Dispatch** — go through ``handle_tools_call`` (input validation,
   permission check, kwarg-pool resolution, output serialization,
   ``ToolResult`` shaping) but *not* through the HTTP transport.
3. **HTTP** — full round-trip through the Django test client
   (origin / version / session validation, JSON-RPC parsing, dispatch,
   response serialization).

The numbers tell you where the overhead actually lives — handler-side
work vs. transport boilerplate — so you know whether to optimise the
dispatch pipeline or the transport, if optimisation is ever needed.

Run from the repo root::

    uv run python scripts/benchmark.py
"""

from __future__ import annotations

import os
import statistics
import time
from typing import Any

# Set up Django before importing anything that reads settings.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "tests.conftest_settings")

import django  # noqa: E402

django.setup()

import json  # noqa: E402

from django.test import Client  # noqa: E402
from rest_framework import serializers  # noqa: E402
from rest_framework_services.types.service_spec import ServiceSpec  # noqa: E402

from rest_framework_mcp import MCPServer  # noqa: E402
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend  # noqa: E402
from rest_framework_mcp.auth.token_info import TokenInfo  # noqa: E402
from rest_framework_mcp.handlers.context import MCPCallContext  # noqa: E402
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call  # noqa: E402
from rest_framework_mcp.transport.in_memory_session_store import (  # noqa: E402
    InMemorySessionStore,
)


class _Input(serializers.Serializer):
    n = serializers.IntegerField()


def _service(*, data: dict[str, Any]) -> dict[str, Any]:
    return {"result": data["n"] * 2}


def _build_server() -> MCPServer:
    server = MCPServer(
        name="bench",
        auth_backend=AllowAnyBackend(),
        session_store=InMemorySessionStore(),
    )
    server.register_service_tool(
        name="double",
        spec=ServiceSpec(service=_service, input_serializer=_Input),
    )
    return server


def _bench(name: str, fn, iterations: int = 5_000) -> tuple[str, float, float]:
    # Warm-up.
    for _ in range(min(500, iterations // 10)):
        fn()
    samples: list[float] = []
    for _ in range(5):
        start: float = time.perf_counter()
        for _ in range(iterations):
            fn()
        samples.append((time.perf_counter() - start) / iterations)
    median: float = statistics.median(samples)
    stdev: float = statistics.stdev(samples) if len(samples) > 1 else 0.0
    return name, median, stdev


def _direct() -> None:
    _service(data={"n": 21})


def _dispatch_factory(server: MCPServer):
    from django.http import HttpRequest

    request = HttpRequest()
    ctx = MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version="2025-11-25",
    )

    def fn() -> None:
        handle_tools_call({"name": "double", "arguments": {"n": 21}}, ctx)

    return fn


def _http_factory(server: MCPServer):
    from django.urls import include, path
    from django.urls.resolvers import URLResolver, get_resolver

    # Mount the server's URLs by monkey-patching the resolver.
    # Easier than building a full url conf module for a benchmark.
    urlpatterns = [path("mcp/", include(server.urls))]

    resolver: URLResolver = get_resolver()
    original = resolver.url_patterns
    resolver.url_patterns = urlpatterns
    resolver._populate()  # type: ignore[attr-defined]

    client = Client()
    init_resp = client.post(
        "/mcp/",
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "bench", "version": "0"},
                },
            }
        ),
        content_type="application/json",
        HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
        HTTP_ORIGIN="http://localhost",
    )
    sid: str = init_resp.headers["Mcp-Session-Id"]

    body: str = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "double", "arguments": {"n": 21}},
        }
    )

    def fn() -> None:
        client.post(
            "/mcp/",
            body,
            content_type="application/json",
            HTTP_MCP_PROTOCOL_VERSION="2025-11-25",
            HTTP_ORIGIN="http://localhost",
            HTTP_MCP_SESSION_ID=sid,
        )

    def cleanup() -> None:
        resolver.url_patterns = original
        resolver._populate()  # type: ignore[attr-defined]

    return fn, cleanup


def main() -> None:
    server: MCPServer = _build_server()
    direct_name, direct_med, direct_sd = _bench("direct call", _direct)
    disp_name, disp_med, disp_sd = _bench("dispatch", _dispatch_factory(server))

    http_fn, cleanup = _http_factory(server)
    try:
        http_name, http_med, http_sd = _bench("HTTP round-trip", http_fn, iterations=1_000)
    finally:
        cleanup()

    print(f"{'path':<22}  {'median (µs)':>12}  {'± stdev':>10}  {'overhead vs direct':>20}")
    print("-" * 70)
    for name, med, sd in [
        (direct_name, direct_med, direct_sd),
        (disp_name, disp_med, disp_sd),
        (http_name, http_med, http_sd),
    ]:
        ratio: str = f"{(med / direct_med):>6.1f}x" if med else "n/a"
        print(f"{name:<22}  {med * 1e6:>12.1f}  {sd * 1e6:>10.1f}  {ratio:>20}")


if __name__ == "__main__":
    main()
