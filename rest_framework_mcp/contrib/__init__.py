"""Opt-in glue that doesn't belong in the core package.

Everything under ``rest_framework_mcp.contrib`` is additive — none of it
is imported by the core MCP transport. Consumers wire in the helpers
they need (typically an OAuth endpoint matrix or a user-hydration
adapter) and ignore the rest.
"""
