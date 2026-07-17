from __future__ import annotations

from django.conf import settings as django_settings
from django.core.exceptions import ImproperlyConfigured

# Setting name → how to say the same thing now. These named a *collaborator* by
# dotted path, which only ever existed because ``settings.py`` cannot hold a
# live object (an import cycle or ``AppRegistryNotReady``). ``urls.py`` can, so
# the object is passed directly and the string indirection is gone.
_REMOVED_SETTINGS: dict[str, str] = {
    "AUTH_BACKEND": "pass auth_backend=YourAuthBackend() to MCPServer(...)",
    "SESSION_STORE": "pass session_store=YourSessionStore() to MCPServer(...)",
}


def check_removed_settings() -> None:
    """Reject removed ``REST_FRAMEWORK_MCP`` keys instead of ignoring them.

    Called from :meth:`MCPServer.__init__`, so a stale settings dict fails when
    the URL conf is imported rather than on some later request.

    A removed key left in place would otherwise be **silently dropped** — the
    server would quietly fall back to its default auth backend, which for
    ``AUTH_BACKEND`` means a project that thought it had configured
    authentication has not. Failing loudly is the whole point; a warning would
    scroll past in a deploy log.
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
