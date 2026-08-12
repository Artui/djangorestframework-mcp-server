from __future__ import annotations

from typing import Any

from django.core import signing

from rest_framework_mcp.constants import REQUEST_STATE_SALT
from rest_framework_mcp.elicitation.types.request_state import RequestState


def verify_request_state(
    raw: Any,
    *,
    principal: str,
    fingerprint: str,
    max_age: int,
) -> RequestState | None:
    """Unpack a client-presented ``requestState``, or ``None`` if it is not ours.

    Four things have to hold, all four named by the spec — signature, expiry,
    principal, originating request. Checked in that order: cheapest first, and a
    forged token never reaches the comparisons that could leak by timing.

    **Every failure is the same failure.** A tampered signature, a stale token,
    someone else's token and a token minted for a different tool all return
    ``None``, which the caller treats identically to "the client sent no state
    at all" — the exchange starts over and the user is asked again.
    Distinguishing them would turn the endpoint into an oracle for which
    principals and calls have live state, and an honest client cannot use the
    distinction anyway, being forbidden from looking inside the token.

    **Not single-use.** The spec is explicit that principal, expiry and request
    binding *"bound the replay window and prevent cross-user and cross-request
    reuse, but do not by themselves guarantee single-use"*. Within the TTL a
    client may redeem the same state twice — for this package, re-running the
    same tool call with the same confirmed answers, which is what an ordinary
    retry does. A service that cannot accept that needs its own idempotency key.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        payload: Any = signing.loads(raw, salt=REQUEST_STATE_SALT, max_age=max_age)
    except signing.BadSignature:
        # ``SignatureExpired`` is a subclass, so this arm covers both the
        # tampered and the merely stale token.
        return None

    state: RequestState | None = RequestState.from_payload(payload)
    if state is None:
        return None
    if state.principal != principal or state.fingerprint != fingerprint:
        return None
    return state


__all__ = ["verify_request_state"]
