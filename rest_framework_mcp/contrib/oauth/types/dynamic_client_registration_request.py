from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DynamicClientRegistrationRequest:
    """RFC 7591 dynamic client registration request payload.

    Mutable dataclass so :class:`DynamicClientRegistrationSerializer`
    (a ``DataclassSerializer`` over this type) can apply defaults via
    ``setdefault``-style normalisation if needed. Frozen would force
    consumers to build a second instance just to change a defaulted
    field, which is awkward for a request-shape type.

    Two vocabularies land here side by side:

    - ``token_endpoint_auth_method``, ``grant_types`` and
      ``response_types`` are the RFC 7591 §2 fields an interoperable
      client actually sends.
    - ``client_type`` and ``authorization_grant_type`` are DOT's
      non-standard equivalents, kept as an escape hatch for callers that
      already speak DOT.

    The serializer's ``validate`` reconciles the two, so everything
    downstream reads a consistent set. ``response_types`` has no DOT
    counterpart at all — RFC 7591 §2.1 makes it a function of the grant,
    so it is derived rather than stored, and an explicit value that
    contradicts the grant is rejected.

    ``id_token_signed_response_alg`` maps to DOT's ``Application.algorithm``,
    which decides whether an ID token can be signed at all. Only the
    viewset can resolve it, because whether an algorithm is *usable*
    depends on server configuration (an RSA key) and on the client type
    (HS256 signs with the client secret, which a public client does not
    have) — neither of which is visible from the payload alone.

    ``application_type`` is the one field kept without a DOT counterpart. The
    MCP spec makes sending it a client **MUST**, because an OIDC authorization
    server derives redirect-URI constraints from it — so dropping it silently
    would leave a client that carefully declared ``native`` unable to tell
    whether it had been heard. It is validated and echoed back; see the
    serializer for why it is not enforced.

    RFC 7591 fields the server doesn't understand (``contacts``,
    ``logo_uri``, ``jwks``, …) are ignored, which §2 requires — DOT has
    nowhere to put them, and echoing metadata the authorization server
    won't honour is worse than dropping it.
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
