from __future__ import annotations


def audience_matches(token_audience: str | None, expected: str | None) -> bool:
    """Return True if the token's audience satisfies the configured resource URL.

    Implements RFC 8707 audience binding: when the resource server has a
    canonical URL configured, every accepted token must carry that URL as its
    audience. Two configurations short-circuit to ``True``:

    - ``expected is None`` — audience enforcement is disabled. Suitable for
      development or deployments where audience binding happens upstream
      (e.g. a gateway terminates the bearer token).
    - ``expected == token_audience`` — exact-match (the only safe comparison;
      token audiences are URLs, not patterns).

    A token whose ``aud`` claim is missing while ``expected`` is set is
    explicitly rejected — silent acceptance would defeat the point of
    configuring the canonical URL.
    """
    if expected is None:
        return True
    if token_audience is None:
        return False
    return token_audience == expected


__all__ = ["audience_matches"]
