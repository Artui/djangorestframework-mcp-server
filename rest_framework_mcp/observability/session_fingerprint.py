from __future__ import annotations

_SESSION_ID_PREFIX_CHARS: int = 8


def session_fingerprint(session_id: str | None) -> str:
    """A short, non-replayable tag for correlating log lines about one client.

    A session id is a bearer credential. An operator reading logs must be able
    to follow one client across requests and must **not** be handed something
    they could replay; a prefix does the first without the second, and is enough
    to tell concurrent clients apart in practice.

    ``"-"`` for a request that carried no id at all — which is itself the thing
    worth seeing, since that is the ``400`` case rather than the ``404`` one.
    """
    if not session_id:
        return "-"
    return f"{session_id[:_SESSION_ID_PREFIX_CHARS]}…"


__all__ = ["session_fingerprint"]
