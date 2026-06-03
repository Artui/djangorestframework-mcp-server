"""Unit coverage for ``handlers.utils`` helpers.

Focused on ``invoke_context_provider`` — the signature-aware shim that
forwards a serializer-context provider's resolved-data extras
(``result`` / ``instance`` / ``page``) only when the provider declares
them, mirroring sister-repo 0.15's ``output_serializer_context`` contract.
"""

from __future__ import annotations

from typing import Any

from rest_framework_mcp.handlers.utils import invoke_context_provider


def test_legacy_two_arg_provider_gets_exactly_view_and_request() -> None:
    seen: dict[str, Any] = {}

    def provider(view: Any, request: Any) -> dict[str, Any]:
        seen["args"] = (view, request)
        return {}

    invoke_context_provider(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    # Undeclared extras must not leak in — called with exactly two args.
    assert seen["args"] == ("VIEW", "REQ")


def test_declared_extra_is_passed_by_keyword() -> None:
    seen: dict[str, Any] = {}

    def provider(view: Any, request: Any, *, result: Any) -> dict[str, Any]:
        seen["result"] = result
        return {}

    invoke_context_provider(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    assert seen["result"] == "R"


def test_var_keyword_provider_receives_all_extras() -> None:
    seen: dict[str, Any] = {}

    def provider(view: Any, request: Any, **extra: Any) -> dict[str, Any]:
        seen.update(extra)
        return {}

    invoke_context_provider(provider, "VIEW", "REQ", extras={"result": "R", "page": "P"})
    assert seen == {"result": "R", "page": "P"}


def test_provider_return_is_passed_through() -> None:
    def provider(view: Any, request: Any, *, instance: Any) -> dict[str, Any]:
        return {"id": instance}

    out = invoke_context_provider(provider, "V", "R", extras={"instance": 7})
    assert out == {"id": 7}
