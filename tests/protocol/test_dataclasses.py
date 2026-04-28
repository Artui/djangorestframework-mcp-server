from __future__ import annotations

from rest_framework_mcp.protocol.client_capabilities import ClientCapabilities
from rest_framework_mcp.protocol.implementation import Implementation
from rest_framework_mcp.protocol.initialize_params import InitializeParams
from rest_framework_mcp.protocol.initialize_result import InitializeResult
from rest_framework_mcp.protocol.json_rpc_error import JsonRpcError
from rest_framework_mcp.protocol.json_rpc_error_code import JsonRpcErrorCode
from rest_framework_mcp.protocol.json_rpc_notification import JsonRpcNotification
from rest_framework_mcp.protocol.json_rpc_request import JsonRpcRequest
from rest_framework_mcp.protocol.json_rpc_response import JsonRpcResponse
from rest_framework_mcp.protocol.parse_message import parse_message
from rest_framework_mcp.protocol.resource import Resource
from rest_framework_mcp.protocol.resource_contents import ResourceContents
from rest_framework_mcp.protocol.resource_template import ResourceTemplate
from rest_framework_mcp.protocol.server_capabilities import ServerCapabilities
from rest_framework_mcp.protocol.tool import Tool
from rest_framework_mcp.protocol.tool_content_block import ToolContentBlock
from rest_framework_mcp.protocol.tool_result import ToolResult


def test_implementation_to_dict() -> None:
    assert Implementation(name="x", version="1").to_dict() == {"name": "x", "version": "1"}


def test_client_capabilities_empty() -> None:
    assert ClientCapabilities().to_dict() == {}


def test_client_capabilities_full() -> None:
    caps = ClientCapabilities(
        roots={"list": True}, sampling={}, elicitation={"x": 1}, experimental={"y": 2}
    )
    out = caps.to_dict()
    assert out == {
        "roots": {"list": True},
        "sampling": {},
        "elicitation": {"x": 1},
        "experimental": {"y": 2},
    }


def test_server_capabilities_full() -> None:
    caps = ServerCapabilities(
        tools={"list": {}},
        resources={"r": True},
        prompts={"p": True},
        logging={"l": 1},
        completions={"c": 1},
        experimental={"e": 1},
    )
    out = caps.to_dict()
    assert set(out.keys()) == {
        "tools",
        "resources",
        "prompts",
        "logging",
        "completions",
        "experimental",
    }


def test_server_capabilities_only_tools() -> None:
    caps = ServerCapabilities(tools={}, resources=None)
    out = caps.to_dict()
    assert "tools" in out
    assert "resources" not in out


def test_server_capabilities_with_tools_none() -> None:
    caps = ServerCapabilities(tools=None, resources={})
    out = caps.to_dict()
    assert "tools" not in out
    assert "resources" in out


def test_initialize_params_from_payload_minimal() -> None:
    parsed = InitializeParams.from_payload({})
    assert parsed.protocol_version == ""
    assert parsed.client_info.name == ""


def test_initialize_params_from_payload_full() -> None:
    parsed = InitializeParams.from_payload(
        {
            "protocolVersion": "2025-11-25",
            "capabilities": {
                "roots": {"list": True},
                "sampling": {},
                "elicitation": {},
                "experimental": {},
            },
            "clientInfo": {"name": "c", "version": "1"},
        }
    )
    assert parsed.protocol_version == "2025-11-25"
    assert parsed.capabilities.roots == {"list": True}
    assert parsed.client_info == Implementation(name="c", version="1")


def test_initialize_result_with_instructions() -> None:
    res = InitializeResult(
        protocol_version="2025-11-25",
        capabilities=ServerCapabilities(),
        server_info=Implementation(name="s", version="1"),
        instructions="hello",
    )
    assert res.to_dict()["instructions"] == "hello"


def test_initialize_result_no_instructions() -> None:
    res = InitializeResult(
        protocol_version="2025-11-25",
        capabilities=ServerCapabilities(),
        server_info=Implementation(name="s", version="1"),
    )
    assert "instructions" not in res.to_dict()


def test_json_rpc_error_to_dict_min() -> None:
    err = JsonRpcError(code=JsonRpcErrorCode.PARSE_ERROR, message="boom")
    assert err.to_dict() == {"code": -32700, "message": "boom"}


def test_json_rpc_error_to_dict_with_data() -> None:
    err = JsonRpcError(code=-32000, message="boom", data={"hint": "x"})
    assert err.to_dict()["data"] == {"hint": "x"}


def test_json_rpc_request_to_dict_with_params() -> None:
    req = JsonRpcRequest(method="m", id=1, params={"a": 1})
    assert req.to_dict() == {"jsonrpc": "2.0", "method": "m", "id": 1, "params": {"a": 1}}


def test_json_rpc_request_to_dict_without_params() -> None:
    req = JsonRpcRequest(method="m", id=1)
    assert "params" not in req.to_dict()


def test_json_rpc_notification_to_dict_with_params() -> None:
    note = JsonRpcNotification(method="n", params={"x": 1})
    assert note.to_dict()["params"] == {"x": 1}


def test_json_rpc_notification_to_dict_no_params() -> None:
    note = JsonRpcNotification(method="n")
    assert "params" not in note.to_dict()


def test_json_rpc_response_to_dict_result() -> None:
    res = JsonRpcResponse(id=1, result={"ok": True})
    assert res.to_dict()["result"] == {"ok": True}


def test_json_rpc_response_to_dict_error() -> None:
    res = JsonRpcResponse(id=1, error=JsonRpcError(code=-32600, message="bad"))
    assert "error" in res.to_dict()
    assert "result" not in res.to_dict()


def test_tool_to_dict_full() -> None:
    tool = Tool(
        name="t",
        description="d",
        title="T",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        annotations={"readOnlyHint": True},
    )
    out = tool.to_dict()
    assert out["title"] == "T"
    assert out["description"] == "d"
    assert out["outputSchema"] == {"type": "object"}
    assert out["annotations"] == {"readOnlyHint": True}


def test_tool_to_dict_minimal() -> None:
    out = Tool(name="t").to_dict()
    assert "title" not in out
    assert "description" not in out
    assert "outputSchema" not in out
    assert "annotations" not in out
    assert out["inputSchema"] == {"type": "object"}


def test_tool_content_block_full() -> None:
    block = ToolContentBlock(
        type="image", text="t", data="d", mime_type="image/png", annotations={"k": 1}
    )
    out = block.to_dict()
    assert out["text"] == "t"
    assert out["data"] == "d"
    assert out["mimeType"] == "image/png"
    assert out["annotations"] == {"k": 1}


def test_tool_content_block_minimal() -> None:
    out = ToolContentBlock(type="text").to_dict()
    assert out == {"type": "text"}


def test_tool_result_with_error_flag() -> None:
    res = ToolResult(content=[ToolContentBlock(type="text", text="oops")], is_error=True)
    out = res.to_dict()
    assert out["isError"] is True
    assert out["content"][0]["text"] == "oops"
    assert "structuredContent" not in out


def test_tool_result_with_structured() -> None:
    res = ToolResult(content=[ToolContentBlock(type="text", text="x")], structured_content={"a": 1})
    assert res.to_dict()["structuredContent"] == {"a": 1}


def test_resource_to_dict_full() -> None:
    r = Resource(
        uri="u://1",
        name="n",
        title="T",
        description="d",
        mime_type="text/plain",
        size=42,
        annotations={"k": 1},
    )
    out = r.to_dict()
    assert out["title"] == "T"
    assert out["description"] == "d"
    assert out["mimeType"] == "text/plain"
    assert out["size"] == 42
    assert out["annotations"] == {"k": 1}


def test_resource_to_dict_minimal() -> None:
    out = Resource(uri="u://1", name="n").to_dict()
    assert out == {"uri": "u://1", "name": "n"}


def test_resource_template_full() -> None:
    t = ResourceTemplate(
        uri_template="u://{x}",
        name="n",
        title="T",
        description="d",
        mime_type="application/json",
        annotations={"k": 1},
    )
    out = t.to_dict()
    assert out["title"] == "T"
    assert out["description"] == "d"
    assert out["mimeType"] == "application/json"
    assert out["annotations"] == {"k": 1}


def test_resource_template_minimal() -> None:
    out = ResourceTemplate(uri_template="u://", name="n").to_dict()
    assert out == {"uriTemplate": "u://", "name": "n"}


def test_resource_contents_text() -> None:
    out = ResourceContents(uri="u", mime_type="text/plain", text="hi").to_dict()
    assert out == {"uri": "u", "mimeType": "text/plain", "text": "hi"}


def test_resource_contents_blob() -> None:
    out = ResourceContents(uri="u", blob="ZGF0YQ==").to_dict()
    assert out == {"uri": "u", "blob": "ZGF0YQ=="}


def test_resource_contents_minimal() -> None:
    assert ResourceContents(uri="u").to_dict() == {"uri": "u"}


def test_parse_message_request() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "method": "m", "params": {"x": 1}})
    assert isinstance(msg, JsonRpcRequest)
    assert msg.method == "m"


def test_parse_message_notification() -> None:
    msg = parse_message({"jsonrpc": "2.0", "method": "m"})
    assert isinstance(msg, JsonRpcNotification)


def test_parse_message_response_with_result() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.result == {"ok": True}


def test_parse_message_response_with_error() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.error is not None and msg.error.code == -32600


def test_parse_message_response_with_null_error() -> None:
    msg = parse_message({"jsonrpc": "2.0", "id": 1, "result": None, "error": None})
    assert isinstance(msg, JsonRpcResponse)
    assert msg.error is None


def test_parse_message_rejects_non_dict() -> None:
    import pytest

    with pytest.raises(ValueError, match="JSON object"):
        parse_message([])  # type: ignore[arg-type]


def test_parse_message_rejects_wrong_version() -> None:
    import pytest

    with pytest.raises(ValueError, match="version"):
        parse_message({"jsonrpc": "1.0", "method": "m"})


def test_parse_message_rejects_non_string_method() -> None:
    import pytest

    with pytest.raises(ValueError, match="must be a string"):
        parse_message({"jsonrpc": "2.0", "method": 7})


def test_parse_message_rejects_non_dict_error() -> None:
    import pytest

    with pytest.raises(ValueError, match="object"):
        parse_message({"jsonrpc": "2.0", "id": 1, "error": "boom"})


def test_parse_message_rejects_unknown_shape() -> None:
    import pytest

    with pytest.raises(ValueError, match="must contain"):
        parse_message({"jsonrpc": "2.0", "id": 1})
