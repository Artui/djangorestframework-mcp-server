from __future__ import annotations

from typing import Any

from django.conf import settings as django_settings

# Every key is documented for consumers in ``docs/reference/settings.md`` —
# defaults, semantics and the reasoning behind each one. Comments here carry
# only what that page cannot say.
DEFAULTS: dict[str, Any] = {
    # Both eras in one list, most-preferred first: a revision belongs to
    # exactly one era, and which one is decided by ``MODERN_PROTOCOL_VERSIONS``.
    "PROTOCOL_VERSIONS": ["2026-07-28", "2025-11-25", "2025-06-18"],
    "REQUIRE_PROTOCOL_VERSION_HEADER": True,
    # Legacy era only; the modern era is already sessionless.
    "SESSIONS_ENABLED": True,
    "SESSION_TTL_SECONDS": 60 * 60 * 24,
    "SESSION_MAX_AGE_SECONDS": 60 * 60 * 24 * 7,
    "INCLUDE_STRUCTURED_CONTENT": True,
    "INCLUDE_OUTPUT_SCHEMA": True,
    "CATALOG_CACHE_TTL_MS": 60_000,
    "RESOURCE_CACHE_TTL_MS": 0,
    "TASK_TTL_MS": 86_400_000,
    "TASK_POLL_INTERVAL_MS": 5_000,
    "SUBSCRIPTION_MAX_SECONDS": 3600,
    "MAX_CONCURRENT_SUBSCRIPTIONS": 100,
    # The same two bounds for the legacy era's GET session stream, which parks
    # an ASGI task on exactly the same terms.
    "SSE_STREAM_MAX_SECONDS": 3600,
    "MAX_CONCURRENT_SSE_STREAMS": 100,
    # Expiry is one of the three replay defences the spec asks for; the other
    # two — principal and originating request — are enforced unconditionally
    # rather than configured.
    "INPUT_REQUEST_TTL_SECONDS": 600,
    "MAX_INPUT_ROUNDS": 5,
    "ALLOWED_ORIGINS": [],
    "DEFAULT_OUTPUT_FORMAT": "json",
    # Recognised keys: ``name``, ``version``, ``title``, ``description``,
    # ``websiteUrl``, ``icons``. All but ``description`` are also ``MCPServer``
    # constructor kwargs; the constructor's ``description=`` means the
    # ``initialize`` ``instructions`` string instead.
    "SERVER_INFO": {"name": "djangorestframework-mcp-server"},
    "MAX_PROGRESS_NOTIFICATIONS": 1_000,
    "MAX_REQUEST_BYTES": 1_048_576,
    # Measured on the encoded wire payload: a tool result carries the payload
    # twice (``structuredContent`` plus the text mirror), so a ceiling counting
    # one copy would be wrong by 2x.
    "MAX_RESULT_BYTES": 5_242_880,
    # Matches the page size dispatch applies when ``limit`` is absent, so an
    # unconfigured deployment never advertises a larger page than it serves.
    "MAX_PAGE_SIZE": 100,
    # Async transport only, and it does not reclaim the worker — pair it with a
    # database statement timeout.
    "DISPATCH_TIMEOUT": 60.0,
    # Only the default for ``MCPServer(resource_url=...)``: RFC 8707 binds a
    # token to *a* resource, so two servers sharing one URL means a token
    # minted for one passes the audience check at the other.
    "RESOURCE_URL": None,
    # Off by default because DOT's stock ``AccessToken`` records no resource,
    # so enforcement would reject every token. Needs a swapped
    # ``ACCESS_TOKEN_MODEL`` or an explicit ``audience_getter=``.
    "ENFORCE_AUDIENCE": False,
    "PAGE_SIZE": 100,
    # Off by default: the arguments dict can carry PII or secrets.
    "INCLUDE_VALIDATION_VALUE": False,
    # ``ServiceValidationError`` is never recorded — it is client input
    # failure, not a server fault.
    "RECORD_SERVICE_EXCEPTIONS": False,
    "DCR_ENABLED": False,
    "DCR_INITIAL_ACCESS_TOKEN": None,
    "SIMPLEJWT_ACCESS_COOKIE": "access",
    "FILTER_LISTINGS_BY_PERMISSIONS": False,
    "REQUIRE_TOOL_PERMISSIONS": True,
    "REQUIRE_TOOL_DESCRIPTIONS": False,
    "REQUIRE_LIST_PAGINATION": False,
}


def get_setting(name: str) -> Any:
    """Return a single setting from ``REST_FRAMEWORK_MCP``, falling back to ``DEFAULTS``.

    Raises ``KeyError`` for unknown setting names so typos surface immediately.
    """
    if name not in DEFAULTS:
        raise KeyError(f"Unknown REST_FRAMEWORK_MCP setting: {name!r}")
    user_settings: dict[str, Any] = getattr(django_settings, "REST_FRAMEWORK_MCP", {}) or {}
    if name in user_settings:
        return user_settings[name]
    return DEFAULTS[name]
