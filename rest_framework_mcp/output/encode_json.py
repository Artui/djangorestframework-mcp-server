from __future__ import annotations

import json
from typing import Any


def encode_json(payload: Any) -> str:
    """Encode ``payload`` as a stable, pretty JSON string.

    Uses ``default=str`` so DRF outputs containing ``Decimal``, ``UUID``,
    ``datetime``, etc. are rendered without raising. Sorts keys for
    deterministic output (helpful in tests and diff-friendly transcripts).
    """
    return json.dumps(payload, indent=2, sort_keys=True, default=str, ensure_ascii=False)


__all__ = ["encode_json"]
