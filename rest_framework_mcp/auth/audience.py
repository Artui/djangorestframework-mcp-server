from __future__ import annotations


def audience_matches(token_audience: str | None, expected: str | None) -> bool:
    """Return True if the token's audience satisfies the configured resource URL.

    RFC 8707 audience binding. ``expected is None`` disables enforcement (the
    binding happens upstream, e.g. at a gateway). Otherwise the comparison is
    exact — token audiences are URLs, not patterns — and a token carrying no
    audience is rejected rather than accepted, which is the whole point of
    configuring the canonical URL.
    """
    if expected is None:
        return True
    if token_audience is None:
        return False
    return token_audience == expected


__all__ = ["audience_matches"]
