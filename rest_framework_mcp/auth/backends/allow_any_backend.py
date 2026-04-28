from __future__ import annotations

from typing import Any

from django.contrib.auth.models import AnonymousUser
from django.http import HttpRequest

from rest_framework_mcp.auth.token_info import TokenInfo


class AllowAnyBackend:
    """Development / test backend that authenticates every request as anonymous.

    DO NOT use in production. The ``protected_resource_metadata`` payload is
    intentionally minimal so misconfiguration is loud rather than silent.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        user: Any = getattr(request, "user", None) or AnonymousUser()
        return TokenInfo(user=user, scopes=(), audience=None, raw=None)

    def protected_resource_metadata(self) -> dict[str, Any]:
        return {
            "resource": "(unset)",
            "bearer_methods_supported": ["header"],
            "scopes_supported": [],
            "_warning": "AllowAnyBackend is for development only.",
        }

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        parts: list[str] = ['Bearer realm="mcp"']
        if error:
            parts.append(f'error="{error}"')
        if scopes:
            parts.append(f'scope="{" ".join(scopes)}"')
        return ", ".join(parts)


__all__ = ["AllowAnyBackend"]
