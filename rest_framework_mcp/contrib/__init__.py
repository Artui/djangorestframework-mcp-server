"""Opt-in glue that does not belong in the core package.

Nothing here is imported by the core MCP transport. Consumers wire in the
helpers they need — typically an OAuth endpoint matrix or a user-hydration
adapter — and ignore the rest.
"""
