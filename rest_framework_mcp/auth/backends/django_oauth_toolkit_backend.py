from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.audience import audience_matches
from rest_framework_mcp.auth.token_info import TokenInfo
from rest_framework_mcp.conf import get_setting


class DjangoOAuthToolkitBackend:
    """Resource-server adapter for ``django-oauth-toolkit`` (DOT).

    Validates the bearer token using DOT's own validators, then projects the
    result into a :class:`TokenInfo`. The ``oauth2_provider`` package is
    imported lazily inside ``authenticate`` because it's an optional extra
    (`pip install "djangorestframework-mcp-server[oauth]"`) — importing this module
    never blows up just because DOT is absent; the ``ImportError`` only fires
    when a request actually reaches authentication.

    Audience enforcement (RFC 8707): when ``REST_FRAMEWORK_MCP["RESOURCE_URL"]``
    is configured, the token's ``resource`` claim must match exactly. Tokens
    without a bound resource are rejected. Setting ``RESOURCE_URL`` to ``None``
    (the default) disables enforcement — appropriate for dev or for setups
    where audience binding is performed by an upstream gateway.
    """

    def authenticate(self, request: HttpRequest) -> TokenInfo | None:
        try:
            # Lazy import: oauth2_provider is an optional extra. Top-level
            # import would force every consumer (including the no-extras
            # smoke job) to install DOT.
            from oauth2_provider.oauth2_validators import OAuth2Validator
        except ImportError as exc:  # pragma: no cover - exercised by smoke job with DOT absent
            raise ImportError(
                "DjangoOAuthToolkitBackend requires `django-oauth-toolkit`. "
                'Install it via `pip install "djangorestframework-mcp-server[oauth]"` '
                "or configure a different REST_FRAMEWORK_MCP['AUTH_BACKEND']."
            ) from exc

        header: str = request.META.get("HTTP_AUTHORIZATION", "")
        if not header.lower().startswith("bearer "):
            return None
        access_token: str = header.split(" ", 1)[1].strip()
        if not access_token:
            return None

        validator = OAuth2Validator()
        try:
            token = validator._load_access_token(access_token)  # type: ignore[attr-defined]
        except Exception:
            return None
        if token is None or not token.is_valid():
            return None

        token_audience: str | None = getattr(token, "resource", None)
        expected: str | None = get_setting("RESOURCE_URL")
        if not audience_matches(token_audience, expected):
            return None

        scopes: tuple[str, ...] = tuple(token.scope.split()) if token.scope else ()
        return TokenInfo(
            user=token.user,
            scopes=scopes,
            audience=token_audience,
            raw=token,
        )

    def protected_resource_metadata(self) -> dict[str, Any]:
        server_info: dict[str, Any] = get_setting("SERVER_INFO")
        # Prefer the explicit RESOURCE_URL setting over SERVER_INFO["resource"]
        # — the former is also what audience enforcement reads from, so a
        # single configuration mistake can't produce inconsistent metadata.
        resource: str | None = get_setting("RESOURCE_URL") or server_info.get("resource") or None
        payload: dict[str, Any] = {
            "resource": resource or "",
            "authorization_servers": server_info.get("authorization_servers", []),
            "bearer_methods_supported": ["header"],
            "scopes_supported": server_info.get("scopes_supported", []),
        }
        if "documentation" in server_info:
            payload["resource_documentation"] = server_info["documentation"]
        return payload

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        server_info: dict[str, Any] = get_setting("SERVER_INFO")
        parts: list[str] = ['Bearer realm="mcp"']
        metadata_url: str | None = server_info.get("resource_metadata_url")
        if metadata_url:
            parts.append(f'resource_metadata="{metadata_url}"')
        if error:
            parts.append(f'error="{error}"')
        if scopes:
            parts.append(f'scope="{" ".join(scopes)}"')
        return ", ".join(parts)


__all__ = ["DjangoOAuthToolkitBackend"]
