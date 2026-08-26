from __future__ import annotations

import json
import sys

import pytest
from rest_framework_services import UNSET

from rest_framework_mcp.constants import OutputFormat
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


def test_build_tool_result_omits_structured_content_when_disabled() -> None:
    res = build_tool_result({"a": 1}, include_structured_content=False)
    assert res.structured_content is UNSET
    # Text payload still carries the data.
    assert json.loads(res.content[0].text or "") == {"a": 1}
    # ``to_dict`` drops the field entirely so the wire payload is leaner.
    assert "structuredContent" not in res.to_dict()


def test_build_tool_result_includes_structured_content_by_default() -> None:
    res = build_tool_result({"a": 1})
    assert res.to_dict()["structuredContent"] == {"a": 1}


def test_tool_result_carries_per_call_meta() -> None:
    """``_meta`` on the *result envelope* is per-call, so it is a parameter.

    A tool's static ``_meta`` is already advertised on its ``tools/list``
    entry; nothing sources this from the binding.
    """
    result = build_tool_result({"ok": True}, meta={"example.com/trace": "abc"})
    assert result.to_dict()["_meta"] == {"example.com/trace": "abc"}


def test_tool_result_omits_meta_by_default() -> None:
    assert "_meta" not in build_tool_result({"ok": True}).to_dict()


# ---------- the format marker names the format the bytes are actually in ----------


def test_the_toon_marker_is_not_stamped_when_the_encoder_fell_back_to_json(
    monkeypatch,
) -> None:
    """Without the optional extra, TOON output is JSON — and must say so.

    ``encode_toon`` warns and returns JSON, but that warning goes to the
    server's warnings filter, not onto the wire. Branching on the *requested*
    format stamped ``# format: toon`` over JSON bytes, so a client or pipeline
    selecting its parser from the marker line mis-parsed, with
    ``structuredContent`` the only channel still telling the truth.
    """
    monkeypatch.setitem(sys.modules, "toon", None)
    with pytest.warns(UserWarning, match="python-toon"):
        res = build_tool_result({"a": 1}, output_format=OutputFormat.TOON)
    text = res.content[0].text or ""
    assert "format: toon" not in text
    assert "```toon" not in text
    assert json.loads(text) == {"a": 1}


def test_the_auto_format_also_leaves_the_marker_off_when_toon_is_absent(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "toon", None)
    with pytest.warns(UserWarning, match="python-toon"):
        res = build_tool_result([{"a": 1}, {"a": 2}], output_format=OutputFormat.AUTO)
    assert "format: toon" not in (res.content[0].text or "")


# ---------- a null payload is a value, not an absent structured channel ----------


def test_a_null_payload_is_emitted_as_an_explicit_null() -> None:
    """``include_structured_content=True`` must be distinguishable from ``False``.

    Omitting the key for a genuine ``None`` collapsed "this tool's answer is
    null" into "this tool has no structured channel", so a client branching on
    the key's presence was told the wrong thing about the server's capability.
    """
    res = build_tool_result(None, include_structured_content=True)
    wire = res.to_dict()
    assert "structuredContent" in wire
    assert wire["structuredContent"] is None


def test_opting_out_still_omits_the_key_entirely() -> None:
    res = build_tool_result(None, include_structured_content=False)
    assert "structuredContent" not in res.to_dict()


def test_a_resource_link_result_makes_the_same_distinction(monkeypatch) -> None:
    from rest_framework_mcp.constants import ToolContentKind

    res = build_tool_result(
        {"uri": "https://example.test/doc", "name": "doc"},
        content_kind=ToolContentKind.RESOURCE_LINK,
        include_structured_content=False,
    )
    assert "structuredContent" not in res.to_dict()
