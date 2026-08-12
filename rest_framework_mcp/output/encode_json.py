from __future__ import annotations

import json
from typing import Any


def encode_json(payload: Any) -> str:
    """Encode ``payload`` as a stable, pretty JSON string.

    ``default=str`` renders DRF outputs containing ``Decimal``, ``UUID`` or
    ``datetime`` without raising; keys are sorted so the output is
    deterministic.
    """
    return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)


__all__ = ["encode_json"]
