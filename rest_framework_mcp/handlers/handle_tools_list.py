from __future__ import annotations

from typing import Any

from rest_framework_mcp.constants import JsonRpcErrorCode, ToolContentKind
from rest_framework_mcp.handlers.is_binding_listable import is_binding_listable
from rest_framework_mcp.handlers.pagination import paginate
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.handlers.utils import (
    advertises_closed_schema,
    catalog_cache_hints,
    resolve_bound,
)
from rest_framework_mcp.output.resolve_structured_output import resolve_structured_output
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.types.tool import Tool
from rest_framework_mcp.registry.types.chain_tool_binding import ChainToolBinding
from rest_framework_mcp.registry.types.selector_tool_binding import SelectorToolBinding
from rest_framework_mcp.schema.agent_conventions import append_agent_conventions
from rest_framework_mcp.schema.chain_tool_schema import build_chain_tool_input_schema
from rest_framework_mcp.schema.output_schema import build_output_schema
from rest_framework_mcp.schema.selector_tool_schema import build_selector_tool_input_schema
from rest_framework_mcp.schema.service_tool_schema import build_service_tool_input_schema


def handle_tools_list(
    params: dict[str, Any] | None,
    context: MCPCallContext,
) -> dict[str, Any] | JsonRpcError:
    """Return the catalog of tools the server exposes, paginated.

    JSON Schemas are rebuilt on every request rather than cached on the
    binding — discovery already runs at router-construction time, so the
    relative cost is small and it keeps bindings cheap to construct.

    Pagination is opaque-cursor per the MCP spec: clients pass back the
    ``nextCursor`` they received without inspecting it.
    """
    cursor: Any = (params or {}).get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, "'cursor' must be a string")

    # Filtered before paginating, so ``nextCursor`` reflects the visible slice
    # rather than the full registry.
    bindings = list(context.tools.all())
    if context.config.filter_listings_by_permissions:
        bindings = [
            b for b in bindings if is_binding_listable(b, context.http_request, context.token)
        ]

    try:
        page, next_cursor = paginate(bindings, cursor, page_size=context.config.page_size)
    except ValueError as exc:
        return JsonRpcError(JsonRpcErrorCode.INVALID_PARAMS, str(exc))

    tools: list[dict[str, Any]] = []
    for binding in page:
        # Chain tools advertise their resolved input serializer; selector tools
        # merge filter / ordering / pagination args in; service tools expose the
        # input serializer's schema verbatim.
        if isinstance(binding, ChainToolBinding):
            input_schema = build_chain_tool_input_schema(binding)
        elif isinstance(binding, SelectorToolBinding):
            input_schema = build_selector_tool_input_schema(
                binding,
                max_page_size=resolve_bound(binding.max_page_size, context.config.max_page_size),
            )
        else:
            input_schema = build_service_tool_input_schema(binding)
        # Stamped to match what the runtime actually enforces; every builder
        # returns a ``"type": "object"`` shape, so this reaches every schema.
        input_schema = dict(input_schema)
        input_schema["additionalProperties"] = not advertises_closed_schema(binding)
        # ``outputSchema`` and ``structuredContent`` are independently
        # toggleable, but the spec forbids advertising the schema while
        # suppressing the content — ``resolve_structured_output`` raises
        # ``ImproperlyConfigured`` for that combination before anything is
        # serialized.
        emit_output_schema, _emit_structured_content = resolve_structured_output(
            include_output_schema_override=binding.include_output_schema,
            include_structured_content_override=binding.include_structured_content,
            binding_name=binding.name,
            default_output_schema=context.config.include_output_schema,
            default_structured_content=context.config.include_structured_content,
        )
        # ``agent_output_serializer`` reconciles where each binding kind keeps
        # its response serializer, and carries the same agent markings the
        # dispatch path projects the payload through -- one declaration, so a
        # schema cannot advertise a field the payload no longer carries.
        # ``outputSchema`` must match the payload shape the dispatch pipeline
        # actually emits — a LIST tool returns a bare array or the pagination
        # envelope — so the selector schema is kind-aware.
        if isinstance(binding, SelectorToolBinding):
            output_schema = build_output_schema(
                binding.agent_output_serializer,
                kind=binding.kind,
                paginate=binding.paginate,
                projection=binding.agent_projection,
            )
        else:
            output_schema = build_output_schema(
                binding.agent_output_serializer, projection=binding.agent_projection
            )
        # A media tool has no JSON result to describe, so the schema is dropped
        # rather than advertised over a payload arriving as an image block.
        # Resource links keep theirs: the links are JSON.
        if binding.content_kind in (ToolContentKind.IMAGE, ToolContentKind.AUDIO):
            output_schema = None
        tool = Tool(
            name=binding.name,
            description=append_agent_conventions(binding.description, binding.agent_projection),
            title=binding.title,
            icons=binding.icons,
            input_schema=input_schema,
            output_schema=(output_schema if emit_output_schema else None),
            annotations=dict(binding.annotations) or None,
            meta=dict(binding.meta) or None,
        )
        tools.append(tool.to_dict())
    response: dict[str, Any] = {
        "tools": tools,
        **catalog_cache_hints(
            ttl_ms=context.config.catalog_cache_ttl_ms,
            filtered_by_permissions=context.config.filter_listings_by_permissions,
        ),
    }
    if next_cursor is not None:
        response["nextCursor"] = next_cursor
    return response


__all__ = ["handle_tools_list"]
