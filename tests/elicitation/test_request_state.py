"""``requestState`` — the one value in this protocol that leaves the server,
passes through the client, and comes back trusted.

The spec is blunt about it: *"servers MUST treat ``requestState`` as an
attacker-controlled input"*. Everything below is a way of not trusting it.
"""

from __future__ import annotations

from typing import Any

import pytest
from django.core import signing

from rest_framework_mcp.constants import REQUEST_STATE_SALT
from rest_framework_mcp.elicitation.fingerprint_request import fingerprint_request
from rest_framework_mcp.elicitation.sign_request_state import sign_request_state
from rest_framework_mcp.elicitation.types.request_state import RequestState
from rest_framework_mcp.elicitation.verify_request_state import verify_request_state

PRINCIPAL: str = "7"
FINGERPRINT: str = "a" * 64


def _state(**overrides: Any) -> RequestState:
    fields: dict[str, Any] = {
        "principal": PRINCIPAL,
        "fingerprint": FINGERPRINT,
        "answers": {"confirmed": True},
        "round": 1,
    }
    return RequestState(**{**fields, **overrides})


def _verify(raw: Any, **overrides: Any) -> RequestState | None:
    kwargs: dict[str, Any] = {
        "principal": PRINCIPAL,
        "fingerprint": FINGERPRINT,
        "max_age": 600,
    }
    return verify_request_state(raw, **{**kwargs, **overrides})


# ---------- the happy path ----------


def test_a_state_we_minted_comes_back_intact() -> None:
    assert _verify(sign_request_state(_state())) == _state()


def test_the_token_is_opaque_to_the_client_but_not_secret() -> None:
    """⚠ Signed, not encrypted — worth pinning so nobody later puts a secret in
    an answer. What is inside is the caller's own identity, a digest of the
    caller's own request, and what the user at that client just typed."""
    payload = signing.loads(sign_request_state(_state()), salt=REQUEST_STATE_SALT)
    assert payload == {"p": PRINCIPAL, "f": FINGERPRINT, "a": {"confirmed": True}, "r": 1}


# ---------- the four rejections ----------


def test_a_tampered_token_is_rejected() -> None:
    signed = sign_request_state(_state())
    assert _verify(signed[:-1] + ("x" if signed[-1] != "x" else "y")) is None


def test_a_token_forged_with_another_salt_is_rejected() -> None:
    """The salt is why a signed value from elsewhere in the project — a password
    reset link, anything else calling ``django.core.signing`` — cannot be
    presented here."""
    forged = signing.dumps(_state().to_payload(), salt="something.else")
    assert _verify(forged) is None


def test_an_expired_token_is_rejected() -> None:
    assert _verify(sign_request_state(_state()), max_age=-1) is None


def test_another_principals_token_is_rejected() -> None:
    """The replay defence that matters most: a token that leaked through a log
    or a shared proxy is worthless to anyone else."""
    assert _verify(sign_request_state(_state(principal="99"))) is None


def test_a_token_minted_for_a_different_call_is_rejected() -> None:
    """⚠ Without this, a confirmation the user gave for a harmless call could be
    replayed onto a destructive one — same principal, still unexpired, and
    carrying an ``answers`` map the second call would read as consent."""
    assert _verify(sign_request_state(_state(fingerprint="b" * 64))) is None


# ---------- shapes that are not tokens ----------


@pytest.mark.parametrize("raw", [None, "", 42, {"p": "7"}, []])
def test_anything_that_is_not_a_non_empty_string_is_not_a_token(raw: Any) -> None:
    assert _verify(raw) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-dict",
        {"p": 7, "f": FINGERPRINT, "a": {}, "r": 0},
        {"p": PRINCIPAL, "f": None, "a": {}, "r": 0},
        {"p": PRINCIPAL, "f": FINGERPRINT, "a": "nope", "r": 0},
        {"p": PRINCIPAL, "f": FINGERPRINT, "a": {}, "r": "one"},
        {},
    ],
)
def test_a_validly_signed_payload_of_the_wrong_shape_is_rejected(payload: Any) -> None:
    """⚠ A signature proves the payload came from this server — not from *this
    release* of it. A token minted by an older shape verifies and would
    otherwise be unpacked into nonsense, so the shape is checked too, and a
    stale one costs the client one extra round rather than a compatibility rule
    anyone has to remember."""
    assert _verify(signing.dumps(payload, salt=REQUEST_STATE_SALT)) is None


# ---------- the fingerprint ----------


def test_the_fingerprint_ignores_key_order() -> None:
    """JSON object order carries no meaning, so two encodings of the same call
    must not disagree — otherwise a client that reserialises its arguments
    invalidates its own token."""
    a = fingerprint_request("tools/call", {"name": "t", "arguments": {"x": 1, "y": 2}})
    b = fingerprint_request("tools/call", {"name": "t", "arguments": {"y": 2, "x": 1}})
    assert a == b


def test_the_fingerprint_separates_calls() -> None:
    assert fingerprint_request("tools/call", {"name": "a"}) != fingerprint_request(
        "tools/call", {"name": "b"}
    )
    assert fingerprint_request("tools/call", {"name": "a"}) != fingerprint_request(
        "prompts/get", {"name": "a"}
    )


def test_the_fingerprint_survives_a_value_json_cannot_encode() -> None:
    """A mismatch would be an annoyance; a ``TypeError`` out of a handler would
    be a 500 on an ordinary tool call."""
    from decimal import Decimal

    assert fingerprint_request("tools/call", {"amount": Decimal("1.5")})
