"""The package's logger namespace, and why it exists.

Until 0.25.0 this package emitted **nothing**: no ``getLogger``, no ``logger``
call anywhere, in a library that terminates a wire protocol, authenticates every
request, enforces six kinds of bound and rejects requests for four distinct
reasons.

⭐ **That did not merely slow an incident down, it corrupted one.** A consumer
debugging a transport-wide ``404`` reported "nothing in the application logs",
and that was read as evidence the request never reached Django — a whole branch
of hypotheses grew on it. The package would have been silent either way.
*Absence of logs is only evidence when something would have logged.*

**Namespace.** ``rest_framework_mcp.<module>``, via ``getLogger(__name__)`` at
each site, so a project configures it through Django ``LOGGING`` like any other
library::

    LOGGING = {
        "loggers": {"rest_framework_mcp": {"level": "INFO", "handlers": ["console"]}},
    }

**What is logged where.** ``WARNING`` for every rejection a caller sees but an
operator cannot otherwise explain — session, auth, origin, protocol version, and
each outbound bound. ``INFO`` for ``initialize`` and the era a request selected.
``DEBUG`` for per-call timing and result size.

⭐ **The no-oracle rule applies to the response, not the log.** The session gate
deliberately merges "unknown id" with "id owned by another principal" so a
caller cannot probe session ids — but the operator reading the log is not the
adversary that rule protects against, and a log line is not the wire. Server
side we name the exact condition, which is the single most useful thing in here.

⚠ **Never logged:** bearer tokens, full session ids (a session id is a
credential — log a short prefix), or tool arguments and results, which are
consumer domain data and may be anything at all.
"""

from __future__ import annotations

from rest_framework_mcp.observability.get_logger import get_logger
from rest_framework_mcp.observability.session_fingerprint import session_fingerprint

__all__ = ["get_logger", "session_fingerprint"]
