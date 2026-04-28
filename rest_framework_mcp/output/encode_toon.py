from __future__ import annotations

import warnings
from typing import Any

from rest_framework_mcp.output.encode_json import encode_json


def encode_toon(payload: Any) -> str:
    """Encode ``payload`` as TOON (token-oriented object notation).

    TOON is an optional dependency. If ``python-toon`` is not installed, this
    encoder issues a warning and falls back to JSON so a tool call never
    breaks just because the extra is absent. The warning fires every time —
    silencing it is the consumer's job (``warnings.filterwarnings`` or
    installing the extra).
    """
    try:
        import toon  # ty: ignore[unresolved-import]
    except ImportError:
        warnings.warn(
            "python-toon is not installed; falling back to JSON. "
            "Install with `pip install djangorestframework-mcp-server[toon]`.",
            stacklevel=2,
        )
        return encode_json(payload)
    return toon.dumps(payload)


__all__ = ["encode_toon"]
