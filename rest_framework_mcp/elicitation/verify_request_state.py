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

    Four things have to hold, and the spec names all four — signature, expiry,
    principal, originating request. They are checked in that order because it is
    the cheap-first order and because a forged token should never reach the
    comparisons that could leak anything by timing.

    ⚠ **Every failure is the same failure.** A tampered signature, a token from
    yesterday, someone else's token and a token minted for a different tool all
    return ``None``, and the caller treats ``None`` identically to "the client
    sent no state at all" — the exchange simply starts over and the user is
    asked again. That is a deliberately unhelpful answer: distinguishing them
    would turn the endpoint into an oracle for which principals and which calls
    have live state, and the honest client cannot use the distinction anyway
    because it is forbidden from looking inside the token in the first place.

    The one thing this does *not* provide is single-use. The spec is explicit
    that principal, expiry and request binding *"bound the replay window and
    prevent cross-user and cross-request reuse, but do not by themselves
    guarantee single-use"*. Within the TTL a client may redeem the same state
    twice — which for this package means re-running the same tool call with the
    same confirmed answers, i.e. exactly what an ordinary retry of any tool call
    does. A service for which that is not acceptable needs its own idempotency
    key, and would need one with or without elicitation.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        payload: Any = signing.loads(raw, salt=REQUEST_STATE_SALT, max_age=max_age)
    except signing.BadSignature:
        # ``SignatureExpired`` is a subclass, so this arm covers both the
        # tampered and the merely stale token — see the docstring on why they
        # are not told apart.
        return None

    state: RequestState | None = RequestState.from_payload(payload)
    if state is None:
        return None
    if state.principal != principal or state.fingerprint != fingerprint:
        return None
    return state


__all__ = ["verify_request_state"]
