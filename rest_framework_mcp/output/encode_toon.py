from __future__ import annotations

import importlib
import warnings
from typing import Any

from rest_framework_mcp.output.encode_json import encode_json


def encode_toon(payload: Any) -> str:
    """Encode ``payload`` as TOON (token-oriented object notation).

    TOON is an optional dependency. Without ``python-toon`` installed this
    warns and falls back to JSON, so a tool call never breaks because the extra
    is absent. The warning fires every time — silence it with
    ``warnings.filterwarnings`` or install the extra.
    """
    try:
        # Via ``importlib`` so ``ty`` doesn't flag the optional module as
        # unresolved where the ``[toon]`` extra isn't installed.
        toon = importlib.import_module("toon")
    except ImportError:
        warnings.warn(
            "python-toon is not installed; falling back to JSON. "
            "Install with `pip install djangorestframework-mcp-server[toon]`.",
            stacklevel=2,
        )
        return encode_json(payload)
    return toon.encode(payload)


__all__ = ["encode_toon"]
