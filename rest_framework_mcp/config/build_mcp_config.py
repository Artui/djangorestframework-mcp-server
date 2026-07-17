from __future__ import annotations

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import OutputFormat


def build_mcp_config(
    *,
    protocol_versions: tuple[str, ...] | list[str] | None = None,
    require_protocol_version_header: bool | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    default_output_format: OutputFormat | str | None = None,
    max_request_bytes: int | None = None,
    page_size: int | None = None,
    include_validation_value: bool | None = None,
    record_service_exceptions: bool | None = None,
    filter_listings_by_permissions: bool | None = None,
    require_tool_permissions: bool | None = None,
) -> MCPConfig:
    """Resolve a :class:`MCPConfig` from ``REST_FRAMEWORK_MCP``, applying overrides.

    The single place the scalar settings are read. :class:`MCPServer` calls this
    once in ``__init__``; nothing reads these settings per request, which is what
    lets two servers in one project hold different values.

    Every argument is ``None`` by default, meaning "take it from settings". Pass
    one to override just that field for this server::

        MCPServer(name="internal", config=build_mcp_config(page_size=500))

    Use this rather than constructing :class:`MCPConfig` directly — it is what
    layers your overrides *over* the project's settings instead of discarding
    them.
    """
    return MCPConfig(
        protocol_versions=tuple(
            protocol_versions if protocol_versions is not None else get_setting("PROTOCOL_VERSIONS")
        ),
        require_protocol_version_header=bool(
            require_protocol_version_header
            if require_protocol_version_header is not None
            else get_setting("REQUIRE_PROTOCOL_VERSION_HEADER")
        ),
        include_structured_content=bool(
            include_structured_content
            if include_structured_content is not None
            else get_setting("INCLUDE_STRUCTURED_CONTENT")
        ),
        include_output_schema=bool(
            include_output_schema
            if include_output_schema is not None
            else get_setting("INCLUDE_OUTPUT_SCHEMA")
        ),
        allowed_origins=tuple(
            allowed_origins if allowed_origins is not None else get_setting("ALLOWED_ORIGINS")
        ),
        default_output_format=OutputFormat.coerce(
            default_output_format
            if default_output_format is not None
            else get_setting("DEFAULT_OUTPUT_FORMAT")
        ),
        max_request_bytes=int(
            max_request_bytes if max_request_bytes is not None else get_setting("MAX_REQUEST_BYTES")
        ),
        page_size=int(page_size if page_size is not None else get_setting("PAGE_SIZE")),
        include_validation_value=bool(
            include_validation_value
            if include_validation_value is not None
            else get_setting("INCLUDE_VALIDATION_VALUE")
        ),
        record_service_exceptions=bool(
            record_service_exceptions
            if record_service_exceptions is not None
            else get_setting("RECORD_SERVICE_EXCEPTIONS")
        ),
        filter_listings_by_permissions=bool(
            filter_listings_by_permissions
            if filter_listings_by_permissions is not None
            else get_setting("FILTER_LISTINGS_BY_PERMISSIONS")
        ),
        require_tool_permissions=bool(
            require_tool_permissions
            if require_tool_permissions is not None
            else get_setting("REQUIRE_TOOL_PERMISSIONS")
        ),
    )


__all__ = ["build_mcp_config"]
