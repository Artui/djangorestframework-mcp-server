from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

from rest_framework_mcp.output.encode_json import encode_json
from rest_framework_mcp.output.utils import toon_encoder


def encode_toon(payload: Any) -> str:
    """Encode ``payload`` as TOON (token-oriented object notation).

    TOON is an optional dependency. Without ``python-toon`` installed this
    warns and falls back to JSON, so a tool call never breaks because the extra
    is absent. The warning fires every time — silence it with
    ``warnings.filterwarnings`` or install the extra.

    The return value alone cannot say which encoder produced it, so a caller
    that labels the text — ``build_tool_result`` stamps a ``# format: toon``
    marker — must ask ``toon_encoder`` rather than assume.
    """
    encode: Callable[[Any], str] | None = toon_encoder()
    if encode is None:
        warnings.warn(
            "python-toon is not installed; falling back to JSON. "
            "Install with `pip install djangorestframework-mcp-server[toon]`.",
            stacklevel=2,
        )
        return encode_json(payload)
    return encode(payload)


__all__ = ["encode_toon"]
