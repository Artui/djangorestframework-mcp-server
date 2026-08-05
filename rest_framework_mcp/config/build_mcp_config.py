from __future__ import annotations

from typing import Any

from django.core.exceptions import ImproperlyConfigured
from rest_framework_services import UNSET, UnsetType

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.config.types.mcp_config import MCPConfig
from rest_framework_mcp.constants import OutputFormat


def _nullable_int(value: Any) -> int | None:
    """Coerce a resolved bound to ``int``, preserving ``None`` (= disabled)."""
    return None if value is None else int(value)


def _nullable_float(value: Any) -> float | None:
    """Coerce a resolved bound to ``float``, preserving ``None`` (= disabled)."""
    return None if value is None else float(value)


def _bound(value: int | float | None | UnsetType, setting: str) -> Any:
    """Resolve a nullable bound: ``UNSET`` → settings, ``None`` → disabled.

    The three outbound bounds differ from every other field here in that
    ``None`` is a *meaningful value* ("no ceiling"), so it cannot double as
    the "not supplied" sentinel the other arguments use. ``UNSET``
    (drf-services' sentinel, reused rather than re-invented) carries that
    distinction: ``build_mcp_config()`` takes the setting,
    ``build_mcp_config(dispatch_timeout=None)`` genuinely disables the
    deadline for this server.
    """
    return get_setting(setting) if isinstance(value, UnsetType) else value


def build_mcp_config(
    *,
    protocol_versions: tuple[str, ...] | list[str] | None = None,
    require_protocol_version_header: bool | None = None,
    sessions_enabled: bool | None = None,
    include_structured_content: bool | None = None,
    include_output_schema: bool | None = None,
    allowed_origins: tuple[str, ...] | list[str] | None = None,
    default_output_format: OutputFormat | str | None = None,
    max_request_bytes: int | None = None,
    max_progress_notifications: int | None = None,
    max_result_bytes: int | None | UnsetType = UNSET,
    max_page_size: int | None | UnsetType = UNSET,
    dispatch_timeout: float | None | UnsetType = UNSET,
    page_size: int | None = None,
    include_validation_value: bool | None = None,
    record_service_exceptions: bool | None = None,
    filter_listings_by_permissions: bool | None = None,
    require_tool_permissions: bool | None = None,
    require_tool_descriptions: bool | None = None,
    require_list_pagination: bool | None = None,
    catalog_cache_ttl_ms: int | None = None,
    resource_cache_ttl_ms: int | None = None,
    task_ttl_ms: int | None = None,
    task_poll_interval_ms: int | None = None,
    subscription_max_seconds: float | None | UnsetType = UNSET,
    max_concurrent_subscriptions: int | None | UnsetType = UNSET,
    input_request_ttl_seconds: int | None = None,
    max_input_rounds: int | None = None,
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
    resolved_versions: tuple[str, ...] = tuple(
        protocol_versions if protocol_versions is not None else get_setting("PROTOCOL_VERSIONS")
    )
    if not resolved_versions:
        # The only genuinely unusable value: a server that supports no revision
        # can answer nothing, and every version lookup downstream would be an
        # index into an empty tuple. Caught here, once, at construction —
        # rather than as an ``IndexError`` out of a view on the first request.
        raise ImproperlyConfigured(
            "REST_FRAMEWORK_MCP['PROTOCOL_VERSIONS'] is empty, so this server supports "
            "no MCP revision and cannot answer any request. List at least one."
        )

    return MCPConfig(
        protocol_versions=resolved_versions,
        require_protocol_version_header=bool(
            require_protocol_version_header
            if require_protocol_version_header is not None
            else get_setting("REQUIRE_PROTOCOL_VERSION_HEADER")
        ),
        sessions_enabled=bool(
            sessions_enabled if sessions_enabled is not None else get_setting("SESSIONS_ENABLED")
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
        max_result_bytes=_nullable_int(_bound(max_result_bytes, "MAX_RESULT_BYTES")),
        max_page_size=_nullable_int(_bound(max_page_size, "MAX_PAGE_SIZE")),
        dispatch_timeout=_nullable_float(_bound(dispatch_timeout, "DISPATCH_TIMEOUT")),
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
        require_tool_descriptions=bool(
            require_tool_descriptions
            if require_tool_descriptions is not None
            else get_setting("REQUIRE_TOOL_DESCRIPTIONS")
        ),
        require_list_pagination=bool(
            require_list_pagination
            if require_list_pagination is not None
            else get_setting("REQUIRE_LIST_PAGINATION")
        ),
        max_progress_notifications=int(
            max_progress_notifications
            if max_progress_notifications is not None
            else get_setting("MAX_PROGRESS_NOTIFICATIONS")
        ),
        catalog_cache_ttl_ms=int(
            catalog_cache_ttl_ms
            if catalog_cache_ttl_ms is not None
            else get_setting("CATALOG_CACHE_TTL_MS")
        ),
        resource_cache_ttl_ms=int(
            resource_cache_ttl_ms
            if resource_cache_ttl_ms is not None
            else get_setting("RESOURCE_CACHE_TTL_MS")
        ),
        # ⚠ ``None`` is meaningful for both — "no expiry" and "send no poll
        # hint" — so neither can use the ``x if x is not None else setting``
        # shape the scalars above use: passing ``None`` explicitly would be
        # indistinguishable from not passing it, and would silently pick up the
        # setting instead. The kwarg wins only when it differs from the
        # setting's own default, which ``_resolve_optional`` decides.
        task_ttl_ms=_resolve_optional(task_ttl_ms, "TASK_TTL_MS"),
        task_poll_interval_ms=_resolve_optional(task_poll_interval_ms, "TASK_POLL_INTERVAL_MS"),
        subscription_max_seconds=_nullable_float(
            _bound(subscription_max_seconds, "SUBSCRIPTION_MAX_SECONDS")
        ),
        max_concurrent_subscriptions=_nullable_int(
            _bound(max_concurrent_subscriptions, "MAX_CONCURRENT_SUBSCRIPTIONS")
        ),
        input_request_ttl_seconds=int(
            input_request_ttl_seconds
            if input_request_ttl_seconds is not None
            else get_setting("INPUT_REQUEST_TTL_SECONDS")
        ),
        max_input_rounds=int(
            max_input_rounds if max_input_rounds is not None else get_setting("MAX_INPUT_ROUNDS")
        ),
    )


def _resolve_optional(value: int | None, setting: str) -> int | None:
    """Resolve a scalar whose ``None`` means something rather than "unset".

    The kwarg is taken when given; otherwise the setting is read, and a
    ``None`` there is passed through as the configured answer. An explicit
    ``0`` is kept as ``0`` — for a TTL that means "already expired", which is
    a strange thing to configure but not ours to override.
    """
    if value is not None:
        return int(value)
    configured: Any = get_setting(setting)
    return None if configured is None else int(configured)


__all__ = ["build_mcp_config"]
