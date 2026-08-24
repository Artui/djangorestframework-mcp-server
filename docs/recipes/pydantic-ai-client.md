# Connect a Pydantic-AI agent over MCP

Point a [Pydantic-AI](https://ai.pydantic.dev) agent at this server's
Streamable-HTTP endpoint. One line of client code, no coupling in either
direction: the agent process needs nothing from this package, and this package
needs nothing from Pydantic-AI.

```python
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset

agent = Agent("openai:gpt-5", toolsets=[MCPToolset("https://example.com/mcp/")])

async with agent:
    result = await agent.run("How many invoices are unpaid?")
```

`MCPToolset` speaks Streamable HTTP, which is exactly what
`path("mcp/", server.urls)` mounts. Every registered tool arrives as an agent
tool, named and schema'd as `tools/list` describes it.

## First decide whether you want HTTP at all

If the agent runs **inside the same Django process** as the specs it calls,
this recipe is the long way round. Serialising a call to JSON, pushing it
through a socket back into the same interpreter, and paying an auth round trip
buys nothing when there is no network between the two.

Use
[`djangorestframework-pydantic-ai`](https://artui.github.io/djangorestframework-pydantic-ai/)'s
`SpecToolset` instead. It takes the same `ServiceSpec` / `SelectorSpec` objects
you register here, reflects the same schemas through
`djangorestframework-services`' `spec_to_json_schema`, and dispatches
in-process:

```python
from rest_framework_pydantic_ai import SpecToolset

agent = Agent("openai:gpt-5", toolsets=[SpecToolset(registry)])
```

Reach for `MCPToolset` when the network is real:

| Use `MCPToolset` when | Use `SpecToolset` when |
|---|---|
| the agent is a separate process, service or machine | the agent runs in your Django process |
| the consumer is somebody else's client (an IDE, a desktop host) | you own both halves |
| you want one surface for MCP hosts and your own agent alike | you want the shortest path and no transport |

The two are not exclusive. Declare the specs once in a `SpecRegistry` (see
[Register tools from a shared spec registry](register-from-spec-registry.md)),
feed it to `MCPServer.register_specs` for the wire and to `SpecToolset` for
the in-process agent, and the two surfaces cannot drift apart.

## What the client stack resolves to

`MCPToolset` is built on the FastMCP client, which is built on the MCP Python
SDK. Which revision of the protocol you end up speaking is decided by that
bottom layer, not by anything you configure:

| Install | FastMCP | MCP SDK | Protocol era |
|---|---|---|---|
| `pip install "pydantic-ai-slim[mcp]"` | 3.x | 1.x | legacy (`2025-11-25`) |
| the same, allowing prereleases | 4.0.0b3 | 2.0.0 | modern (`2026-07-28`) |

Both connect, list tools and call them against this server — it serves both
eras on one endpoint (see
[Protocol eras](../concepts.md#protocol-eras)). The difference shows up in one
place only, and it is the interesting one: elicitation.

```bash
# The modern-era stack, as of the versions above. Drop --prerelease=allow
# once FastMCP 4 is released; add the httpx constraint only while the
# prerelease resolver would otherwise pick up an httpx 1.0 dev build.
uv pip install --prerelease=allow "pydantic-ai-slim[mcp]" "httpx<1"
```

## Elicitation works, on the modern stack

A service that raises `AdditionalInputRequired` is answered with an
`input_required` result rather than a value — the question rides in the result
and the client retries the original call (see
[Asking the user](../concepts.md#asking-the-user-elicitation)). That is a
different mechanism from the server-initiated `elicitation/create` request the
older revisions used, so it is worth saying plainly which one the client
implements.

**The FastMCP 4 client implements the result-carried form**, resolves the retry
loop itself, and reuses the same `elicitation_handler` argument to ask you the
question:

```python
async def elicitation_handler(message, response_type, params, context):
    # `message` is the service's own message, verbatim.
    # `params.requested_schema` is the restricted JSON Schema it asked for.
    print(message)
    return {"confirmed": True}


toolset = MCPToolset(
    "https://example.com/mcp/",
    elicitation_handler=elicitation_handler,
)
```

Nothing else changes. The agent sees one tool call and one tool return; the two
HTTP round trips, the `requestState` and the retry are the toolset's business:

```text
tools/call  rows.delete {"count": 500}
  -> resultType: input_required, inputRequests, requestState
tools/call  rows.delete {"count": 500} + inputResponses + requestState
  -> resultType: complete, {"deleted": 500, "confirmed": true}
```

Return an explicit refusal to decline instead of answering:

```python
from fastmcp.client.elicitation import ElicitResult


async def elicitation_handler(message, response_type, params, context):
    return ElicitResult(action="decline")
```

The call then comes back as an `isError` result with
`"type": "input_declined"`, which Pydantic-AI surfaces to the model as a
`ModelRetry`. Give the model instructions about what a declined confirmation
means, or it will reasonably try again.

!!! warning "Ignore the `elicitation_handler will never be called` warning"

    Pydantic-AI emits a `UserWarning` on connecting saying the handler will
    never be called because a modern session "holds no connection for the
    server to issue elicitation requests over". That is true of the mechanism
    it was written for and false for this one: the question does not need a
    connection, because it arrives inside a result. The handler is called.
    Filter the warning if it is noisy:

    ```python
    warnings.filterwarnings("ignore", message=".*elicitation_handler.* will never be called.*")
    ```

### On the legacy stack it degrades instead

A client on the 1.x SDK negotiates `2025-11-25`, which has no way to carry the
question. This server does not fall back to the old server-initiated request —
that direction was removed from the protocol — so the call returns an ordinary
error result carrying the message **and the schema**:

```json
{"error": {"type": "input_required",
           "message": "500 rows match. Confirm to proceed.",
           "requestedInput": {"confirmed": {"type": "boolean"}}}}
```

Pydantic-AI turns that into a `ModelRetry`, so a capable model reads what is
missing and supplies `confirmed: true` on its next call — the same outcome by a
shorter route, with the model deciding rather than the user. `elicitation_handler`
is never invoked on this path, whatever the client declared at `initialize`:
this server reads elicitation support per request, and a legacy request carries
no capabilities.

If the confirmation must come from a human, the modern stack is not optional.

## What you still have to configure

**Authentication.** `MCPToolset` sends whatever headers you give it; this
server checks them with its `MCPAuthBackend`. For the default OAuth backend,
pass the bearer token through:

```python
MCPToolset(
    "https://example.com/mcp/",
    headers={"Authorization": f"Bearer {token}"},
)
```

`AllowAnyBackend` is for local development only — see
[Authentication](../auth.md).

**Origin, or rather not.** A non-browser client sends no `Origin` header, which
this server treats as same-origin and accepts, so the default empty
`ALLOWED_ORIGINS` needs no widening for an agent. Do not add `"*"` to make a
Python client work; it does not need it, and it disables the DNS-rebinding
check that browser clients rely on.

**ASGI, if you want SSE.** Mount `server.async_urls` rather than `server.urls`
when the agent should receive push notifications or when dispatch is I/O-bound
(see [Async deployment](../async.md)). Plain tool calling works under WSGI.

## Trying it locally

The [`invoicing` example](https://github.com/Artui/djangorestframework-mcp-server/tree/main/examples/invoicing)
is a complete server to point this at:

```bash
cd examples/invoicing
python manage.py migrate
python manage.py runserver
```

```python
import asyncio

from pydantic_ai.mcp import MCPToolset


async def main() -> None:
    async with MCPToolset("http://127.0.0.1:8000/mcp/") as toolset:
        print([tool.name for tool in await toolset.list_tools()])
        print(await toolset.direct_call_tool("invoices.list", {}))


asyncio.run(main())
```

`direct_call_tool` bypasses the model loop, which makes it the fastest way to
confirm the wire is working before you spend a token on it.
