from __future__ import annotations

import json
import sys

import pytest

from rest_framework_mcp.output.encode_toon import encode_toon


def test_encode_toon_falls_back_to_json_when_extra_missing(monkeypatch) -> None:
    # Force the import of `toon` to fail by stubbing sys.modules.
    monkeypatch.setitem(sys.modules, "toon", None)
    with pytest.warns(UserWarning, match="python-toon"):
        out = encode_toon({"a": 1})
    parsed = json.loads(out)
    assert parsed == {"a": 1}


def test_encode_toon_uses_toon_when_available(monkeypatch) -> None:
    class FakeToon:
        @staticmethod
        def encode(payload: object) -> str:
            return f"TOON:{payload!r}"

    monkeypatch.setitem(sys.modules, "toon", FakeToon())  # type: ignore[arg-type]
    out = encode_toon({"a": 1})
    assert out.startswith("TOON:")
