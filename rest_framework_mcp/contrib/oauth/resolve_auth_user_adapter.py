from __future__ import annotations

from django.utils.module_loading import import_string

from rest_framework_mcp.conf import get_setting
from rest_framework_mcp.contrib.oauth.adapters.types.auth_user_adapter import AuthUserAdapter


def resolve_auth_user_adapter() -> AuthUserAdapter | None:
    """Resolve the configured :class:`AuthUserAdapter` instance, or ``None`` if unset.

    ``REST_FRAMEWORK_MCP['AUTH_USER_ADAPTER']`` is a dotted path to an
    adapter class (mirrors the existing ``AUTH_BACKEND`` /
    ``SESSION_STORE`` settings). ``None`` (the default) means "no
    adapter; DOT's own dispatch decides the user" — typically a
    session-based login redirect.

    Adapters MUST be safe to instantiate without arguments per the
    Protocol contract; settings-driven configuration belongs inside the
    adapter's own module.
    """
    dotted: str | None = get_setting("AUTH_USER_ADAPTER")
    if dotted is None:
        return None
    cls = import_string(dotted)
    instance: AuthUserAdapter = cls()
    return instance


__all__ = ["resolve_auth_user_adapter"]
