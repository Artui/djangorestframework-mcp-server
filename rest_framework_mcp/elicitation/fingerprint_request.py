from __future__ import annotations

import hashlib
import json
from typing import Any


def fingerprint_request(method: str, payload: Any) -> str:
    """A stable digest identifying the call a ``requestState`` belongs to.

    The spec asks servers to bind state to *"an identifier for the originating
    request, e.g. the method name and a digest of its salient parameters,
    rejecting state presented on a request that does not match"*. This is that
    digest. Without it a token minted for a harmless call could be replayed onto
    a destructive one — same principal, still unexpired, and carrying an
    ``answers`` map that the second call would read as a confirmation the user
    never gave.

    ``sort_keys`` plus ``default=str`` is what makes it *stable* rather than
    merely deterministic: JSON object order is not meaningful, so two encodings
    of the same arguments must not disagree, and an argument that is not
    JSON-native (a ``Decimal``, a date the client sent as a string that DRF
    already coerced) must not raise where a mismatch would do.

    ⚠ Feed it the arguments **as the client sent them**, never the merged ones —
    see :attr:`~rest_framework_mcp.elicitation.types.request_state.RequestState.fingerprint`.
    """
    encoded: str = json.dumps(
        {"method": method, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["fingerprint_request"]
