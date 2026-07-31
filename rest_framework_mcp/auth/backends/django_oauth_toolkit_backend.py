from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest

from rest_framework_mcp.auth.audience import audience_matches
from rest_framework_mcp.auth.types.authorization_server_metadata import (
    AuthorizationServerMetadata,
)
from rest_framework_mcp.auth.types.protected_resource_metadata import ProtectedResourceMetadata
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.conf import get_setting

# The attribute an access token is expected to carry its RFC 8707 resource on.
# Named once so the startup check and the default getter cannot drift.
_RESOURCE_FIELD = "resource"


def _default_audience_getter(token: Any) -> str | None:
    """Read the bound resource off a DOT access token.

    Always ``None`` for DOT's stock model, which is why enforcement is
    opt-in and validated at construction.
    """
    return getattr(token, _RESOURCE_FIELD, None)


class DjangoOAuthToolkitBackend:
    """Resource-server adapter for ``django-oauth-toolkit`` (DOT).

    Validates the bearer token using DOT's own validators, then projects the
    result into a :class:`TokenInfo`. The ``oauth2_provider`` package is
    imported lazily inside ``authenticate`` because it's an optional extra
    (`pip install "djangorestframework-mcp-server[oauth]"`) — importing this module
    never blows up just because DOT is absent; the ``ImportError`` only fires
    when a request actually reaches authentication.

    Audience enforcement (RFC 8707) is controlled by ``enforce_audience``,
    **not** by ``resource_url``. ``resource_url`` is the identity this server
    publishes in its protected-resource metadata; enforcement is whether a
    token that doesn't carry that identity is rejected.

    Those two were once one knob, and coupling them made the bundled backend
    unusable. Enforcement needs the access token to record the resource it was
    issued for, and **DOT's stock ``AccessToken`` has no such field** — DOT
    implements no resource indicators, so ``getattr(token, "resource", None)``
    is always ``None``. Setting ``resource_url``, which RFC 9728 effectively
    requires, therefore rejected every token: a deployment had to choose between
    authenticating anybody and publishing valid metadata, with no configuration
    that did both.

    So enforcement now defaults off and, when switched on, is checked at
    construction rather than discovered per request. It works when the token
    really does carry the resource:

    - a swapped ``OAUTH2_PROVIDER["ACCESS_TOKEN_MODEL"]`` carrying a
      ``resource`` field (DOT supports substituting the model), or
    - an explicit ``audience_getter=`` reading it from wherever it lives — a
      JWT claim, an upstream gateway header, a related row.

    Turning it on without either raises :class:`ImproperlyConfigured` at
    startup, naming both ways out, instead of 401-ing every request.

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
        enforce_audience: bool | None = None,
        audience_getter: Callable[[Any], str | None] | None = None,
    ) -> None:
        server_info: dict[str, Any] = get_setting("SERVER_INFO")
        # RESOURCE_URL is preferred over SERVER_INFO["resource"] — the former is
        # also what audience enforcement reads, so one configuration mistake
        # can't produce metadata that disagrees with the check.
        #
        # ``or None`` on the argument too, not just the settings fallbacks: an
        # explicit ``resource_url=""`` used to be "not None", so it skipped the
        # fallbacks *and* enforced the audience against the empty string, which
        # no token can match. Empty means unset at every layer.
        self._resource_url: str | None = (
            (resource_url or None)
            if resource_url is not None
            else get_setting("RESOURCE_URL") or server_info.get("resource") or None
        )
        self._audience_getter: Callable[[Any], str | None] = (
            audience_getter if audience_getter is not None else _default_audience_getter
        )
        self._enforce_audience: bool = bool(
            enforce_audience if enforce_audience is not None else get_setting("ENFORCE_AUDIENCE")
        )
        if self._enforce_audience:
            self._check_enforcement_is_satisfiable(
                has_custom_getter=audience_getter is not None,
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

        token_audience: str | None = self._audience_getter(token)
        if self._enforce_audience and not audience_matches(token_audience, self._resource_url):
            return None

        scopes: tuple[str, ...] = tuple(token.scope.split()) if token.scope else ()
        return TokenInfo(
            user=token.user,
            scopes=scopes,
            audience=token_audience,
            raw=token,
        )

    def _check_enforcement_is_satisfiable(self, *, has_custom_getter: bool) -> None:
        """Refuse to start when audience enforcement could never succeed.

        Both failure modes here reject every token, so the only useful
        moment to raise is construction — which happens at startup, since
        ``MCPServer.__init__`` builds the backend. Per-request discovery
        was the original bug: an unsatisfiable check is indistinguishable
        from a bad credential once it reaches the client as a 401.

        DOT is imported here rather than at module scope so this module
        stays importable without the ``[oauth]`` extra; when DOT is
        genuinely absent the token-model check is skipped and
        :meth:`authenticate` raises its own ``ImportError`` instead.
        """
        if self._resource_url is None:
            raise ImproperlyConfigured(
                "DjangoOAuthToolkitBackend: audience enforcement is on but no resource "
                "URL is configured, so there is nothing for a token's resource to match "
                "and every request would be rejected. Set resource_url= (or "
                "REST_FRAMEWORK_MCP['RESOURCE_URL']), or turn off "
                "REST_FRAMEWORK_MCP['ENFORCE_AUDIENCE']."
            )
        if has_custom_getter:
            return
        try:
            from oauth2_provider.models import (  # type: ignore[import-not-found]
                get_access_token_model,
            )
        except ImportError:  # pragma: no cover - exercised by smoke job w/o DOT
            return
        token_model: Any = get_access_token_model()
        if any(field.name == _RESOURCE_FIELD for field in token_model._meta.get_fields()):
            return
        raise ImproperlyConfigured(
            "DjangoOAuthToolkitBackend: audience enforcement is on, but "
            f"{token_model.__name__} has no {_RESOURCE_FIELD!r} field — django-oauth-toolkit "
            "implements no RFC 8707 resource indicators, so no token it issues records the "
            "resource it was issued for and every request would be rejected. Either supply "
            "audience_getter= to read the audience from wherever it actually lives (a JWT "
            "claim, a gateway header), swap OAUTH2_PROVIDER['ACCESS_TOKEN_MODEL'] for one "
            f"carrying a {_RESOURCE_FIELD!r} field, or turn off "
            "REST_FRAMEWORK_MCP['ENFORCE_AUDIENCE'] and keep resource_url= for metadata only."
        )

    def protected_resource_metadata(self) -> ProtectedResourceMetadata:
        # RFC 9728 makes ``resource`` REQUIRED, so an unconfigured server
        # publishes an empty one either way. Say so in the payload rather than
        # letting a client puzzle over a blank required field.
        return ProtectedResourceMetadata(
            resource=self._resource_url or "",
            authorization_servers=list(self._authorization_servers),
            bearer_methods_supported=["header"],
            scopes_supported=list(self._scopes_supported),
            resource_documentation=self._resource_documentation,
            warning=(
                None
                if self._resource_url
                else (
                    "No resource URL is configured, so `resource` is empty. Set "
                    "REST_FRAMEWORK_MCP['RESOURCE_URL'] or MCPServer(resource_url=...)."
                )
            ),
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

        ``client_id_metadata_document_supported`` is read from DOT rather than
        asserted here — see :func:`_cimd_enabled`.
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
            client_id_metadata_document_supported=_cimd_enabled(),
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


def _cimd_enabled() -> bool:
    """Whether DOT is configured to accept URL ``client_id``s.

    **Feature-detected, not version-gated.** Client ID Metadata Document
    support landed in ``django-oauth-toolkit`` 3.4.0 as the opt-in
    ``CIMD_ENABLED`` setting; older releases have no such key. Reading it with
    a default keeps the ``[oauth]`` extra's floor where it is — CIMD is opt-in
    even on 3.4, so raising the floor would force a migration on every existing
    installation to advertise something most of them have not switched on.

    Sourced from DOT rather than from a setting of our own so the two can never
    disagree: DOT's own RFC 8414 endpoint advertises the same value, and a
    project that runs both would otherwise get contradictory metadata from the
    same authorization server.
    """
    try:
        from oauth2_provider.settings import (  # type: ignore[import-not-found]
            oauth2_settings,
        )
    except ImportError:  # pragma: no cover - exercised by the smoke job w/o DOT
        return False
    return bool(getattr(oauth2_settings, "CIMD_ENABLED", False))


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
