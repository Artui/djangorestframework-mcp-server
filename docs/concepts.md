# Concepts

A short tour of every moving part. Read this once; the rest of the docs assume
you have these in your head.

## `ServiceSpec` is the unit of registration

The MCP server does **not** wrap, walk, or otherwise reach into DRF viewsets,
routers, or views. Consumers register
[`ServiceSpec`](https://github.com/Artui/djangorestframework-services/blob/main/rest_framework_services/types/service_spec.py)
instances directly. Same value object as the HTTP transport — same callable can
serve both at once.

```python
from rest_framework_mcp import SelectorKind, SelectorSpec, ServiceSpec  # re-exported for ergonomics

spec = ServiceSpec(
    service=create_invoice,
    input_serializer=InvoiceInputSerializer,
    output_selector_spec=SelectorSpec(  # nested spec for the post-call
        kind=SelectorKind.RETRIEVE,  # render pipeline (RETRIEVE → many=False,
        output_serializer=InvoiceOutputSerializer,  # LIST → many=True)
        selector=None,  # optional post-call re-fetch callable
    ),
    atomic=True,  # wrap dispatch in transaction.atomic()
    success_status=None,  # ignored by MCP — used by HTTP
    kwargs=None,  # optional per-spec kwargs provider; see below
)
```

This means a project that uses neither `ServiceViewSet` nor DRF routers can
still expose its services over MCP. The HTTP and MCP transports are siblings,
not layers — neither owns the other.

### What `ServiceSpec` / `SelectorSpec` carries through to MCP

The MCP layer honors the same spec fields as the HTTP transport —
register a spec once and both surfaces get the same shape:

- **`permission_classes`** — DRF `BasePermission` classes. Auto-wrapped
  with `DRFPermissionAdapter` and prepended to the per-binding
  `permissions` tuple, so spec-declared permissions run before any
  tool-level `MCPPermission` you add at the MCP call site.
- **`SelectorSpec` queryset shaping** — `select_related`,
  `prefetch_related`, `annotations`, and `extend_queryset` are applied
  before the FilterSet / ordering / pagination pipeline. Non-queryset
  returns (lists, scalars) pass through unchanged.
- **Serializer context** — every serializer the MCP transport builds
  carries DRF's baseline context (`request` / `format` / `view`, from the
  synthesised pair), exactly as `get_serializer_context()` supplies it
  behind a view — so a serializer reading `self.context["request"]`
  unguarded renders the same over both surfaces. On top of that,
  `input_serializer_context` / `output_serializer_context` (on
  `ServiceSpec`) and `output_serializer_context` (on `SelectorSpec`) are
  merged over the baseline and forwarded as `context=` to the serializer
  constructor, on both sync and async dispatch paths. Providers are
  invoked **through the keyword pool** — each receives the subset of
  `view` / `request` / the resolved-data extra (`result` / `instance` /
  `page`) it declares *by name*, or the whole pool if it takes `**kwargs`
  — which is how drf-services invokes them on the HTTP path, so one
  provider serves both. Requires `djangorestframework-services>=0.29.0`.
- **`SelectorSpec.kind`** — required `SelectorKind` discriminator
  (`LIST` or `RETRIEVE`). It drives the `many=` flag on the output
  serializer and gates which post-fetch knobs the registration
  accepts (a `RETRIEVE` spec rejects the collection-only
  `ordering_fields` / `paginate`, but `filter_set` is allowed — it is
  shaped + applied before the single-instance `.first()`).
  `SelectorKind` is re-exported from `rest_framework_mcp` for
  convenience.
- **`ServiceSpec.output_selector_spec`** — a nested
  `SelectorSpec | None` describing the post-call render pipeline
  (optional re-fetch via its `selector`, then `output_serializer`
  with `many=` driven by its `kind`). The decorator forms
  (`@server.service_tool`, etc.) accept flat `output_serializer=` /
  `output_selector=` kwargs and build the nested spec internally;
  direct `ServiceSpec(...)` construction uses the nested shape.

### Per-spec kwargs providers

`ServiceSpec.kwargs` (and `SelectorSpec.kwargs`) is a callable that returns
extra kwargs to merge into the dispatch pool — useful for plumbing per-tenant
context, signed lookups, etc. without scattering `request.user.*` reads
across services.

```python
from rest_framework_services import OfflineServiceView, ServiceSpec


def with_tenant(view: OfflineServiceView, request) -> dict:
    return {"tenant_id": request.user.tenant_id}


server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInputSerializer,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.RETRIEVE,
            output_serializer=InvoiceOutputSerializer,
        ),
        kwargs=with_tenant,
    ),
)
```

The provider receives an `OfflineServiceView` (synthesised by the sister
repo's `build_offline_context` because MCP has no DRF view) — `view.action`
is the binding name, and on resource reads `view.kwargs` carries the
URI-template variables. Same wire shape as the
HTTP transport's `ServiceView`, so providers can be shared between
transports.

Like every provider in the framework, it is invoked **through the keyword
pool**: it receives `view` / `request` *by name*, so it declares only what it
needs (`def with_tenant(request): ...` is as valid as the two-parameter form,
and `**kwargs` takes the whole pool). Declaring a parameter the pool doesn't
carry is the error — not declaring one it does.

### URL kwargs — route values a provider reads off `view.kwargs`

On a **tool** call, `view.kwargs` is empty by default (a tool has no URL). So a
provider shared with an HTTP view that scopes by a route capture — e.g.
`view.kwargs["project_pk"]` behind a tenant/role lookup — would return its
fallback (usually `None`) over MCP and **mis-scope for every caller**. Register a
[`UrlKwarg`](reference/registries.md) so the model can supply that route value: it is
advertised as a tool argument, popped at dispatch, and seeded into the off-HTTP
`view.kwargs` — from where drf-services spreads it into the dispatch pools
(authoritative over the spec params, below the `spec.kwargs` provider).

```python
from rest_framework_mcp import UrlKwarg
from rest_framework_services import ServiceSpec


def scope_by_project(view, request) -> dict:
    # Over HTTP this reads a URL capture; over MCP it reads the UrlKwarg the
    # model supplied — same code, both transports.
    return {"role": role_in_project(request.user, view.kwargs.get("project_pk"))}


server.register_service_tool(
    name="policies.update",
    spec=ServiceSpec(service=update_policy, kwargs=scope_by_project),
    url_kwargs=(UrlKwarg("project_pk", type="integer", description="owning project"),),
)
```

Because a URL kwarg is popped before the spec sees the arguments, it never counts
as an unknown argument (the `REJECT` policy ignores it) and never lands in the
service's validated payload — it routes **only** through `view.kwargs`. A name
can't collide with a reserved transport key (`ordering` / `page` / `limit`, or the
`request` / `user` / `data` / `instance` / `serializer` / `collection` pool
seeds); colliding with an ordinary spec input is allowed and is the intended way
to route a route-capture the spec *also* reads directly.

A capture the spec genuinely cannot run without takes `required=True`:

```python
UrlKwarg("project_pk", type="integer", required=True)
```

The name joins the tool's `inputSchema` `required` list, so the model is told up
front — and, because a schema hint is only a hint, a call that omits it comes back
as an `isError` validation result naming the missing argument rather than failing
somewhere less legible. `required` can't be combined with a `default` (a default
always satisfies the argument, so requiring it would be a no-op); that raises at
registration.

#### A reflected `**extras` key is not a route capture

A selector typed `def list_widgets(user, **extras: Unpack[WidgetExtras])` that
reads `extras["project_pk"]` already has that key reflected into the tool's
`inputSchema` by drf-services (0.26+) — no `UrlKwarg` needed **for the selector
itself**, which receives it through the spec params. Marking it `InputRequired`
(drf-services 0.28+) makes the model supply it; that is a *schema* statement and
changes nothing about where the value lands.

The two declarations answer different questions, and only one of them puts a
value on the request:

| | reflected `**extras` key (± `InputRequired`) | registered `UrlKwarg` |
| --- | --- | --- |
| In the `inputSchema` | yes | yes |
| Can be required | yes (`InputRequired`) | yes (`required=True`, plus an `isError` result when omitted) |
| Reaches the selector | yes, as a spec param | yes, via the `view.kwargs` spread |
| Reaches `view.kwargs` | **no** | yes |
| Ranks above caller-supplied params | no — it *is* caller input | yes |

So anything that reads request state rather than its own arguments — a
`spec.kwargs` provider, `extend_queryset`, a permission class, an
`output_serializer_context` provider — sees nothing for a reflected-only key. A
scoping provider doing `view.kwargs.get("project_pk")` returns `None` and
**mis-scopes every call** instead of failing: the failure mode worth naming here
is that it is silent.

Register the `UrlKwarg` as well when the value is scope. It is a strict superset
— the selector still receives it in `**extras`, and the schema keeps one property
and one `required` entry (an explicit `UrlKwarg` wins the merge over a reflected
key of the same name).

That split mirrors HTTP, where a route capture arrives in the URL and never in the
body — which is what makes it unspoofable. Over MCP the arguments are whatever the
model chose; a `UrlKwarg` value outranks them. If a provider scopes by it, it has
to come through the channel that carries that precedence.

`UrlKwarg` is
[drf-services' type](https://github.com/Artui/djangorestframework-services/blob/main/rest_framework_services/types/url_kwarg.py),
re-exported here — the declaration is the same whichever transport carries it, and
the two adapters that each had a copy had already drifted apart on which names
they reserved. `from rest_framework_mcp import UrlKwarg` keeps working.
Requires `djangorestframework-services>=0.28.1`.

### Query params: read-shaping values the serializer reads

`UrlKwarg`'s sibling. A `QueryParam` is a model-supplied argument that lands in
`request.query_params` instead of `view.kwargs` — the channel a serializer reads
when it branches on the query string (django-restql field selection, a custom
serializer keyed on `?expand=`):

```python
from rest_framework_mcp import QueryParam

server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(
        kind=SelectorKind.LIST, selector=list_invoices, output_serializer=InvoiceSerializer
    ),
    paginate=True,
    query_params=(QueryParam("query", description="django-restql fieldset, e.g. {id,number}"),),
)
```

The value is advertised in the tool's `inputSchema`, **popped** from the
arguments, and handed to `build_offline_context(query_params=…)`. It never
reaches the spec as an input, so the unknown-argument policy never sees it.

Three rules worth knowing:

- **Never required.** A read-shaping param the spec runs fine without cannot be
  required, which is why `QueryParam` has no such flag — unlike `UrlKwarg`,
  where a missing route capture means the spec cannot run at all.
- **One name, one channel.** Declaring the same name as both a `QueryParam` and a
  `UrlKwarg` raises at registration: a value is popped from the arguments once
  and routes to one place.
- **A `filter_set` field is not a query param.** Filter fields are already
  generated into the tool schema and flow through as ordinary arguments, which is
  where `dispatch_spec` reads them. Declaring one as a `QueryParam` would pop it
  out of the arguments and it would silently stop filtering.

!!! warning "The MCP endpoint's query string is no longer a channel"
    Every dispatch path wraps the real Django `POST` to the MCP endpoint, so
    until `0.23.0` whatever query string a client appended to that URL —
    `POST /mcp/?fields=all` — showed up in `request.query_params` for every call
    on that connection. It was undeclared, client-controlled, identical for every
    call, and invisible to the model.

    Every call site now passes `query_params=` explicitly, so the synthetic
    request's `GET` is always a value this package chose: the registered params
    for a tool that declares them, and empty everywhere else. If you were relying
    on that passthrough, declare a `QueryParam` for each value instead — it then
    arrives per call, advertised to the model, and validated at registration.

    Resources and prompts get the closing and no registration knob: a resource
    URI *is* a locator, so per-call read-shaping belongs in its URI template,
    whose variables already route to `view.kwargs`.

`QueryParam` is
[drf-services' type](https://github.com/Artui/djangorestframework-services/blob/main/rest_framework_services/types/query_param.py),
re-exported here for the same reason as `UrlKwarg` — one declaration, whichever
transport carries it.

### `SelectorSpec` for resources

`register_resource(selector=...)` requires a
[`SelectorSpec`](https://github.com/Artui/djangorestframework-services/blob/main/rest_framework_services/types/selector_spec.py),
mirroring `register_service_tool(spec=ServiceSpec(...))`. The unit of registration
is a spec on both surfaces.

- `.kind` is the required `SelectorKind` discriminator (`LIST` or
  `RETRIEVE`); it drives the `many=` flag on the output serializer at
  dispatch. `RETRIEVE` is the typical choice for a single-object
  URI-template lookup.
- `.selector` is the callable that gets dispatched (must be set; specs with
  `selector=None` are rejected).
- `.output_serializer` fills in when the caller didn't pass one explicitly.
- `.kwargs` becomes the binding's per-request kwargs provider.

```python
from rest_framework_mcp import SelectorKind, SelectorSpec

server.register_resource(
    name="invoice",
    uri_template="invoices://{pk}",
    selector=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,
        output_serializer=InvoiceOutputSerializer,
        kwargs=with_tenant,
    ),
)
```

Bare callables are rejected with `TypeError` — this is intentional: keeping
the imperative surface symmetric with `register_service_tool` makes the spec the
single point where output serializers and kwargs providers attach. Use the
`@server.resource(uri_template=...)` decorator if you'd rather skip the
boilerplate; it wraps the function in a `SelectorSpec` for you.

## Per-tool registration kwargs

Beyond `permissions=`, `output_format=`, and `include_structured_content=`,
`register_service_tool` / `register_selector_tool` (and their decorator
forms) accept three behavior knobs:

- **`argument_binding=`** — how the validated `arguments` flow into the
  callable's kwarg pool. The enum is re-exported from
  `djangorestframework-services` (the transport-neutral `dispatch_spec` owns
  these policies).
  - `ArgumentBinding.BUNDLE` (default for service tools) — only
    `data=<validated>` enters the pool.
  - `ArgumentBinding.SPREAD_AUTHOR_WINS` (default for selector tools) — every
    key from the validated arguments is spread into the pool as a top-level
    kwarg, so selectors can declare individual parameters
    (`def list_drafts(*, project_id, page=1)`). `spec.kwargs(...)` wins
    on conflict so author-declared invariants beat client input.
  - `ArgumentBinding.SPREAD_CALLER_WINS` — like `SPREAD_AUTHOR_WINS` but the
    spread wins on conflict, so `spec.kwargs(...)` supplies client-overridable
    defaults.
  - `ArgumentBinding.AUTO` — resolve per spec type (service → `BUNDLE`,
    selector → `SPREAD_AUTHOR_WINS`).

  Reserved transport-pool seeds (`request` / `user` / `data` / `instance` /
  `serializer`) and the
  selector pipeline keys (`ordering` / `page` / `limit`) are stripped
  from the spread regardless of mode so clients can't poison
  transport-controlled state.

- **`unknown_arguments=`** — how `arguments` keys outside the binding's
  declared field set are handled.
  - `UnknownArguments.REJECT` (default) — the validator rejects unknown
    keys with `-32602`, and the outer `inputSchema` advertises
    `"additionalProperties": false`. This holds only when the binding has an
    `input_serializer` to validate against: a serializer-less binding has no
    declared field set, so `REJECT` can't fire and its schema stays open
    (`"additionalProperties": true`) to match the runtime.
  - `UnknownArguments.PASSTHROUGH` — `"additionalProperties": true`;
    unknown keys survive validation and are merged onto the validated
    payload before binding.
  - `UnknownArguments.IGNORE` — `"additionalProperties": true`; unknown
    keys are silently dropped (the historic DRF default).

  Selector tools' pipeline-reserved keys are always treated as "known",
  so the policy doesn't fight the post-fetch pipeline.

- **`always_listed=`** — when
  `REST_FRAMEWORK_MCP["FILTER_LISTINGS_BY_PERMISSIONS"]` is enabled,
  bindings are dropped from `tools/list` / `resources/list` /
  `prompts/list` when their permissions deny the current caller.
  Setting `always_listed=True` keeps the binding visible as a discovery
  aid; the permission still gates the actual invocation.

### Tool annotations

Every tool advertises the MCP-standard `ToolAnnotations` hints, derived
from what the server already knows about the tool's mutation profile —
so downstream clients get correct `readOnlyHint` / `destructiveHint`
without a hand-set flag:

- **Selector tools** are reads → `{"readOnlyHint": true}`.
- **Service tools** are mutations → `{"readOnlyHint": false,
  "destructiveHint": true}`.
- **Chain tools** are read-only only when *every* step is a selector;
  any service step makes the whole chain a mutation.

`destructiveHint` / `idempotentHint` are spec-meaningful only when
`readOnlyHint` is false, so a read-only tool emits neither. Pass
`annotations=` at registration to override or extend the derived hints —
the explicit values win:

```python
server.register_service_tool(
    name="invoices.mark_paid",
    spec=mark_paid_spec,
    # An idempotent, non-destructive mutation:
    annotations={"destructiveHint": False, "idempotentHint": True},
)
```

The merged bundle lands on `binding.annotations` and on the `tools/list`
wire payload.

### Generic `_meta`

Separate from `annotations` — which is a closed, spec-defined set of
client hints — the base protocol gives most wire objects a free-form
`_meta` object. It is the extension namespace: each protocol extension
owns a top-level key inside it, and a server may add its own.

Pass `meta=` at any registration surface (`register_service_tool`,
`register_selector_tool`, `register_chain_tool`, `register_resource`,
`register_prompt`, the matching decorators, or a `ToolDefinition` /
`SelectorDefaults` / `ServiceDefaults`):

```python
server.register_selector_tool(
    name="invoices.list",
    spec=list_invoices_spec,
    meta={"example.com/panel": {"href": "panel://invoices"}},
)
```

The bundle lands on `binding.meta` and is emitted verbatim under the
`"_meta"` key of the binding's listing entry — `tools/list`,
`resources/list`, `resources/templates/list`, `prompts/list` — and, for a
resource, on the `contents` block `resources/read` returns. It is
omitted entirely when empty.

Nothing here validates, reserves, or rewrites a key: the whole point of
`_meta` is that its contents are opaque to the transport. On the
`tools/call` result envelope `_meta` is per-call rather than per-binding,
so it is a `build_tool_result(..., meta=...)` argument instead of
something sourced from the binding.

### Bulk registration

For projects that register many tools in one place, the
`register_tools(server, definitions, *, selector_defaults=None,
service_defaults=None)` entry point collapses the boilerplate. Pass a
list of `ToolDefinition.service(...)` / `ToolDefinition.selector(...)`
instances plus per-kind defaults that fill in fields each definition
leaves as `None`. The function loops over the existing per-tool
registration methods, so every guarantee and bug fix applies
automatically.

```python
from rest_framework_mcp import (
    ServiceDefaults,
    SelectorDefaults,
    ToolDefinition,
    register_tools,
)

register_tools(
    server,
    [
        ToolDefinition.service(name="invoices.create", spec=create_spec),
        ToolDefinition.service(name="invoices.update", spec=update_spec),
        ToolDefinition.selector(name="invoices.list", spec=list_spec),
    ],
    service_defaults=ServiceDefaults(permissions=[ScopeRequired(["invoices:write"])]),
    selector_defaults=SelectorDefaults(permissions=[ScopeRequired(["invoices:read"])]),
)
```

Per-definition kwargs win over defaults on conflict; `None` is the
"no override" sentinel across both layers.

## Transport-neutral invocation: `call_tool`

`server.call_tool(name, arguments, *, user, request=None)` invokes a
registered spec-backed tool **off the HTTP / JSON-RPC path** and returns
the same `ToolResult` the wire handlers build. An in-process consumer — a
bridge, a Pydantic-AI toolset, a management command — uses it instead of
re-implementing dispatch:

```python
result = server.call_tool("invoices.create", {"number": "A-1"}, user=request.user)
result.structured_content  # the rendered payload
```

It is built on `djangorestframework-services`' transport-neutral
`dispatch_spec` / `render_spec_output` / `enforce_permissions`, so the
spec-execution core (instance resolution, input validation, the
service / selector run, the output-selector re-fetch, queryset shaping
including `filter_set`, and the retrieve nullability contract) is shared
with the HTTP transport rather than reproduced.

It honours the binding's `argument_binding` / `unknown_arguments` policies
(mapped onto `dispatch_spec`'s) and the spec's `permission_classes` in two
layers: an upfront `enforce_permissions` call for the class-level
`has_permission` check, plus the `on_target_resolved=enforce_permissions` hook
for object-level checks on the resolved target.
It does **not** layer on the read-shaped transport extras (pagination,
ordering, a selector binding's MCP-only `input_serializer`); those stay with
the wire handlers, as do the transport-level MCP permissions / rate limits.
Chain tools are unsupported — they orchestrate several specs and raise
`TypeError`. A service raising `ServiceValidationError` / `ServiceError` and a
missing required instance come back as `isError` results; a denied permission
or malformed input raises, for the caller to map.

### Full in-process transport: `acall_tool` / `list_tools`

`call_tool` is the spec **core**. When an in-process consumer needs the *whole*
transport — exactly what a remote MCP client sees — `MCPServer` exposes two
async-friendly siblings that route through the same wire handlers:

```python
page = server.list_tools(user=request.user, request=request)  # one tools/list page
page["tools"]  # merged inputSchema per tool
page["nextCursor"]  # pass back to list_tools(cursor, ...) to paginate

result = await server.acall_tool(
    "invoices.list", {"ordering": "-amount", "page": 1}, user=request.user, request=request
)
result["structuredContent"]  # the wire's result payload (dict, not ToolResult)
```

- `list_tools(cursor=None, *, user, request=None)` returns one page of the tool
  catalog with the same merged `inputSchema` (serializer fields **plus** a
  selector tool's filter / ordering / pagination arguments and the
  `additionalProperties` policy), the same `FILTER_LISTINGS_BY_PERMISSIONS`
  per-caller filter, and the same opaque-cursor pagination the HTTP transport uses.
- `acall_tool(name, arguments=None, *, user, request=None)` invokes a tool with
  the **full** transport applied: the transport-level MCP permissions and rate
  limits, the selector post-fetch pipeline (filter / order / paginate), a selector
  binding's MCP-only `input_serializer`, chain tools, and the output format —
  everything `call_tool` deliberately omits. It returns the wire's `dict` payload
  (`content` / `structuredContent` / `isError`) or a `JsonRpcError` for a protocol
  fault (unknown tool, malformed `arguments` shape, denied permission).

Both build the call context internally from `user` + `request` (a minimal request
is synthesised when `request` is `None`). `JsonRpcError` and `JsonRpcErrorCode`
are re-exported from the package root so a consumer can branch on faults. This is
the surface the `django-ag-ui` bridge consumes to run drf-mcp tools in-process
with HTTP-equivalent semantics.

## Documenting tools

A tool's description and its argument descriptions are the entire contract a
model has to work from. Both have a channel; neither is filled in for you.

### The tool description

`description=` on any `register_*` call. There is **no docstring fallback** for
spec registration — a docstring is written for the next developer, not for a
model choosing between tools, so promoting one silently would ship prose nobody
reviewed for that audience.

Registering without one emits `UndescribedToolWarning`, and
`REST_FRAMEWORK_MCP["REQUIRE_TOOL_DESCRIPTIONS"] = True` turns that into an
`ImproperlyConfigured`. This mirrors `REQUIRE_TOOL_PERMISSIONS`: two properties
are equally required for a tool to be usable — something must gate the call, and
something must say what the call does.

### Per-argument descriptions

Do **not** restate argument meaning in the tool description. Three channels feed
`inputSchema.properties.*.description` directly:

```python
# 1. Serializer fields — `help_text` becomes the property description.
class ArchiveWidgetInput(serializers.Serializer):
    widget_id = serializers.IntegerField(
        help_text="Primary key of the widget. Not the public slug.",
    )


# 2. URL kwargs — `UrlKwarg` takes a description of its own.
server.register_selector_tool(
    name="list_loan_documents",
    spec=documents_spec,
    description="List the documents filed against a loan.",
    url_kwargs=[
        UrlKwarg(
            name="loan_pk",
            type="string",
            required=True,
            description="Primary key of the *loan*, not the borrower.",
        )
    ],
)
```

The third is drf-services' `Annotated` marker vocabulary on an
`Unpack[TypedDict]` extras key, which today carries `InputRequired` and
`NotClientInput` but no description — a key documented only that way has to fall
back to the tool description until that gap is closed upstream.

Duplicated prose is where descriptions get longest and go stale, so an argument
whose name doesn't match the entity it identifies belongs in `help_text` or
`UrlKwarg(description=…)`, written once.

## Tools vs resources

| | Tools | Resources |
| --- | --- | --- |
| MCP capability | `tools` | `resources` |
| Mutation? | Yes (services) | No (selectors) |
| Addressable? | By name (`invoices.create`) | By URI (`invoices://42`) |
| Dispatched via | `tools/call` | `resources/read` |
| Backed by | `ServiceSpec` | `SelectorSpec` |
| Schema advertised | `inputSchema` + optional `outputSchema` | `mimeType` |

Tools are imperative (the client decides when to call them and supplies
arguments). Resources are read-only and addressable by URI; they have a stable
identifier and the client can rely on the same URI returning a consistent shape
over time.

## URI templates

Resource URIs follow a small subset of [RFC 6570](https://www.rfc-editor.org/rfc/rfc6570).
Each `{var}` placeholder becomes a kwarg in the selector's signature:

```python
server.register_resource(
    name="invoice",
    uri_template="invoices://{pk}",
    selector=SelectorSpec(
        kind=SelectorKind.RETRIEVE,
        selector=get_invoice,  # def get_invoice(*, pk): ...
        output_serializer=InvoiceOutputSerializer,
    ),
)
```

Concrete URIs (no placeholders) appear in `resources/list`; templated ones
appear in `resources/templates/list` so clients can fill them in.

## Resource body encoding

A resource advertises a `mime_type` and returns a body. Those are two separate
decisions, so `encoding=` is declared rather than inferred from the mime type —
sniffing would silently change the body for anyone already advertising
something other than JSON.

```python
server.register_resource(
    name="changelog",
    uri_template="docs://changelog",
    selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=read_changelog),
    mime_type="text/markdown",
    encoding=ResourceEncoding.TEXT,
)
```

`ResourceEncoding.JSON` (the default) pretty-prints the selector's return
value. `ResourceEncoding.TEXT` returns it verbatim, which is what Markdown,
CSV, plain text and HTML need — under JSON the document would come back
wrapped in a quoted string literal instead of as itself. A `TEXT` resource's
selector must return a `str`; anything else is reported as a JSON-RPC error on
the read rather than raising through the transport.

`ResourceEncoding.BLOB` is the binary case — a PDF, an image, a generated
spreadsheet. The selector returns `bytes` and the body is base64-encoded into
the spec's `blob` field instead of `text`; the two are mutually exclusive on a
`contents` entry, so a client reads whichever is present.

```python
server.register_resource(
    name="invoice_pdf",
    uri_template="invoices://{pk}.pdf",
    selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=render_pdf),
    mime_type="application/pdf",
    encoding=ResourceEncoding.BLOB,
)
```

## Non-text tool results

A tool result's `content` array is the same content vocabulary — text, image,
audio, a link to a resource, or an embedded resource. As with resource bodies,
a binding *declares* what it returns rather than having it guessed, because a
base64 string and a text body are indistinguishable by inspection:

```python
server.register_service_tool(
    name="charts.render",
    spec=ServiceSpec(service=render_chart, atomic=False),  # returns bytes
    content_kind=ToolContentKind.IMAGE,
    content_mime_type="image/png",
)
```

`ToolContentKind.TEXT` (the default) renders JSON per the binding's
`output_format` and mirrors it in `structuredContent`. `IMAGE` and `AUDIO` take
the media itself — `bytes`, or a `str` already in base64 — and carry **no**
`structuredContent` or `outputSchema`, since neither can describe a PNG;
registering one alongside those is refused rather than ignored.

`ToolContentKind.RESOURCE_LINK` is usually the better answer for anything
large. The tool returns a mapping with `uri` and `name` (or a list of them),
each becoming a link the client can read through `resources/read` — so no bytes
ride on the tool-result path and the client fetches only what it decides it
wants. `structuredContent` is kept for this kind: the links are ordinary JSON.

```python
server.register_selector_tool(
    name="invoices.attachments",
    spec=SelectorSpec(kind=SelectorKind.LIST, selector=list_attachments),
    content_kind=ToolContentKind.RESOURCE_LINK,
)
# → [{"uri": "invoices://1.pdf", "name": "Invoice 1", "mimeType": "application/pdf"}, ...]
```

A payload that doesn't match the declared kind comes back as an `isError`
result naming the binding — the same treatment an oversized result or a missed
deadline gets, so the client always has a well-formed response to read.

## Argument completion

Clients offer autocompletion while a user fills in a prompt argument or a URI
template variable. Register a completer per argument and this server answers
`completion/complete`:

```python
server.register_prompt(
    name="code_review",
    render=review_prompt,
    arguments=[PromptArgument(name="language")],
    completions={
        "language": lambda value: Language.objects.filter(name__startswith=value).values_list(
            "name", flat=True
        )
    },
)
```

Completers are dispatched through the same kwarg-pool machinery as everything
else, so declare whichever of `value` (the text typed so far), `arguments`
(siblings the client has already resolved, also spread by name), `request` and
`user` you need. Return any iterable — a list, a generator, a queryset: the
handler slices it to the spec's cap of 100 and sets `hasMore` rather than
draining it, so a queryset reads 101 rows, not the table.

Resource templates work the same way, keyed by the `{variable}` name:

```python
server.register_resource(
    name="invoice",
    uri_template="invoices://{pk}",
    selector=SelectorSpec(kind=SelectorKind.RETRIEVE, selector=get_invoice),
    completions={"pk": recent_invoice_ids},
)
```

A completer keyed to an argument the binding doesn't have is refused at
registration — the failure mode otherwise is an empty dropdown with nothing in
the logs.

!!! warning "A completion is a read"

    Completion runs the binding's `permissions` and `rate_limits` before the
    completer. Without that, a resource a caller may not read would still
    answer "which ids exist?" one keystroke at a time.

The `completions` capability is advertised only when something is actually
completable, which is the same rule `tools`, `resources` and `prompts` follow:
a capability is a promise, and a server that declares one and then answers
`-32601` is worse off than one that never declared it.

## Icons

Tools, resources, resource templates, prompts and the server itself can carry
display icons. This package only emits them — fetching, sanitising and
rendering are the client's problem, and the spec puts a long list of MUSTs on
that side.

```python
server.register_service_tool(
    name="invoices.create",
    spec=spec,
    icons=(Icon(src="https://cdn.example.com/invoice.png", sizes=("48x48",)),),
)

MCPServer(name="billing", website_url="https://example.com", icons=(...,))
```

`src` must be `https:` or a `data:` URI — clients are required to reject
anything else, so a `http://localhost/...` icon is refused at registration
rather than shipped as an icon nobody will ever see. The server's own identity
(`title`, `description`, `websiteUrl`, `icons`) can also come from the
`SERVER_INFO` setting.

## Streaming progress

A long-running tool can report how far it has got, and the client sees it as it
happens. Declare `progress` on the service or selector and call it:

```python
def export_invoices(*, data, progress):
    rows = list(build_rows(data))
    for index, row in enumerate(rows):
        write(row)
        progress(
            index + 1, total=len(rows), message="writing rows", meta={"com.example/file": data.path}
        )
```

Nothing about the registration changes — `progress` is a kwarg-pool seed from
`djangorestframework-services`, so it arrives like `request` and `user` do, and
the same service runs unchanged over HTTP where nobody is listening.

**The client opts in** by putting a `progressToken` in the request's `_meta`:

```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/call",
 "params": {"name": "invoices.export", "arguments": {},
            "_meta": {"progressToken": "abc123"}}}
```

That token is the *only* trigger. With it, the response becomes
`text/event-stream` carrying `notifications/progress` frames followed by the
result; without it, a single JSON object as before — a stream whose only event
is the final response costs a connection and buys nothing.

⭐ **Era-independent.** `_meta.progressToken` sits in the same place in
`2025-11-25` and `2026-07-28`, so a legacy client gets streamed progress on
exactly the same terms as a modern one.

⚠ **ASGI only, for *streamed* progress.** A sync WSGI view cannot yield while
its dispatch is still running, so `server.urls` keeps answering
`application/json`. That stays spec-legal — a single JSON object is always
permitted. Use `server.async_urls` if you want progress on the wire mid-call.

The same service reports fine under WSGI when it runs as a
[task](#progress-from-inside-a-task): there the reports go to the task record
rather than to a stream, and the client polls for them.

### What the server does with your reports

| | |
|---|---|
| `progress` must increase | A non-increasing report is **dropped**. The spec makes increase a MUST, so forwarding one would put this server in violation on the service's behalf. |
| Frames are capped | `MAX_PROGRESS_NOTIFICATIONS` (default 1000) per request. The spec asks both parties to rate-limit; a per-row reporter over a large table is a flood. Past the cap reports are dropped — **the dispatch is untouched and the result still arrives**. |
| `meta` rides in `_meta` | Structured detail goes where the protocol puts extension data, so `message` stays prose. Namespace your keys. |
| Closing the stream cancels | Client disconnect *is* the cancellation signal in `2026-07-28`. ⚠ It cancels the await, not the work — a thread parked in a driver's socket read is not interruptible by asyncio, the same caveat `DISPATCH_TIMEOUT` carries. |

!!! warning "Permissions are checked before the stream opens"

    A streaming response commits its HTTP status before the handler runs, so a
    permission denial found inside could only ride as an in-stream error inside
    a `200` — losing the `403` the authorization spec makes normative. The
    permission stack therefore runs once at the transport before the stream is
    opened, so a denied call still gets `403` and a `WWW-Authenticate`
    challenge. Rate limits stay inside the handler: consuming one is not
    idempotent, and a rate-limit rejection was already a `200` with the detail
    in the body.

## Server-pushed notifications: subscriptions

A client that wants to know when something changes opens `subscriptions/listen`
— a POST whose response stream stays open — and names exactly what it wants:

```json
{"method": "subscriptions/listen",
 "params": {"notifications": {
     "resourceSubscriptions": ["invoices://42"],
     "toolsListChanged": true}}}
```

⚠ **Every type is opt-in, and that is a MUST**: the server must not send a
notification type the client did not ask for. So an absent field is a refusal,
not a default — and a subscription that was granted *nothing* is acknowledged
and then **closed**, rather than held open to deliver silence.

The first frame is always `notifications/subscriptions/acknowledged`, carrying
the subset the server **agreed to** honour. That is where a client learns it
will not get something — a resource it may not read, a `promptsListChanged` on a
server with no prompts — instead of waiting for an event that was never coming.
Every frame after it carries the subscription id in `_meta`, so one client can
run several subscriptions and tell them apart.

### Wiring it up

```python
from redis.asyncio import Redis
from rest_framework_mcp.subscriptions.redis_subscription_broker import (
    RedisSubscriptionBroker,
)

server = MCPServer(
    name="invoices",
    subscription_broker=RedisSubscriptionBroker(Redis.from_url("redis://…")),
)
```

There is **no default broker**, deliberately. A server that quietly got an
in-process one would advertise support and then deliver nothing the moment a
second worker existed.

!!! danger "The in-memory broker is single-worker only"

    `InMemorySubscriptionBroker` is for development and tests. The write that
    triggers a notification lands on whichever worker served that request, and
    the subscriber's stream is parked on a different one — so past one worker it
    delivers to nobody, and the failure looks exactly like "the resource never
    changed". `RedisSubscriptionBroker` (in the `[redis]` extra) is the
    deployable one.

⚠ **A subscription occupies a worker for as long as it is open.** One parked
ASGI task per subscriber — inherent to the wire format, since this method exists
to replace the old GET endpoint. Two settings bound it:

| | |
|---|---|
| `SUBSCRIPTION_MAX_SECONDS` (1 h) | The server closes the stream gracefully and the client re-subscribes. ⚠ Also the **re-authorization interval**: a subscription's permissions are checked once, when it opens, so this is what stops a principal whose access was revoked from receiving change signals indefinitely. |
| `MAX_CONCURRENT_SUBSCRIPTIONS` (100) | Per worker. Without it an authenticated caller can exhaust the pool by opening streams in a loop. Past the cap a new subscription is refused with `503` / `-32603` rather than queued. |

### Watching a task instead of polling it

A client that holds a task id can subscribe to it and stop calling `tasks/get`:

```json
{"method": "subscriptions/listen",
 "params": {"notifications": {"taskIds": ["786512e2-…"]}}}
```

Every status change pushes `notifications/tasks` carrying the **whole task** —
identical to what `tasks/get` would have returned at that moment — so a missed
notification costs nothing and polling stays genuinely optional rather than a
fallback you have to implement anyway.

A task is watchable only by the principal that created it: its status is as
revealing as its result, since knowing someone else's export finished is knowing
they ran one.

⛔ **One exception to "refused entries are dropped":** a client asking for
`taskIds` without declaring the `io.modelcontextprotocol/tasks` extension gets a
JSON-RPC error, not a quiet omission. The spec requires it, and it is not an
existence oracle — the error turns on what the *client* declared, not on
anything about the tasks it named.

⛔ `notifications/progress` and `notifications/message` are **MUST NOT** on this
stream. Progress for a task is the task's own status; the progress channel
belongs to the request-scoped stream, which a task by definition outlived.

### Publishing a change

**Declare it on the tool that does the writing** — the server is already in the
call path, so this is the trigger that needs no discipline to remember:

```python
server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(service=create_invoice),
    invalidates=("invoices://{pk}", "invoices://"),
)
```

Templates use the same `{var}` syntax as a resource's `uri_template`, rendered
against the result merged with the call's arguments (**result wins** — after a
write it is authoritative; the arguments cover a delete, whose result carries
nothing). Publishing happens **after the transaction commits**, and a call that
came back `isError` publishes nothing.

⚠ **Its boundary is real, and is why the explicit trigger exists too.** It fires
for calls that go through this server and for nothing else — a management
command, a Celery job or an admin edit changes the same rows and announces
nothing. For those:

```python
from django.db import transaction

transaction.on_commit(
    lambda: async_to_sync(server.notify_resource_updated)(f"invoices://{invoice.pk}")
)
```

⚠ **Publish after the transaction commits.** Inside `transaction.atomic()` you
would be announcing a change that may still roll back, and a subscriber that
re-reads immediately sees the old value — worse than no notification at all.
`invalidates=` does this for you; `notify_resource_updated` is yours to place.

⚠ **Matching is exact, not by prefix.** Publish the concrete URI *and* the
collection URI if you want watchers of the collection to hear about it. A prefix
rule would match `invoices://1` against `invoices://11` and would miss a
tenant-scoped scheme entirely, so the publisher says what it means instead.

Calling `notify_resource_updated` on a server with no broker is a no-op rather
than an error, which is what makes it safe to call unconditionally from a
service.

### Who may watch what

Subscribing to a resource requires the same permission as reading it. Otherwise
a subscription would be a side channel around `resources/read`: a caller denied
the body could still learn every time it changed, and *when* something changes
is often the more sensitive signal. A task may be watched only by the principal
that created it.

A refused entry is **dropped from the acknowledgement**, not turned into an
error — matching the spec's own handling of unsupported types, and avoiding an
oracle that would let a caller tell "no such resource" from "not yours".

## Long-running work: tasks

Streaming progress keeps the client informed while it waits. **Tasks remove the
waiting.** A task-eligible tool answers `tools/call` with a durable handle
instead of a result; the work happens in a queue worker, and the client polls
`tasks/get` until the status is terminal.

This is the `io.modelcontextprotocol/tasks` extension, and it is **modern-era
only** — the client declares support on every request, which a legacy client has
no way to do.

### Wiring it up

Two pieces. An **executor** says where work goes, and a **store** is where tasks
live between the request that created one and the worker that finishes it:

```python
from celery import shared_task


@shared_task
def run_mcp_task(task_id: str) -> None:
    server.run_task(task_id)


class CeleryExecutor:
    def enqueue(self, task_id: str) -> None:
        run_mcp_task.delay(task_id)


server = MCPServer(name="invoices", task_executor=CeleryExecutor())
```

That is the whole seam: one method taking one string. Nothing in this package
imports Celery, and an RQ or Dramatiq call, or a `ThreadPoolExecutor.submit` in
a test, satisfies it just as well. Passing `task_executor=` also builds a
`DjangoCacheTaskStore` namespaced to the server, so the common case is one
argument; pass `task_store=` to override it.

!!! danger "`InMemoryTaskStore` is not deployable"

    Unlike an in-memory *session* store, which is merely restart-fragile, an
    in-memory *task* store is broken by design: the web worker that creates a
    task and the worker that finishes it are different processes. The result is
    written into a dict the web process cannot see, and every poll answers
    "unknown task" until the client gives up. It fails silently and looks like a
    hung job. Use it for tests and a single-process `runserver`.

### Opting a tool in

The extension makes the **server** the sole decider — a client cannot ask for a
task — so the choice lives on the binding, next to every other per-tool knob:

```python
server.register_service_tool(
    name="reports.generate",
    spec=ServiceSpec(service=generate_report),
    task_policy=TaskPolicy.OPTIONAL,
)
```

| `task_policy` | A client that declared the extension | A client that did not |
|---|---|---|
| `FORBIDDEN` (default) | runs inline | runs inline |
| `OPTIONAL` | gets a task handle | runs inline |
| `REQUIRED` | gets a task handle | `-32021`, missing capability |

`FORBIDDEN` is the default, so **every tool registered before this existed
behaves exactly as it did**. Reach for `OPTIONAL` when a tool is slow but can
still finish inside a request, and `REQUIRED` when it genuinely cannot — where
running it inline would only hit the dispatch deadline.

### What a client sees

```json
{"resultType": "task", "taskId": "…", "status": "working",
 "ttlMs": 86400000, "pollIntervalMs": 5000}
```

Then `tasks/get` until `completed` / `failed` / `cancelled`, honouring
`pollIntervalMs`. `tasks/cancel` signals intent to stop; `tasks/update` answers
a task that is parked on `input_required`.

⚠ **`Mcp-Name` must carry the `taskId`** on all three methods — the extension
requires it so a gateway can route a follow-up to the instance holding the
task's state. A mismatch is `-32020`, exactly as for `tools/call`.

### Progress from inside a task

The same `progress` seed works, and the service body is unchanged:

```python
def export_invoices(*, data, progress):
    for n, row in enumerate(rows, 1):
        progress(n, total=len(rows), message="Exporting")
```

Run inline, that streams `notifications/progress`. Run as a task, there is no
connection to stream on — so the reports land on the task record instead, and
the client reads them from the `statusMessage` it was already polling:

```json
{"taskId": "…", "status": "working", "statusMessage": "Exporting (142/500)"}
```

⭐ **This is what makes `progress` worth declaring at all under the task
model.** Without it the seed is live-connection-only, and the operations most
in need of progress — the ones promoted to tasks *because* they run long — are
exactly the ones it would silently do nothing for. Nothing about the service,
the spec, or the registration distinguishes the two paths.

| | |
|---|---|
| `statusMessage` is the whole channel | The protocol `Task` has no numeric field. `progress` / `total` are kept on the record for logs and admin visibility, but a polling client only ever receives the rendered string. |
| `meta` is dropped | On the inline path it rides in the notification's `_meta`. A task has no notification and no free-form slot, so the argument is accepted and discarded rather than given a home no client could read. |
| No `notifications/tasks` per tick | That notification means *the status changed*; progress is movement inside `working`. Subscribers would get a firehose describing a task that has not moved. Poll at `pollIntervalMs`. |
| A finished task is never rewritten | A late report from a worker still unwinding cannot move `lastUpdatedAt` on a completed task or make a cancel look like it did not take. |
| A store that is down does not fail the work | The report describes the operation; it is not the operation. Write failures are swallowed. |

### Things worth knowing

| | |
|---|---|
| Permissions run **twice** | Once before the task is created, so a denied call never reaches the queue and still gets its `403`; once again in the worker, against the same rebuilt token. The task stores the caller's scopes for exactly this reason. |
| Rate limits run **once** | Consuming a quota is a side effect, so it is charged when the client asks and not again on replay. Charging both would halve every configured limit. |
| A tool error is `completed`, not `failed` | The spec is explicit. A `ServiceError` produces a well-formed result carrying `isError: true`, and that is a task that finished. `failed` means the task machinery broke. |
| There is no `tasks/list` | Deliberate in the spec: without sessions there is nothing to scope a listing by. Ids are high-entropy and ownership is checked on top — an id belonging to someone else answers exactly as one that never existed. |
| A queue that is down fails the *task* | The record is already durable when `enqueue` raises, so the handle comes back with `status: "failed"` and the reason in `statusMessage`. The client finds out through the channel it was already using. |
| Redelivery runs the work once | Queues deliver at least once; a task is claimed as the worker starts. Best-effort, not a lock — an idempotent service is still the right thing to write. |

## Asking the user: elicitation

Some calls cannot be decided by the arguments alone. "Delete everything matching
this filter" is safe at three rows and alarming at nine thousand, and the service
only finds out after it has looked.

A service says so by raising, and that is the whole of its involvement:

```python
from rest_framework_services import AdditionalInputRequired


def delete_rows(*, data):
    doomed = rows_matching(data)
    if len(doomed) > 100 and not data["confirmed"]:
        raise AdditionalInputRequired(
            f"{len(doomed)} rows match. Confirm to proceed.",
            schema={"confirmed": {"type": "boolean"}},
        )
    ...
```

`confirmed` is an ordinary field on the tool's input serializer. The server turns
the raise into a question, the client puts it to the user, and the answer arrives
back as that ordinary field — so the service reads it exactly as it would read
anything else a caller sent.

### What goes over the wire

```json
{"resultType": "input_required",
 "inputRequests": {"additionalInput": {
   "method": "elicitation/create",
   "params": {"mode": "form",
              "message": "9412 rows match. Confirm to proceed.",
              "requestedSchema": {"type": "object",
                                  "properties": {"confirmed": {"type": "boolean"}},
                                  "required": ["confirmed"]}}}},
 "requestState": "…"}
```

⚠ **This is a success, not an error.** `input_required` is a second legal shape
for a `tools/call` result, inside a `200`, and a client that treats a non-`complete`
`resultType` as a failure will never retry.

The client collects the input and **retries the original call** — a new request,
a new JSON-RPC id — carrying `inputResponses` and the `requestState` verbatim:

```json
{"name": "rows.delete", "arguments": {"count": 9412},
 "inputResponses": {"additionalInput": {"action": "accept",
                                        "content": {"confirmed": true}}},
 "requestState": "…"}
```

⭐ **Nothing is held between the two requests.** That is the point of the pattern
— it replaced server-initiated requests precisely so the retry can land on a
different process, behind a load balancer that knows nothing about the first one.
The service is not resumed; it **runs again from the top**, with the answer
present. A service that did irreversible non-transactional work before raising
will do it twice, which is a reason to raise early and to keep `atomic=True`.

### `requestState` is attacker-controlled

It leaves the server, passes through the client, and comes back. So it is signed
(HMAC, via `django.core.signing`) and carries three things that are checked
before any of it is believed:

| Bound to | Rejects |
|---|---|
| the authenticated principal | a token that leaked and is presented by someone else |
| a digest of the original call | a confirmation given for a harmless call, replayed onto a destructive one |
| a timestamp (`INPUT_REQUEST_TTL_SECONDS`) | anything captured from a log or a proxy and used later |

All four failures — including a bad signature — answer identically: the state is
ignored and the user is asked again. Distinguishing them would turn the endpoint
into an oracle, and an honest client cannot use the distinction anyway, since it
is forbidden from looking inside the token.

Signed is not encrypted: a client can decode it. What is in there is the caller's
own principal id, a digest of the caller's own request, and the answers the user
at that client just typed. Form mode is documented for **non-sensitive** values
for exactly this reason — do not ask for a password.

### More than one question

`requestState` accumulates the answers, so a service that wants a confirmation
and then a reason works: the client sends only the latest round's response, and
the earlier answers ride in the token. `MAX_INPUT_ROUNDS` bounds it, so a service
whose condition an answer never clears fails instead of volleying the same
question at a user forever.

### Clients that cannot be asked

The spec forbids sending an elicitation to a client that did not declare the
capability. Rather than a protocol error, such a call gets an ordinary `isError`
result carrying the service's message **and the schema**:

```json
{"error": {"type": "input_required",
           "message": "9412 rows match. Confirm to proceed.",
           "requestedInput": {"confirmed": {"type": "boolean"}}}}
```

A model reading that can simply pass `confirmed: true` on its next call, which
is the same outcome by a shorter route. This is what a legacy-era client sees, a
URL-only client, and a task worker replaying a call with nobody at the other end.

### Boundaries

| | |
|---|---|
| Service tools only | ⛔ A **chain** tool degrades instead of asking. MRTR completes a call by re-running it, and a chain that asked at step three would run steps one and two twice on the retry. A **selector** is a read — one that needs the user to decide something is a tool wearing the wrong registration. |
| Form mode only | The spec's other mode hands the user a URL to complete out of band. Nothing here knows how to mint one; a service that needs it has a redirect to build, not a schema to declare. |
| Top-level fields only | `requestedSchema` is a restricted subset: strings, numbers, booleans and enums, no nesting. A schema outside it raises `ImproperlyConfigured` at the moment it would have been sent, rather than shipping something the client must reject. |
| `tools/call` only | The spec also permits `input_required` on `prompts/get` and `resources/read`. Neither is implemented: both dispatch a bare callable with no failure channel, and a prompt render or resource read that needs to stop and ask is not a shape this package has a caller for. |
| Sampling and roots | ⛔ Not built. Both are **Deprecated** as of `2026-07-28`; elicitation is the only input worth asking for. |

## Protocol eras

This server speaks two revisions of MCP at once, on one endpoint, and picks
which by reading the request:

| | Legacy (`2025-11-25`, `2025-06-18`) | Modern (`2026-07-28`) |
|---|---|---|
| Opens with | `initialize` | nothing — every request stands alone |
| State | a session, in `Mcp-Session-Id` | none |
| Version | negotiated once | declared per request |
| Detected by | absence of the marker below | `_meta["io.modelcontextprotocol/protocolVersion"]` |
| Push notifications | none | `subscriptions/listen` |
| Tasks, elicitation | not offered | available |

A modern request carries its version, client identity and client capabilities
in `params._meta`, and mirrors selected body fields into headers so gateways
can route without parsing JSON:

```http
POST /mcp/ HTTP/1.1
Mcp-Protocol-Version: 2026-07-28
Mcp-Method: tools/call
Mcp-Name: invoices.create
```

Those headers are **validated against the body**. A mismatch is `400` with
JSON-RPC `-32020` — not pedantry: a load balancer that routes on the header
while the server executes the body is a confused deputy, and the check is what
closes it. `Mcp-Name` may arrive Base64-wrapped (`=?base64?…?=`) when the value
would not survive as a plain ASCII header; it is decoded before comparison.

Two more modern-only status codes matter, because clients use them to work out
which era they are talking to: an unknown method is `404` with a `-32601` body
(the body is what distinguishes this endpoint from a server that does not host
one), and an unsupported version is `400` with `-32022` listing what *is*
supported. `GET` and `DELETE` return `405` to a caller naming a modern
revision — the SSE stream and session termination were both removed there.

!!! note "Keeping legacy is a deliberate choice"

    Legacy clients have no fall-forward mechanism: drop the era and every
    client that has not migrated is stranded with nothing but an error string.
    The cost of carrying both is one branch at the transport edge — everything
    below it is era-agnostic, with two exceptions.

    **`resources/read`** answers a missing URI with `-32002` for a legacy caller
    and `-32602` for a modern one, because the revision that retired `-32002`
    also told clients to keep recognising it, so neither value is safe to send
    to both.

    **The advertised capabilities** follow the caller, not the server. A
    capability is a promise, and two of them can only be kept for a modern
    client: every push flag (`subscribe`, and the three `listChanged` fields)
    describes a notification that leaves through `subscriptions/listen`, and
    `extensions` is not a field on the legacy `ServerCapabilities` at all. So a
    legacy `initialize` is told about neither, however the server is configured
    — and `server/discover`, which both eras may call, answers according to the
    version the caller declared.

    ⛔ **`resources/subscribe` is deliberately not implemented.** It is optional
    in `2025-11-25` and gone from `2026-07-28`, where the schema says
    `SubscriptionFilter.resourceSubscriptions` *"replaces the former `resources/subscribe`
    RPC"* — which this server does implement. Building the legacy RPC would mean
    a cross-process session→URI registry serving only the era being carried for
    compatibility rather than grown.

## Interactive views (MCP Apps)

A tool can declare an HTML view that an MCP **host** renders inline in the
chat, under the [MCP Apps](https://github.com/modelcontextprotocol/ext-apps)
extension. It layers over the base protocol this package already speaks, so
there is no protocol bump and no transport change.

The host/server split is the whole shape of it. This package **declares**:

```python
server.register_ui_resource(
    name="invoices_table",
    uri="ui://invoices/table.html",
    template_name="mcp/invoices_table.html",
    ui=UIResourceMeta(
        csp=UICsp(connect_domains=["https://api.example.com"]),
        prefers_border=True,
    ),
)
```

The **host** renders: it builds the sandboxed iframe, constructs and enforces
the CSP from what you declared, and runs the `ui/*` postMessage bridge. None of
that is implemented here, and none of it should be.

A view is an ordinary resource — one URI namespace with your data resources,
listed in `resources/list`, and guardable with `permissions=` — with three
things fixed for you: the `text/html;profile=mcp-app` mime type, `TEXT` body
encoding, and a `_meta` bundle under the extension's key. Give it exactly one
content source: `template_name=` (a Django template, the idiomatic choice),
`html=` (a literal document), or `selector=` (a zero-argument callable).

Views are **unguarded by default**. The MCP session is already authenticated,
a view is a static asset rather than tenant data, and hosts may prefetch one
before any tool call. Pass `permissions=` if your project wants otherwise.

!!! warning "Keep tenant data out of the view"

    Hosts may prefetch and cache a view, so it is a *shell* that hydrates
    itself at runtime from tool results — which is why the template renders
    with no context. This is a house rule, not a spec rule, and it is the one
    thing a Django author's instinct gets wrong: rendering the queryset into
    the template is normally the right answer, and here it would leak data
    across the host's cache.

A tool then points at the view, and the host renders that tool's result inside
it instead of showing raw JSON:

```python
server.register_selector_tool(
    name="list_invoices",
    spec=list_invoices_spec,
    ui=UIToolMeta(resource_uri="ui://invoices/table.html"),
)
```

The render payload is the `structuredContent` the tool **already emits** — no
second serialisation path — and a `tools/call` the view makes comes back
through the ordinary endpoint, inheriting your auth, `MCPPermission`s and rate
limits unchanged.

Three ways a link can be wrong all fail the same way at runtime — a view that
silently never renders — so all three are refused at registration:

| Mistake | Why it's caught |
| --- | --- |
| `resource_uri` names no view on this server | The host resolves it against the same server, so a typo reaches it as a dangling reference. Register the view **before** the tool that links to it. |
| The tool has `include_structured_content=False` | That *is* the render payload; the view would come up blank. Checked against the effective value, so a project that turned it off globally is caught too. |
| Both `ui=` and a `"ui"` key in `meta=` | Both write the same `_meta` key, so one would quietly overwrite the other. |

`visibility` declares who may call the tool — `UIVisibility.MODEL`, `APP`, or
both. It is **host-enforced**: a host is required not to offer the model a tool
whose visibility omits `MODEL`, which makes an `APP`-only tool a useful shape
for a fine-grained operation that exists to serve the view rather than the
conversation. This server declares the field and does not filter `tools/list`
on it — a client that doesn't implement the extension wouldn't honour the rule
anyway.

A client advertises Apps support as `capabilities.extensions` on `initialize`,
which is parsed onto `ClientCapabilities.extensions`. **Advertisement is
one-directional, client → server** — the spec defines no matching server
capability, so nothing is sent back, and `_meta.ui` is emitted unconditionally.
Unknown `_meta` keys are ignorable by design, so a client that doesn't
implement Apps is unaffected.

## Dispatch flow

The MCP package owns its own dispatch flow. It does **not** import
`_execute_mutation` or anything under `rest_framework_services.viewsets`.

`tools/call`:

1. Look up the `ToolBinding` by name; reject unknown.
2. Evaluate per-binding `MCPPermission` classes (AND-combined). Denial → 403
   with `WWW-Authenticate` carrying any required scopes.
3. If `spec.instance_selector_spec` is set (sister-repo 0.16), resolve
   the mutation target first: the nested RETRIEVE selector runs against
   `{request, user}` + the raw arguments (the MCP analogue of URL kwargs)
   + the nested spec's own `kwargs` provider; queryset shaping applies
   and a QuerySet return is materialized via `.first()`. A missing row
   short-circuits to an `isError: true` tool result (`type: "not_found"`).
4. Validate `arguments` via `spec.input_serializer` (DRF `Serializer`,
   bare `@dataclass` auto-wrapped in `DataclassSerializer`, or `None`).
   `spec.partial=True` validates partially (and drops `required` from the
   advertised `inputSchema`); the resolved instance is threaded into the
   serializer DRF-style so instance-dependent `validate()` sees
   `self.instance`.
5. Build a kwarg pool: `{request, user, data}` plus — when present — the
   resolved `instance` and the bound, validated `serializer` (both
   reserved seeds clients cannot poison; services opt in by declaring
   the parameter, e.g. to call `serializer.save()`).
6. `resolve_callable_kwargs(spec.service, pool)` →
   `run_service(spec.service, kwargs, atomic=spec.atomic)`.
7. Map failures along the MCP protocol-vs-tool boundary. The serializer
   rejecting the arguments *shape* stays a JSON-RPC `-32602`. A service
   raising on well-shaped input — `ServiceValidationError` or
   `ServiceError` — returns an **`isError: true` tool result** the model
   can read and self-correct from, with a JSON `{"error": {"type":
   "validation_error" | "service_error", "message": ..., "detail": ...}}`
   payload in `content[0]` (and no `structuredContent`, which is tied to
   the success schema). Chain steps add `failedStep`. Setting
   `REST_FRAMEWORK_MCP["INCLUDE_VALIDATION_VALUE"] = True` additionally
   echoes the offending `arguments` dict back under `value` — handy for
   debugging schema mismatches against opaque client SDKs, off by default
   because the dict can carry sensitive payloads.
8. If `spec.output_selector_spec` is set, run its post-call pipeline:
   optionally re-fetch via `output_selector_spec.selector` (same
   kwarg-pool dispatch), then render through
   `output_selector_spec.output_serializer` with `many=` driven by
   `output_selector_spec.kind`. If `output_selector_spec` is `None`,
   the service's return value is passed through unchanged.
9. Wrap as a `ToolResult` with `OutputFormat`-driven encoding for the human-
   readable `content[0]` block. `structuredContent` is always JSON.

RETRIEVE selector tools mirror the sister repo's read semantics: a
QuerySet return is materialized via `.first()`, and a missing row is a
`not_found` `isError` result — unless the spec sets `allow_none=True`
(the nullable-resource contract), which renders a successful `null`
result instead. LIST tools advertise a kind-aware `outputSchema`: a bare
array schema unpaginated, the `{items, page, totalPages, hasNext}`
envelope with `paginate=True` (enable pagination for a fully
spec-compliant *object*-shaped `structuredContent`).

`resources/read`:

1. Resolve URI through `ResourceRegistry` (returns binding + URI-template
   variables).
2. Permission check.
3. Build kwarg pool: `{request, user, **uri_vars}`.
4. `resolve_callable_kwargs(selector, pool)` → `run_selector(...)`.
5. Render through `binding.output_serializer` if set, then JSON-encode.

## Sessions, headers, origins

The MCP 2025-11-25 transport requires:

- **`MCP-Protocol-Version`** — the version the client speaks. Validated against
  `REST_FRAMEWORK_MCP["PROTOCOL_VERSIONS"]`. Missing → 400 except on
  `initialize`, which is allowed to omit it for the initial handshake. Some
  clients omit the header on every request; set
  `REST_FRAMEWORK_MCP["REQUIRE_PROTOCOL_VERSION_HEADER"] = False` to accept
  those by falling back to the first supported version. A present-but-
  unsupported version is still rejected either way.
- **`MCP-Session-Id`** — issued by the server in the response to `initialize`.
  Required on every subsequent call. Unknown id → 404 (forces the client to
  re-initialize). Since 0.7 every session is **bound to the authenticated
  principal** that initialized it: a session presented by a different
  principal renders the same 404 as an unknown id (deliberately
  indistinguishable, so ownership cannot be probed). Sessions are stored in
  a pluggable [`SessionStore`](reference/registries.md) — by default the
  Django cache.
- **`Origin`** — strict allowlist. Empty allowlist means "no cross-origin
  requests"; an empty `Origin` header is treated as same-origin and allowed.
  Configure via `REST_FRAMEWORK_MCP["ALLOWED_ORIGINS"]`. Use `["*"]` only for
  dev.

All three verbs authenticate through the configured `MCPAuthBackend`
**before** any session lookup, so an unauthenticated caller always sees
401 — session validity is never revealed without a credential.

`DELETE /mcp/` with a session id terminates that session immediately —
only for the principal that owns it. `GET /mcp/` opens a server-initiated
SSE stream for the caller's own session — available on `async_urls` only
(WSGI's `server.urls` returns 405 on GET because SSE requires the event
loop). See [Async deployment](async.md) for the wire details and
`MCPServer.notify(...)` for pushing frames.

## Output formats

Per the MCP tools spec, a tool result has both a `content` block list and an
optional `structuredContent`:

- `structuredContent` is always JSON-shaped — clients parse it directly.
- `content[0]` is a text block whose payload is encoded per `OutputFormat`.

```python
from rest_framework_mcp import OutputFormat

server.register_service_tool(
    name="invoices.list",
    spec=ServiceSpec(
        service=list_invoices,
        output_selector_spec=SelectorSpec(
            kind=SelectorKind.LIST,
            output_serializer=InvoiceOutputSerializer,
        ),
    ),
    output_format=OutputFormat.AUTO,  # JSON, TOON, or AUTO
)
```

`AUTO` picks per-payload — TOON for uniform list-of-objects, JSON otherwise.
TOON is wrapped in a fenced ` ```toon ` block with a leading `# format: toon`
marker so clients that don't parse it natively can still render it.

If TOON is requested but the optional extra is missing, the encoder falls back
to JSON with a `warnings.warn` — a tool call never fails because an optional
extra is absent.

### Omitting `structuredContent` and `outputSchema`

`structuredContent` and `outputSchema` are independently toggleable. The MCP
spec (2025-06-18, SEP-1624) imposes one asymmetric rule: a tool that
advertises `outputSchema` **must** return conforming `structuredContent`. The
reverse — emitting `structuredContent` without an `outputSchema` — is
allowed.

Two server-wide settings, both default `True`:

- `REST_FRAMEWORK_MCP["INCLUDE_STRUCTURED_CONTENT"]` — gates the
  `structuredContent` field on `tools/call` results.
- `REST_FRAMEWORK_MCP["INCLUDE_OUTPUT_SCHEMA"]` — gates the `outputSchema`
  field on `tools/list` entries.

Per-tool overrides mirror them: `include_structured_content` and
`include_output_schema` on `register_service_tool`, `register_selector_tool`,
or their decorator forms. Each is tri-state — `None` (default) inherits the
global, `True`/`False` force the behaviour regardless of the setting.

Common patterns:

- **Default**: both `True`. Tools advertise their schema and return matching
  structured content. Spec-compliant and easiest for typed clients.
- **Drop only `outputSchema`**: useful when the schema bloats `tools/list`
  responses but you still want machine-parsable results. Set
  `INCLUDE_OUTPUT_SCHEMA=False`; leave `INCLUDE_STRUCTURED_CONTENT=True`.
- **Drop both**: useful when a downstream client echoes both fields back to
  the LLM (doubling token usage) or chokes on `structuredContent`. Set both
  to `False`. The text payload in `content[0]` still carries the full result
  (JSON-encoded by default, or TOON when requested).

The fourth combination — advertising `outputSchema` while suppressing
`structuredContent` — violates the spec. It is rejected with
`ImproperlyConfigured` at construction time (for explicit per-binding
conflicts) or at request time (for setting-level conflicts), so the misconfig
surfaces immediately rather than producing a non-compliant response.

## Auth model

Two pluggable surfaces:

- **Backend** (`MCPAuthBackend` Protocol). Authenticates a request and produces
  a `TokenInfo`. The transport calls `authenticate(request)` on every call;
  returning `None` produces a spec-mandated 401 with a `WWW-Authenticate`
  header built from `www_authenticate_challenge(...)`. The
  `/.well-known/oauth-protected-resource` view delegates its payload to the
  backend's `protected_resource_metadata()`.
- **Permissions** (`MCPPermission` Protocol). DRF-style classes attached to a
  binding (`permissions=[ScopeRequired(["invoices:write"])]`). Evaluated after
  authentication; AND-combined; required scopes from any denying class are
  surfaced in `WWW-Authenticate`.

[Authentication](auth.md) walks through the full picture, including the
django-oauth-toolkit recipe and audience binding.
