from __future__ import annotations

import json
import uuid
from datetime import date, datetime
from decimal import Decimal

from rest_framework_mcp.output.encode_json import encode_json


def test_encode_json_handles_decimal_uuid_datetime() -> None:
    out = encode_json(
        {
            "amount": Decimal("12.34"),
            "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
            "ts": datetime(2026, 4, 28, 12, 0, 0),
            "d": date(2026, 4, 28),
        }
    )
    parsed = json.loads(out)
    assert parsed["amount"] == "12.34"
    assert parsed["id"] == "12345678-1234-5678-1234-567812345678"
    assert parsed["ts"].startswith("2026-04-28")


def test_encode_json_sort_keys() -> None:
    out = encode_json({"b": 1, "a": 2})
    assert out.index('"a"') < out.index('"b"')


def test_encode_json_unicode_passthrough() -> None:
    out = encode_json({"name": "café"})
    assert "café" in out
