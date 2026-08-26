from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any


def toon_encoder() -> Callable[[Any], str] | None:
    """The optional ``python-toon`` encoder, or ``None`` when the extra is absent.

    One place asks whether TOON is really available, so the encoder and the
    caller that labels its output cannot disagree about which format the bytes
    are in. Imported via ``importlib`` so ``ty`` does not flag the optional
    module as unresolved where the ``[toon]`` extra is not installed; the result
    is not cached here because ``sys.modules`` already caches it.
    """
    try:
        toon = importlib.import_module("toon")
    except ImportError:
        return None
    encode: Callable[[Any], str] = toon.encode
    return encode


__all__ = ["toon_encoder"]
