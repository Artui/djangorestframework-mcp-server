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
    modern client leads with. It answers what ``initialize`` answered, minus the
    negotiation: nothing is agreed, no state is created, and a client that
    already knows what it wants may skip it entirely and handle a version error
    if it guessed wrong.

    ⚠ **Answered in both eras, deliberately** — answering it before the
    transport fork exists is what lets a modern client probe this server today,
    and a legacy client that never asks is unaffected.

    ⚠ **But the capabilities are the caller's, not the server's.** The versions
    and the identity are properties of the endpoint; two of the capabilities are
    not, because they can only be *reached* by a modern client — see
    :func:`~rest_framework_mcp.handlers.handle_initialize.build_capabilities`.
    So the era comes from what this caller declared, which for a header-less
    request is the configured default and therefore modern on a modern-only
    server.

    ``params`` is accepted and ignored: the request carries no parameters beyond
    the standard ``_meta``, whose per-request protocol fields belong to the
    transport rather than to this handler.
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
    # Cacheable, and ``public`` regardless of the listing filter: nothing here
    # varies by caller. It is the one catalog result that stays shareable even
    # when ``FILTER_LISTINGS_BY_PERMISSIONS`` is on, because it reports *which*
    # capabilities exist rather than which bindings this caller may see.
    return {
        **result,
        **catalog_cache_hints(
            ttl_ms=context.config.catalog_cache_ttl_ms, filtered_by_permissions=False
        ),
    }


__all__ = ["handle_server_discover"]
