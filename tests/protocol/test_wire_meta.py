"""Every wire object emits the base protocol's generic ``_meta`` bundle.

``_meta`` is MCP's open extension namespace, carried on most wire objects.
The dataclass field is named ``meta`` (a field literally named ``_meta``
would read as private by Python convention); ``to_dict`` is the only place
the wire spelling appears. It is omitted entirely when unset *or* empty —
an empty ``"_meta": {}`` is legal but pure noise, and the handlers already
normalise an empty binding bundle to ``None``.
"""

from __future__ import annotations

from rest_framework_mcp.protocol.types.prompt import Prompt
from rest_framework_mcp.protocol.types.resource import Resource
from rest_framework_mcp.protocol.types.resource_contents import ResourceContents
from rest_framework_mcp.protocol.types.resource_template import ResourceTemplate
from rest_framework_mcp.protocol.types.tool import Tool
from rest_framework_mcp.protocol.types.tool_content_block import ToolContentBlock
from rest_framework_mcp.protocol.types.tool_result import ToolResult

META = {"example.com/flavour": {"colour": "teal"}}


# ---------- emitted when present ----------


def test_tool_emits_meta() -> None:
    assert Tool(name="t", meta=META).to_dict()["_meta"] == META


def test_resource_emits_meta() -> None:
    assert Resource(uri="x://1", name="r", meta=META).to_dict()["_meta"] == META


def test_resource_template_emits_meta() -> None:
    tpl = ResourceTemplate(uri_template="x://{pk}", name="r", meta=META)
    assert tpl.to_dict()["_meta"] == META


def test_resource_contents_emits_meta() -> None:
    assert ResourceContents(uri="x://1", text="hi", meta=META).to_dict()["_meta"] == META


def test_tool_content_block_emits_meta() -> None:
    assert ToolContentBlock(type="text", text="hi", meta=META).to_dict()["_meta"] == META


def test_tool_result_emits_meta() -> None:
    assert ToolResult(meta=META).to_dict()["_meta"] == META


def test_prompt_emits_meta() -> None:
    assert Prompt(name="p", meta=META).to_dict()["_meta"] == META


# ---------- omitted when unset or empty ----------


def test_tool_omits_meta_when_unset() -> None:
    assert "_meta" not in Tool(name="t").to_dict()


def test_resource_omits_meta_when_unset() -> None:
    assert "_meta" not in Resource(uri="x://1", name="r").to_dict()


def test_resource_template_omits_meta_when_unset() -> None:
    assert "_meta" not in ResourceTemplate(uri_template="x://{pk}", name="r").to_dict()


def test_resource_contents_omits_meta_when_unset() -> None:
    assert "_meta" not in ResourceContents(uri="x://1", text="hi").to_dict()


def test_tool_content_block_omits_meta_when_unset() -> None:
    assert "_meta" not in ToolContentBlock(type="text", text="hi").to_dict()


def test_tool_result_omits_meta_when_unset() -> None:
    assert "_meta" not in ToolResult().to_dict()


def test_prompt_omits_meta_when_unset() -> None:
    assert "_meta" not in Prompt(name="p").to_dict()


def test_empty_meta_is_omitted_rather_than_emitted_as_an_empty_object() -> None:
    assert "_meta" not in Tool(name="t", meta={}).to_dict()


def test_meta_rides_alongside_the_annotations_hint_bundle() -> None:
    """``_meta`` and ``annotations`` are different fields, both emitted."""
    out = Tool(name="t", annotations={"readOnlyHint": True}, meta=META).to_dict()
    assert out["annotations"] == {"readOnlyHint": True}
    assert out["_meta"] == META
