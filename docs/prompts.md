# Prompts

Prompts are the MCP capability for **server-defined message templates** that
clients invoke by name. A typical prompt fills client-supplied arguments
into a structured set of LLM messages — system prompt, few-shot examples,
the actual ask — and returns them so the client can replay the conversation
through whichever model it's wired up to.

```python
from rest_framework_mcp import MCPServer, PromptArgument

server = MCPServer(name="my-app")


@server.prompt(
    arguments=[
        PromptArgument(name="topic", description="What to brainstorm.", required=True),
        PromptArgument(name="count", description="How many ideas to return."),
    ],
)
def brainstorm(*, topic: str, count: str = "5") -> str:
    """Generate a brainstorm of ideas about a topic."""
    return (
        f"You are a brainstorming assistant. Produce {count} concise, distinct "
        f"ideas about: {topic}. Number each."
    )
```

The decorator registers a `PromptBinding`. The same render callable can be
called directly from Python; nothing about MCP is leaking in.

## What `render` can return

The handler normalises whatever the callable returns:

| Return value | Becomes |
| --- | --- |
| `str` | One user-text message |
| `list[str]` | One user-text message per item |
| `PromptMessage` | Wrapped in a single-item list |
| `list[PromptMessage]` | Passed through |
| `list[dict]` | Each dict is treated as a wire-shaped `PromptMessage` (must have `role` + `content`) |
| anything else | `JsonRpcError` `-32603 Internal Error` |

For multi-turn prompts, return `PromptMessage` objects directly:

```python
from rest_framework_mcp import PromptMessage


@server.prompt()
def critique(*, draft: str) -> list[PromptMessage]:
    """Reviewer with one few-shot example."""
    return [
        PromptMessage.text("user", "Please review the attached draft."),
        PromptMessage.text("assistant", "Sure — share the draft."),
        PromptMessage.text("user", draft),
    ]
```

## Arguments

`PromptArgument` declares one input. Required arguments missing from
`prompts/get` produce a `-32602 Invalid Params` with `data={"missing": [...]}`
so the client can surface a useful UI:

```python
PromptArgument(name="topic", description="What to brainstorm", required=True)
```

The arguments dict is threaded into `render` as kwargs. `request` and `user`
are automatically supplied if the callable declares them — same kwarg-pool
pattern as services and selectors.

## Permissions and rate limits

Prompts use the same `MCPPermission` and `MCPRateLimit` machinery as tools and
resources:

```python
from rest_framework_mcp import ScopeRequired
from rest_framework_mcp.auth.rate_limits.fixed_window_rate_limit import (
    FixedWindowRateLimit,
)


@server.prompt(
    permissions=[ScopeRequired(["assistant:prompt"])],
    rate_limits=[FixedWindowRateLimit(max_calls=120, per_seconds=60)],
)
def assistant(*, draft: str) -> str:
    return f"Critique this draft: {draft}"
```

## Capability advertisement

The server advertises `capabilities.prompts` only when at least one prompt is
registered. An empty registry → no advertisement, so well-behaved clients
don't bother calling `prompts/list` they know is empty.

## Async render

Just declare the callable async; it'll run native under `async_urls`:

```python
@server.prompt()
async def fetch_then_render(*, topic: str) -> str:
    notes = await fetch_notes_for(topic)  # async I/O
    return f"Summarize these notes:\n{notes}"
```

The same async/sync detection that's in `acall` routes the dispatch.

## Pagination

`prompts/list` is paginated identically to `tools/list` — the same opaque
cursor scheme, the same `PAGE_SIZE` setting. Clients echo back `nextCursor`
without inspecting it.
