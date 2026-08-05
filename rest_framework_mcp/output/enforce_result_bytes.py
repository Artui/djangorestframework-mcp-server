from __future__ import annotations

from typing import Any

from rest_framework_mcp.observability import get_logger
from rest_framework_mcp.output.encode_json import encode_json

logger = get_logger(__name__)


def enforce_result_bytes(payload: Any, max_bytes: int | None, *, label: str) -> str | None:
    """Return an explanatory message when ``payload`` exceeds ``max_bytes``, else ``None``.

    The outbound mirror of the transport's ``MAX_REQUEST_BYTES`` check.
    ``max_bytes`` of ``None`` disables the check.

    **Measured on the encoded result, not on the rendered text block.** A
    successful ``tools/call`` result carries its payload twice — once as
    ``structuredContent`` and once as the ``content[0]`` text the spec asks for
    as a backwards-compatibility mirror — so a ceiling counting only one of them
    would be wrong by 2× against the thing that actually matters, the client's
    context window. Callers hand in the finished result dict for that reason.

    **Returns a message rather than a response** because the two callers need
    different shapes for the same condition: a tool call answers with an
    ``isError`` result the model can act on, while ``resources/read`` has no
    such envelope and answers with a JSON-RPC error. Building either here would
    force one of them to unwrap the other.

    The message names the measured size, the ceiling and the remedies, because
    its audience is a language model deciding what to do next — "result too
    large" alone is not something a model can act on.
    """
    if max_bytes is None:
        return None
    size: int = len(encode_json(payload).encode("utf-8"))
    if size <= max_bytes:
        return None
    # The caller is told; the operator was not, until now. A bound that fires
    # invisibly reads to everyone else as "the tool is broken".
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
