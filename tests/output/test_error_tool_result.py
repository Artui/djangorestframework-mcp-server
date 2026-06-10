from __future__ import annotations

import json

from rest_framework_mcp.output.error_tool_result import build_error_tool_result


def test_error_payload_shape() -> None:
    result = build_error_tool_result("nope", error_type="service_error")
    out = result.to_dict()
    assert out["isError"] is True
    assert "structuredContent" not in out
    payload = json.loads(out["content"][0]["text"])
    assert payload == {"error": {"type": "service_error", "message": "nope"}}


def test_detail_merges_into_error_object() -> None:
    result = build_error_tool_result(
        "bad input",
        error_type="validation_error",
        detail={"detail": {"f": ["required"]}, "failedStep": "s"},
    )
    payload = json.loads(result.to_dict()["content"][0]["text"])
    assert payload["error"]["type"] == "validation_error"
    assert payload["error"]["detail"] == {"f": ["required"]}
    assert payload["error"]["failedStep"] == "s"


def test_empty_detail_is_omitted() -> None:
    result = build_error_tool_result("x", error_type="not_found", detail={})
    payload = json.loads(result.to_dict()["content"][0]["text"])
    assert payload == {"error": {"type": "not_found", "message": "x"}}
