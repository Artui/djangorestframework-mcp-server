from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import MODERN_PROTOCOL_VERSIONS
from rest_framework_mcp.handlers.handle_initialize import build_capabilities
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import catalog_cache_hints
from rest_framework_mcp.protocol.build_server_info import build_server_info
from rest_framework_mcp.protocol.types.discover_result import DiscoverResult
from rest_framework_mcp.protocol.types.implementation import Implementation
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError


def handle_server_discover(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Report this server's protocol versions, capabilities and identity.

    A **MUST**-implement method of the ``2026-07-28`` revision and the request a
    modern client leads with. It answers what ``initialize`` answered minus the
    negotiation: nothing is agreed and no state is created.

    **Answered in both eras, deliberately** — answering before the transport
    fork exists is what lets a modern client probe this server today, and a
    legacy client that never asks is unaffected.

    **The capabilities are the caller's, not the server's.** The versions and
    identity are properties of the endpoint; two of the capabilities are not,
    since only a modern client can reach them — see
    ``build_capabilities``.
    The era therefore comes from what this caller declared, which for a
    header-less request is the configured default.

    ``params`` is accepted and ignored: the request carries nothing beyond the
    standard ``_meta``, which belongs to the transport.
    """
    server_info: Implementation | None = context.server_info
    if server_info is None:
        # The degenerate path, as in ``initialize``: a context assembled without
        # an owning server (a hand-wired viewset).
        server_info = build_server_info()
    result: dict[str, Any] = DiscoverResult(
        supported_versions=context.config.protocol_versions,
        capabilities=build_capabilities(
            context, modern=context.protocol_version in MODERN_PROTOCOL_VERSIONS
        ),
        server_info=server_info,
        instructions=context.instructions,
    ).to_dict()
    # The one catalog result that stays ``public`` even under
    # ``FILTER_LISTINGS_BY_PERMISSIONS``: it reports *which* capabilities exist,
    # not which bindings this caller may see, so nothing varies by caller.
    return {
        **result,
        **catalog_cache_hints(
            ttl_ms=context.config.catalog_cache_ttl_ms, filtered_by_permissions=False
        ),
    }


__all__ = ["handle_server_discover"]
