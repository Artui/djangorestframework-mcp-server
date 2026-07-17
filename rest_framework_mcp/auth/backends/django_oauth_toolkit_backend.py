from __future__ import annotations

from typing import Any

from django.http import HttpRequest

from rest_framework_mcp.auth.audience import audience_matches
from rest_framework_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from rest_framework_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.conf import get_setting


class DjangoOAuthToolkitBackend:
    """Resource-server adapter for ``django-oauth-toolkit`` (DOT).

    Validates the bearer token using DOT's own validators, then projects the
    result into a :class:`TokenInfo`. The ``oauth2_provider`` package is
    imported lazily inside ``authenticate`` because it's an optional extra
    (`pip install "djangorestframework-mcp-server[oauth]"`) — importing this module
    never blows up just because DOT is absent; the ``ImportError`` only fires
    when a request actually reaches authentication.

    Audience enforcement (RFC 8707): when ``resource_url`` is set, the token's
    ``resource`` claim must match it exactly, and tokens without a bound
    resource are rejected. ``None`` (the default) disables enforcement —
    appropriate for dev, or where audience binding happens at an upstream
    gateway.

    **One resource URL per server.** RFC 8707 binds a token to *a* resource, so
    each server needs its own canonical URL — that binding is precisely what
    stops a token issued for one resource being replayed against another. Two
    servers sharing a single URL defeat it: a token minted for ``/public/mcp``
    would satisfy ``/internal/mcp``. Hence ``resource_url`` is per-backend and
    the ``RESOURCE_URL`` setting is only its default::

        MCPServer(
            name="internal",
            resource_url="https://example.com/internal/mcp/",
        )

    Every value is resolved **once, here** — the settings reads are defaults for
    the arguments, not per-request lookups — so two backends in one process can
    genuinely differ.
    """

    def __init__(
        self,
        *,
        resource_url: str | None = None,
        authorization_servers: list[str] | None = None,
        scopes_supported: list[str] | None = None,
        resource_documentation: str | None = None,
        resource_metadata_url: str | None = None,
    ) -> None:
        server_info: dict[str, Any] = get_setting("SERVER_INFO")
        # RESOURCE_URL is preferred over SERVER_INFO["resource"] — the former is
        # also what audience enforcement reads, so one configuration mistake
        # can't produce metadata that disagrees with the check.
        self._resource_url: str | None = (
            resource_url
            if resource_url is not None
            else get_setting("RESOURCE_URL") or server_info.get("resource") or None
        )
        self._authorization_servers: list[str] = list(
            authorization_servers
            if authorization_servers is not None
            else server_info.get("authorization_servers", [])
        )
        self._scopes_supported: list[str] = list(
            scopes_supported
            if scopes_supported is not None
            else server_info.get("scopes_supported", [])
        )
        self._resource_documentation: str | None = (
            resource_documentation
            if resource_documentation is not None
            else server_info.get("documentation")
        )
        # Derived from this server's own resource URL when not given outright:
        # the PRM endpoint mounts under the server's prefix, so a server at
        # ``https://x/internal/mcp/`` serves it at
        # ``https://x/internal/mcp/.well-known/oauth-protected-resource``.
        # Taking it from the global SERVER_INFO instead would point every
        # server's 401 challenge at one server's metadata.
        self._resource_metadata_url: str | None = (
            resource_metadata_url
            if resource_metadata_url is not None
            else server_info.get("resource_metadata_url")
            or _derive_metadata_url(self._resource_url)
        )

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
                "or pass a different auth_backend= to MCPServer(...)."
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
        if not audience_matches(token_audience, self._resource_url):
            return None

        scopes: tuple[str, ...] = tuple(token.scope.split()) if token.scope else ()
        return TokenInfo(
            user=token.user,
            scopes=scopes,
            audience=token_audience,
            raw=token,
        )

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        return ProtectedResourceMetadata(
            resource=self._resource_url or "",
            authorization_servers=list(self._authorization_servers),
            bearer_methods_supported=["header"],
            scopes_supported=list(self._scopes_supported),
            resource_documentation=self._resource_documentation,
        )

    def authorization_server_metadata(self) -> AuthorizationServerMetadata:
        """Return the RFC 8414 metadata payload for the DOT-hosted authorization server.

        Pulls the issuer, endpoint URLs, supported grant / response types,
        and scopes from this backend's ``authorization_servers`` (first
        entry) plus the ``contrib.oauth`` mount convention that endpoints
        live at ``/oauth/authorize/``, ``/oauth/token/``, ``/oauth/register/``.

        Missing values fall through as empty strings / lists so the wire
        shape is always valid JSON; consumers are expected to configure
        ``authorization_servers`` for production deployments.
        """
        as_list: list[str] = self._authorization_servers
        issuer: str = as_list[0] if as_list else ""
        # ``base`` is the issuer with no trailing slash so we can build
        # endpoint URLs by string concatenation without doubling slashes.
        base: str = issuer.rstrip("/")
        return AuthorizationServerMetadata(
            issuer=issuer,
            authorization_endpoint=f"{base}/oauth/authorize/" if base else "",
            token_endpoint=f"{base}/oauth/token/" if base else "",
            registration_endpoint=f"{base}/oauth/register/" if base else "",
            scopes_supported=list(self._scopes_supported),
        )

    def www_authenticate_challenge(
        self, *, scopes: list[str] | None = None, error: str | None = None
    ) -> str:
        parts: list[str] = ['Bearer realm="mcp"']
        if self._resource_metadata_url:
            parts.append(f'resource_metadata="{self._resource_metadata_url}"')
        if error:
            parts.append(f'error="{error}"')
        if scopes:
            parts.append(f'scope="{" ".join(scopes)}"')
        return ", ".join(parts)


def _derive_metadata_url(resource_url: str | None) -> str | None:
    """Point a 401 challenge at *this* server's PRM endpoint.

    :class:`MCPServer` mounts the metadata view under its own prefix, so a
    server whose canonical URL is ``https://x/internal/mcp/`` serves it at
    ``https://x/internal/mcp/.well-known/oauth-protected-resource``. Deriving it
    keeps the pointer correct for every server without each one restating it.
    """
    if not resource_url:
        return None
    return f"{resource_url.rstrip('/')}/.well-known/oauth-protected-resource"


__all__ = ["DjangoOAuthToolkitBackend"]
