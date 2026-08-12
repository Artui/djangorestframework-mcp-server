from __future__ import annotations

_SESSION_ID_PREFIX_CHARS: int = 8


def session_fingerprint(session_id: str | None) -> str:
    """A short, non-replayable tag for correlating log lines about one client.

    A session id is a bearer credential, so logs get a prefix rather than the
    id: enough to follow one client across requests, not enough to replay.

    ``"-"`` for a request that carried no id at all — the ``400`` case rather
    than the ``404`` one, and worth seeing as such.
    """
    if not session_id:
        return "-"
    return f"{session_id[:_SESSION_ID_PREFIX_CHARS]}…"


__all__ = ["session_fingerprint"]
