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

    Whitelists the fields the DCR view actually forwards to DOT's
    ``Application`` model. Other RFC 7591 fields are silently ignored
    by the serializer — DOT doesn't model them.
    """

    redirect_uris: list[str] = field(default_factory=list)
    client_name: str = ""
    scope: str = ""
    client_type: str = ""
    authorization_grant_type: str = ""
