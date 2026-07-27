"""``build_resource_contents`` — render + encode, shared by both read handlers."""

from __future__ import annotations

from typing import Any

from rest_framework import serializers
from rest_framework_services.types.selector_kind import SelectorKind

from rest_framework_mcp.constants import JsonRpcErrorCode, ResourceEncoding
from rest_framework_mcp.output.build_resource_contents import build_resource_contents
from rest_framework_mcp.protocol.types.json_rpc_error import JsonRpcError
from rest_framework_mcp.registry.types.resource_binding import ResourceBinding


class _Out(serializers.Serializer):
    label = serializers.CharField()


def _binding(**overrides: Any) -> ResourceBinding:
    defaults: dict[str, Any] = {
        "name": "thing",
        "uri_template": "things://x",
        "description": None,
        "selector": lambda: None,
        "kind": SelectorKind.RETRIEVE,
    }
    return ResourceBinding(**{**defaults, **overrides})


class TestEncoding:
    def test_json_is_the_default(self) -> None:
        contents = build_resource_contents(binding=_binding(), uri="things://x", raw={"a": 1})

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '{\n  "a": 1\n}'

    def test_text_returns_the_document_verbatim(self) -> None:
        """The whole point: JSON-encoding HTML yields a quoted string literal."""
        binding = _binding(encoding=ResourceEncoding.TEXT)
        contents = build_resource_contents(binding=binding, uri="ui://v", raw="<h1>Hi</h1>")

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == "<h1>Hi</h1>"

    def test_json_encoding_would_have_quoted_it(self) -> None:
        """Pins the behaviour TEXT exists to avoid, so a regression is visible."""
        contents = build_resource_contents(binding=_binding(), uri="ui://v", raw="<h1>Hi</h1>")

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '"<h1>Hi</h1>"'

    def test_a_non_string_under_text_is_a_json_rpc_error(self) -> None:
        """A misconfiguration, but it must not become a transport-level 500 —
        the selector's return type isn't knowable at registration."""
        binding = _binding(encoding=ResourceEncoding.TEXT)
        result = build_resource_contents(binding=binding, uri="ui://v", raw={"not": "html"})

        assert isinstance(result, JsonRpcError)
        assert result.code == JsonRpcErrorCode.INTERNAL_ERROR
        assert "encoding=TEXT" in result.message
        assert "dict" in result.message


class TestRendering:
    def test_output_serializer_renders_a_retrieve_as_one_object(self) -> None:
        binding = _binding(output_serializer=_Out, kind=SelectorKind.RETRIEVE)
        contents = build_resource_contents(binding=binding, uri="things://x", raw={"label": "one"})

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '{\n  "label": "one"\n}'

    def test_output_serializer_renders_a_list_as_many(self) -> None:
        binding = _binding(output_serializer=_Out, kind=SelectorKind.LIST)
        contents = build_resource_contents(
            binding=binding, uri="things://x", raw=[{"label": "one"}]
        )

        assert not isinstance(contents, JsonRpcError)
        assert contents.text == '[\n  {\n    "label": "one"\n  }\n]'

    def test_the_serializer_runs_before_the_encoding_check(self) -> None:
        """Order matters: the encoding check must see the *rendered* value, so
        a serializer can't turn a TEXT binding's payload into a body by
        accident and go unreported."""
        binding = _binding(output_serializer=_Out, encoding=ResourceEncoding.TEXT)
        result = build_resource_contents(binding=binding, uri="ui://v", raw={"label": "one"})

        assert isinstance(result, JsonRpcError)
        # ``ReturnDict``, not ``dict`` — the rendered type, not the raw one.
        assert "ReturnDict" in result.message


class TestEnvelope:
    def test_carries_the_bindings_uri_mime_type_and_meta(self) -> None:
        binding = _binding(mime_type="text/csv", meta={"x": 1})
        contents = build_resource_contents(binding=binding, uri="things://actual", raw=[])

        assert not isinstance(contents, JsonRpcError)
        assert (contents.uri, contents.mime_type, contents.meta) == (
            "things://actual",
            "text/csv",
            {"x": 1},
        )

    def test_an_empty_meta_is_omitted(self) -> None:
        contents = build_resource_contents(binding=_binding(), uri="things://x", raw=[])

        assert not isinstance(contents, JsonRpcError)
        assert contents.meta is None

    def test_the_meta_is_copied_not_shared_with_the_binding(self) -> None:
        binding = _binding(meta={"x": 1})
        contents = build_resource_contents(binding=binding, uri="things://x", raw=[])

        assert not isinstance(contents, JsonRpcError)
        assert contents.meta is not binding.meta
