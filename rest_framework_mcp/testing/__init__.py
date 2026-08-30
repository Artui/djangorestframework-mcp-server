"""Test helpers, packaged so a consumer's suite can import them.

Everything here is for a *consumer's* tests, not this package's. It ships in
the wheel because the alternative is what happened before: the assertion lived
in a test file, which is to say outside the wheel, so every project wanting it
wrote its own -- and the version a project writes on its own is the key-set
comparison, which is the one that does not catch anything.

Nothing here is imported by the package at runtime, and its dependency is an
extra: ``pip install djangorestframework-mcp-server[test]``.
"""

from rest_framework_mcp.testing.assert_tool_result_conforms import (
    assert_tool_result_conforms,
)

__all__ = ["assert_tool_result_conforms"]
