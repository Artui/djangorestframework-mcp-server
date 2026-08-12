"""The package's logger namespace and its logging conventions.

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

**The no-oracle rule applies to the response, not the log.** The session gate
deliberately merges "unknown id" with "id owned by another principal" so a
caller cannot probe session ids — but a log line is not the wire, so server side
we name the exact condition.

**Never logged:** bearer tokens, full session ids (a session id is a
credential — log a short prefix), or tool arguments and results, which are
consumer domain data and may be anything at all.
"""

from __future__ import annotations

from rest_framework_mcp.observability.get_logger import get_logger
from rest_framework_mcp.observability.session_fingerprint import session_fingerprint

__all__ = ["get_logger", "session_fingerprint"]
