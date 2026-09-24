# Troubleshooting

## Every tool call returns 404

This is the most confusing failure the transport can produce, because four
unrelated causes look identical from the client. Work down the list.

### 1. Read the `MCP-Error` header

Since 0.25.0 every session rejection carries one:

| Header | HTTP | Meaning |
|---|---|---|
| `MCP-Error: session-missing` | `400` | No `Mcp-Session-Id` arrived at all |
| `MCP-Error: session-unknown` | `404` | An id arrived that this server will not honour |
| *(no header)* | `404` | **Not us.** Something in front of the application answered |

That last row is the important one. A load balancer with no matching rule
returns a bodiless 404 that is indistinguishable from a dead session — and the
JSON-RPC body that would tell them apart often never reaches a human, because
clients commonly log `${status} ${statusText}` and **HTTP/2 has no reason
phrase**. If there is no `MCP-Error` header, look at your routing layer, not at
this package.

!!! warning "Application logs may be silent either way"

    If your logging does not include `rest_framework_mcp` (see
    [Observability](observability.md)), the absence of log lines tells you
    *nothing* about whether the request arrived. Configure the logger before
    concluding anything from silence.

### 2. Check whether the session simply expired

`session-unknown` covers expired, evicted, terminated, and minted-for-another-
principal. Sessions have two windows:

- `SESSION_TTL_SECONDS` — **idle** timeout, default 24h, restarted on every
  request. A connector used continuously never hits it.
- `SESSION_MAX_AGE_SECONDS` — **absolute** ceiling, default 7 days, regardless
  of activity.

A connector idle over a long weekend hits the first with nothing wrong anywhere.

### 3. Rule out cache eviction

Neither window can promise more than the cache underneath it. A Redis
`maxmemory-policy` of `allkeys-lru` (or `allkeys-random`) evicts session keys
long before their timeout, and that is **indistinguishable from expiry** from
every angle a client can observe.

```bash
redis-cli CONFIG GET maxmemory-policy
```

Anything other than `noeviction` or a `volatile-*` policy that respects TTLs
means raising the TTL will not help.

### 4. Check the client re-initializes

The spec is explicit: a client that receives 404 for a request carrying a
session id **MUST** start a new session by sending a fresh `InitializeRequest`.
A client that instead surfaces the 404 as a tool failure turns a recoverable
condition into an outage that needs a human. If yours does that, see below.

## Sessions keep breaking and you don't control the client

Turn them off:

```python title="settings.py"
REST_FRAMEWORK_MCP = {"SESSIONS_ENABLED": False}
```

This is a **conformant mode, not a relaxation.** Both legacy revisions say a
server *"MAY assign a session ID at initialization time"*, and make the client's
duty to echo one back conditional on it having arrived. A server that never
assigns is never sent one.

With it off, the `initialize`-handshake era runs statelessly: no id is minted,
none is required, and a client still echoing a stale id is ignored rather than
rejected — so flipping the setting does not itself cause the outage it prevents.

**What you give up:** server-initiated messaging on the legacy era. The session
id is what addresses a client's SSE channel, so the `GET` stream has no address
and answers `405`; the session `DELETE` does likewise. Request/response tool
calling is untouched.

**What it does not affect:** the modern (`2026-07-28`) era, which is stateless
already and ignores this setting entirely. If your client speaks it, none of
this section applies to you.

## `ImproperlyConfigured` on startup

### "registered with no permissions"

Since 0.25.0 a tool must declare permissions. DRF viewset-level and
`REST_FRAMEWORK` default permission classes **do not reach MCP** — this package
deliberately bypasses DRF's view pipeline — so a spec that looks guarded over
HTTP ships as an open tool.

```python
ServiceSpec(service=create_invoice, permission_classes=[IsAuthenticated])
```

To migrate a large surface gradually, downgrade it to a warning:

```python
REST_FRAMEWORK_MCP = {"REQUIRE_TOOL_PERMISSIONS": False}
```

!!! tip "If your tests assign the settings dict"

    `settings.REST_FRAMEWORK_MCP = {...}` **replaces** the dict rather than
    merging, so a project-level opt-out disappears inside any test that does
    that. Add the key to those literals too.

### "outputSchema would be advertised but structuredContent is disabled"

The spec requires a tool declaring an `outputSchema` to return conforming
`structuredContent`. Since 0.25.0 this is caught when the tool is registered
rather than on the first call.

Note that server-wide `INCLUDE_OUTPUT_SCHEMA=True` with
`INCLUDE_STRUCTURED_CONTENT=False` is **legal** — it just requires every binding
to override the content back on. The error names the binding that did not.

### "input_serializer must be a DRF Serializer subclass" or "output serializer must be a DRF BaseSerializer subclass"

The serializer declared for a tool or resource is a shape no MCP path can use.
The usual cause is an instance where the class belongs:

```python
ServiceSpec(service=create_invoice, input_serializer=InvoiceSerializer(many=True))  # refused
ServiceSpec(service=create_invoice, input_serializer=InvoiceSerializer)  # accepted
```

The two sides accept different shapes. An **input** serializer is validated, so
it must be a `Serializer` subclass or a dataclass type. An **output** serializer
is only rendered, so any `BaseSerializer` subclass is accepted there, including
DRF's read-only pattern that implements just `to_representation`. A dataclass
is accepted on both sides: as output it renders through a `DataclassSerializer`
built for it. For a chain tool the message names the step whose serializer was
refused. A resource has only an output serializer, and it is held to the same
rule.

Such a tool used to register cleanly. Every call to it then failed, and `tools/list`
failed too, for every tool on the server, because this transport derives each
tool's schema on each listing. A resource never had a schema to derive, so it
failed on every read instead.

### "takes the name the spec's list travels under"

A `ServiceSpec` with `many=True` takes its list under one argument, named by the
spec's `many_argument` (default `items`; see
[A list payload](concepts.md#list-payload)). A
`UrlKwarg` or `QueryParam` of that name is popped out of the arguments before
dispatch, so the list would be routed to `view.kwargs` or the query string and
every call would be refused as missing its list. Rename the channel, or give the
list another name on the spec:

```python
ServiceSpec(
    service=create_invoices,
    input_serializer=InvoiceSerializer,
    many=True,
    many_argument="invoices",
)
```

### "cannot apply to a spec declaring many=True"

A `many=True` service receives the whole list as one `data` argument, so a
`SPREAD_AUTHOR_WINS` or `SPREAD_CALLER_WINS` argument binding has nothing to
spread, and drf-services raises `ValueError` for it on every call. Leave
`argument_binding` at its `BUNDLE` default.

### "declares both many=True and a collection_selector_spec"

A list payload and a collection target are two different bulk shapes, and a
`many=True` dispatch never resolves the collection. drf-services' own views refuse
the pair as well. Declare one of them.

### "declares many=True, so its input_serializer describes one item of a list"

A chain with no `input_serializer` of its own validates and advertises its
arguments as its first step's. On a `many=True` step that serializer describes one
item, so the chain listed a single item as its arguments and handed the bulk
service that one object. Give the chain its own `input_serializer`, and build the
step's list from it in `inputs`:

```python
class BulkInvoiceInput(serializers.Serializer):
    items = InvoiceSerializer(many=True)


server.register_chain_tool(
    name="invoices.bulk_create_and_notify",
    input_serializer=BulkInvoiceInput,
    steps=[
        ChainStep("created", bulk_create_spec, inputs=lambda ctx: {"data": ctx.args["items"]}),
        ChainStep("notified", notify_spec),
    ],
)
```

## `ValueError` when registering a resource

### "sets ..., which the resource read path does not apply"

`register_resource` refuses a `SelectorSpec` carrying any of eleven behavioural
fields — `preconditions`, `filter_set`, `select_related`, `prefetch_related`,
`annotations`, `extend_queryset`, `allow_none`, `output_serializer_context`,
`progress_reporter`, `metadata`, `affordances`. `resources/read` dispatches the
bare selector callable, so those would be silently dropped, and a `filter_set`
that scopes a tenant over HTTP would return every row here with nothing in the
response saying so.

Register the same spec as a selector *tool* — which honours all eleven — or move
the behaviour into the callable, where it travels with every dispatch.
[What a resource cannot carry, and why registration refuses it](concepts.md#what-a-resource-cannot-carry-and-why-registration-refuses-it)
has the field-by-field consequences.

## OAuth discovery returns 404s

### The authorization server must be a site root

Endpoint paths are appended to it, so passing the value django-oauth-toolkit
advertises as *its* issuer (`https://host/oauth`) publishes
`https://host/oauth/oauth/authorize/` and two siblings like it. Pass the site
root. Since 0.25.0 this warns at construction; if you genuinely mount elsewhere,
set the paths instead:

```python
DjangoOAuthToolkitBackend(
    authorization_servers=["https://host"],
    authorize_path="/custom/authorize/",
)
```

### Mount order against `oauth2_provider`

django-oauth-toolkit 3.4.0 serves its own `register/` and
`.well-known/oauth-authorization-server`. Django resolves first-match, so
mounting DOT's urls **before** `build_oauth_urlpatterns(...)` means DOT answers
those paths — silently, with different content.

```python
def test_our_oauth_routes_are_not_shadowed():
    from rest_framework_mcp.contrib.oauth import check_oauth_url_shadowing

    assert check_oauth_url_shadowing() == []
```

It is a function you call rather than a Django system check because this package
is a library with no `AppConfig` — there is nowhere to register one.

## A tool result is too large for the client

`MAX_RESULT_BYTES` (default 5 MiB) bounds the wire payload, but a client's
context window is far smaller — a result well under the ceiling can still be
undeliverable. The bound now logs at `WARNING` when it fires.

If a paginated tool still returns too much, the row count is not the problem:
check how wide each row is. A nested serializer that expands related objects can
make ten rows larger than a thousand lean ones, and `limit` cannot express a
byte budget.

## A tool call returns a DRF error body instead of JSON-RPC

A `tools/call` answered with something like HTTP `400` and a body of
``["`items` field is not found"]`` — no `jsonrpc`, no `id` — or with Django's
HTML `500` page, is an exception that escaped the dispatch. The transport had
no answer for it, so DRF's exception handler rendered it: an `APIException`
as DRF's own body, anything else as a server error. A client matching
responses by `id` has nothing to match, and most report a parse failure rather
than the error.

The common cause is a restql selection written against the page envelope. On
a `paginate=True` tool the serializer renders each item, so
`{items{id, number}}` names a field no item has and the serializer raises
while rendering. Select per item — `{id, number}` — see
[Query params](concepts.md#query-param-per-item).

Fixed in 0.49.0, which answers every such exception as JSON-RPC:

- A render-time rejection of a value the caller supplied is an `isError`
  `validation_error` tool result naming the param, the same shape as any
  other validation failure.
- Anything else is a `-32603` whose message is `Internal error`, carrying the
  request's `id`, under HTTP `500`. The exception is logged at `ERROR` with its
  traceback under `rest_framework_mcp.transport`; the text is not in the
  response, so read the log.
- A `PermissionDenied` (DRF's or Django's) is the exception: a permission
  class that refuses by raising, rather than returning `False`, is answered
  exactly like one that returned `False`, as a `FORBIDDEN` under `403` with its
  `WWW-Authenticate` challenge. It is a refusal, not a server fault, so nothing
  is logged at `ERROR`.

A progress-carrying call (one sent with a `progressToken`) has committed its
`200` before the dispatch runs, so it never showed a DRF body; before 0.49.0
its last frame was a `-32603` whose message was the exception's own text.
Since 0.49.0 that frame reads `Internal error` too.
