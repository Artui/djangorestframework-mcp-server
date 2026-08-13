from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DynamicClientRegistrationRequest:
    """RFC 7591 dynamic client registration request payload.

    Mutable, so
    [`DynamicClientRegistrationSerializer`][rest_framework_mcp.contrib.oauth.dcr_serializer.DynamicClientRegistrationSerializer]
    can normalise in place; frozen would force a second instance just to change a
    defaulted field.

    Two vocabularies land here side by side. ``token_endpoint_auth_method``,
    ``grant_types`` and ``response_types`` are the RFC 7591 §2 fields an
    interoperable client sends; ``client_type`` and
    ``authorization_grant_type`` are DOT's non-standard equivalents, kept as an
    escape hatch. The serializer's ``validate`` reconciles them, so everything
    downstream reads a consistent set.

    ``response_types`` has no DOT counterpart: RFC 7591 §2.1 makes it a
    function of the grant, so it is derived rather than stored and an explicit
    value contradicting the grant is rejected.
    ``id_token_signed_response_alg`` maps to ``Application.algorithm``, which
    decides whether an ID token can be signed at all; only the viewset can
    resolve it, because usability depends on server configuration (an RSA key)
    and client type (HS256 signs with a secret a public client does not have).
    ``application_type`` is validated and echoed but has no DOT counterpart —
    the MCP spec makes *sending* it a client MUST, so dropping it silently
    would leave a client that declared ``native`` unable to tell whether it had
    been heard.

    RFC 7591 fields the server does not understand (``contacts``,
    ``logo_uri``, ``jwks``, …) are ignored, as §2 requires: DOT has nowhere to
    put them, and echoing metadata the authorization server will not honour is
    worse than dropping it.
    """

    redirect_uris: list[str] = field(default_factory=list)
    client_name: str = ""
    scope: str = ""
    token_endpoint_auth_method: str = ""
    grant_types: list[str] = field(default_factory=list)
    response_types: list[str] = field(default_factory=list)
    id_token_signed_response_alg: str = ""
    application_type: str = ""
    client_type: str = ""
    authorization_grant_type: str = ""
