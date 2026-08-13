from __future__ import annotations

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

# Setting name to how to say the same thing now. Each named a collaborator by
# dotted path, an indirection that existed only because ``settings.py`` cannot
# hold a live object. ``urls.py`` can, so the object is passed directly.
_REMOVED_SETTINGS: dict[str, str] = {
    "AUTH_BACKEND": "pass auth_backend=YourAuthBackend() to MCPServer(...)",
    "SESSION_STORE": "pass session_store=YourSessionStore() to MCPServer(...)",
    "AUTH_USER_ADAPTER": ("pass auth_user_adapter=YourAdapter() to build_oauth_urlpatterns(...)"),
}


def check_removed_settings() -> None:
    """Reject removed ``REST_FRAMEWORK_MCP`` keys instead of ignoring them.

    Called from ``MCPServer.__init__``, so a stale settings dict fails when
    the URL conf is imported rather than on some later request. A removed key
    left in place would otherwise be silently dropped, and for ``AUTH_BACKEND``
    that means a project which believes it configured authentication has not —
    so this raises rather than warning into a deploy log.
    """
    user_settings: dict[str, object] = getattr(django_settings, "REST_FRAMEWORK_MCP", {}) or {}
    present: list[str] = [name for name in _REMOVED_SETTINGS if name in user_settings]
    if not present:
        return
    details: str = "\n".join(
        f"  REST_FRAMEWORK_MCP[{name!r}] — {_REMOVED_SETTINGS[name]}" for name in present
    )
    raise ImproperlyConfigured(
        "These REST_FRAMEWORK_MCP settings were removed in 0.12.0; the "
        "collaborators they named are now constructor arguments. They would be "
        "silently ignored if left in place, so they are rejected:\n"
        f"{details}"
    )


__all__ = ["check_removed_settings"]
