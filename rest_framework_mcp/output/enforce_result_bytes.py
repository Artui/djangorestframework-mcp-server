from __future__ import annotations

from typing import Any

from rest_framework_mcp.observability import get_logger
from rest_framework_mcp.output.encode_json import encode_json

logger = get_logger(__name__)


def enforce_result_bytes(payload: Any, max_bytes: int | None, *, label: str) -> str | None:
    """Return an explanatory message when ``payload`` exceeds ``max_bytes``, else ``None``.

    The outbound mirror of the transport's ``MAX_REQUEST_BYTES`` check.
    ``max_bytes`` of ``None`` disables it.

    **Measured on the encoded result, not on the rendered text block.** A
    successful ``tools/call`` result carries its payload twice — as
    ``structuredContent`` and as the ``content[0]`` text mirror the spec asks
    for — so counting one copy would be wrong by 2× against the client's context
    window. Callers hand in the finished result dict for that reason.

    **Returns a message rather than a response** because the two callers need
    different shapes: a tool call answers with an ``isError`` result, while
    ``resources/read`` has no such envelope and answers with a JSON-RPC error.

    The message names the size, the ceiling and the remedies, because its
    audience is a model deciding what to do next.
    """
    if max_bytes is None:
        return None
    size: int = len(encode_json(payload).encode("utf-8"))
    if size <= max_bytes:
        return None
    # The caller is told by the returned message; the operator only by this. A
    # bound that fires invisibly reads to everyone else as "the tool is broken".
    logger.warning(
        "Result bound exceeded: %s produced %d bytes over a %d byte ceiling",
        label,
        size,
        max_bytes,
    )
    return (
        f"{label} produced a {size} byte result, over this server's "
        f"{max_bytes} byte ceiling. Narrow the request — add or tighten a "
        "filter, lower 'limit', or select fewer fields — and call again. The "
        "result was not truncated: a partial payload would look complete."
    )


__all__ = ["enforce_result_bytes"]
