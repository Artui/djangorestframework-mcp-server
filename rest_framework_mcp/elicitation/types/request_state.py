from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RequestState:
    """What the server needs back to carry on, and what it checks before it does.

    The spec calls ``requestState`` *"an opaque blob"* the client "must not
    interpret in any way" — so its contents are entirely ours to choose, and the
    choice is the security design. Three of these four fields exist only to be
    verified; only :attr:`answers` carries anything forward.

    ⚠ **It travels through the client, so it is attacker-controlled input.** The
    spec says so in as many words and requires integrity protection whenever the
    state influences authorization, resource access or business logic — which it
    does here, since :attr:`answers` becomes tool arguments. Signing is in
    :mod:`rest_framework_mcp.elicitation.sign_request_state`; the three checks
    below are what the signature is worth.
    """

    principal: str
    """Who the state was minted for.

    Rejecting a mismatch is what stops one caller redeeming another's state —
    the same derived principal id task ownership uses, so a leaked token and a
    leaked ``requestState`` fail the same way."""

    fingerprint: str
    """A digest of the call the state belongs to — method, tool name and the
    arguments *as the client sent them*.

    ⚠ Computed from the client's arguments, never the merged ones, precisely so
    it is stable across rounds: the client retries the original call unchanged,
    while our copy grows an answer each round. Binding to the merged form would
    invalidate the state on the first retry."""

    answers: dict[str, Any] = field(default_factory=dict)
    """Every accepted answer so far, keyed by the input name the service asked
    for it under.

    ⭐ **This is why more than one round works.** A retry carries only the
    *latest* round's ``inputResponses``; a service that asks a second question
    would otherwise lose the first answer, and would ask for it again forever.
    Accumulating here — rather than in a store keyed by some id — is what keeps
    the server stateless while still finishing a conversation."""

    round: int = 0
    """How many questions have already been answered on this call. Bounded by
    ``MAX_INPUT_ROUNDS`` so a service that can never be satisfied stops the
    exchange instead of ping-ponging with the client indefinitely."""

    def to_payload(self) -> dict[str, Any]:
        """The JSON-serialisable form that gets signed. Keys are short because
        the result is base64 and rides in every retry."""
        return {
            "p": self.principal,
            "f": self.fingerprint,
            "a": self.answers,
            "r": self.round,
        }

    @classmethod
    def from_payload(cls, payload: Any) -> RequestState | None:
        """Rebuild from a verified payload, or ``None`` if it is not one.

        Signature verification proves the payload came from this server; it does
        not prove the shape, because a payload signed by an *older* release of
        this package would also verify. Hence the type checks — a stale shape is
        treated exactly like a forged one, which costs the client one extra
        round and costs us no compatibility rules to remember.
        """
        if not isinstance(payload, dict):
            return None
        principal: Any = payload.get("p")
        fingerprint: Any = payload.get("f")
        answers: Any = payload.get("a")
        round_: Any = payload.get("r")
        if not isinstance(principal, str) or not isinstance(fingerprint, str):
            return None
        if not isinstance(answers, dict) or not isinstance(round_, int):
            return None
        return cls(principal=principal, fingerprint=fingerprint, answers=answers, round=round_)


__all__ = ["RequestState"]
