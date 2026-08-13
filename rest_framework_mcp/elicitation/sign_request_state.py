from __future__ import annotations

from django.core import signing

from rest_framework_mcp.constants import REQUEST_STATE_SALT
from rest_framework_mcp.elicitation.types.request_state import RequestState


def sign_request_state(state: RequestState) -> str:
    """Serialise and HMAC a ``RequestState`` into the wire ``requestState``.

    ``django.core.signing`` rather than a hand-rolled HMAC: it carries the
    timestamp
    ``verify_request_state``
    checks the age against, it honours ``SECRET_KEY_FALLBACKS`` so a key rotation
    does not invalidate every form a user is part-way through answering, and its
    compression keeps a multi-round token small.

    **Signed, not encrypted** — anyone holding the token can base64-decode it.
    Acceptable given the contents: the caller's own principal id, a digest of
    the caller's own request, and the answers the user at that client just
    typed. It is also why form mode is the only mode built here: the spec
    reserves forms for non-sensitive values and gives URL mode everything else,
    and a secret in a form field would end up in a token that transits proxies
    and logs.
    """
    return signing.dumps(state.to_payload(), salt=REQUEST_STATE_SALT, compress=True)


__all__ = ["sign_request_state"]
