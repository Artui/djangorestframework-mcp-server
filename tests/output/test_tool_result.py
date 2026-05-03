from __future__ import annotations

import json
import sys

from rest_framework_mcp.output.format import OutputFormat
from rest_framework_mcp.output.tool_result import build_tool_result


def test_build_tool_result_json_default() -> None:
    res = build_tool_result({"a": 1})
    assert res.structured_content == {"a": 1}
    assert json.loads(res.content[0].text or "") == {"a": 1}


def test_build_tool_result_marks_error() -> None:
    res = build_tool_result({"err": "x"}, is_error=True)
    assert res.is_error is True


def test_build_tool_result_auto_picks_json_for_dict() -> None:
    res = build_tool_result({"a": 1}, output_format=OutputFormat.AUTO)
    assert json.loads(res.content[0].text or "") == {"a": 1}


def test_build_tool_result_auto_picks_toon_for_uniform_list(monkeypatch) -> None:
    class FakeToon:
        @staticmethod
        def encode(payload: object) -> str:
            return f"TOON:{payload!r}"

    monkeypatch.setitem(sys.modules, "toon", FakeToon())  # type: ignore[arg-type]
    res = build_tool_result([{"a": 1}, {"a": 2}], output_format=OutputFormat.AUTO)
    assert "format: toon" in (res.content[0].text or "")
    assert "TOON:" in (res.content[0].text or "")


def test_build_tool_result_auto_falls_back_to_json_for_mixed_list() -> None:
    res = build_tool_result([{"a": 1}, {"b": 2}], output_format=OutputFormat.AUTO)
    assert "format: toon" not in (res.content[0].text or "")


def test_build_tool_result_explicit_toon(monkeypatch) -> None:
    class FakeToon:
        @staticmethod
        def encode(payload: object) -> str:
            return f"TOON:{payload!r}"

    monkeypatch.setitem(sys.modules, "toon", FakeToon())  # type: ignore[arg-type]
    res = build_tool_result({"a": 1}, output_format=OutputFormat.TOON)
    text = res.content[0].text or ""
    assert text.startswith("# format: toon")
    assert "```toon" in text


def test_build_tool_result_auto_empty_list_picks_json() -> None:
    res = build_tool_result([], output_format=OutputFormat.AUTO)
    assert (res.content[0].text or "").startswith("[")


def test_build_tool_result_auto_list_of_non_dicts_picks_json() -> None:
    res = build_tool_result([1, 2, 3], output_format=OutputFormat.AUTO)
    assert "format: toon" not in (res.content[0].text or "")
