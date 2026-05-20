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
from rest_framework_mcp import ServiceSpec  # re-exported for ergonomics

spec = ServiceSpec(
    service=create_invoice,
    input_serializer=InvoiceInputSerializer,
    output_serializer=InvoiceOutputSerializer,
    output_selector=None,   # optional post-call shaping
    atomic=True,            # wrap dispatch in transaction.atomic()
    success_status=None,    # ignored by MCP — used by HTTP
    kwargs=None,            # optional per-spec kwargs provider; see below
)
```

This means a project that uses neither `ServiceViewSet` nor DRF routers can
still expose its services over MCP. The HTTP and MCP transports are siblings,
not layers — neither owns the other.

### What `ServiceSpec` / `SelectorSpec` carries through to MCP

Sister-repo (`djangorestframework-services`) 0.12.0 extended the spec
dataclasses with three fields the MCP layer now honors automatically —
register a spec once and both transports get the same shape:

- **`permission_classes`** — DRF `BasePermission` classes. Auto-wrapped
  with `DRFPermissionAdapter` and prepended to the per-binding
  `permissions` tuple, so spec-declared permissions run before any
  tool-level `MCPPermission` you add at the MCP call site.
- **`SelectorSpec` queryset shaping** — `select_related`,
  `prefetch_related`, `annotations`, and `extend_queryset` are applied
  before the FilterSet / ordering / pagination pipeline. Non-queryset
  returns (lists, scalars) pass through unchanged.
- **Serializer context** — `input_serializer_context` /
  `output_serializer_context` (on `ServiceSpec`) and
  `output_serializer_context` (on `SelectorSpec`) are invoked with the
  synthesised view + DRF request and forwarded as `context=` to the
  serializer constructor on both sync and async dispatch paths.

### Per-spec kwargs providers

`ServiceSpec.kwargs` (and `SelectorSpec.kwargs`) is a callable that returns
extra kwargs to merge into the dispatch pool — useful for plumbing per-tenant
context, signed lookups, etc. without scattering `request.user.*` reads
across services.

```python
from rest_framework_mcp import MCPServiceView, ServiceSpec


def with_tenant(view: MCPServiceView, request) -> dict:
    return {"tenant_id": request.user.tenant_id}


server.register_service_tool(
    name="invoices.create",
    spec=ServiceSpec(
        service=create_invoice,
        input_serializer=InvoiceInputSerializer,
        output_serializer=InvoiceOutputSerializer,
        kwargs=with_tenant,
    ),
)
```

The provider receives an :class:`MCPServiceView` (synthesised because MCP
has no DRF view) — `view.action` is the binding name, and on resource reads
`view.kwargs` carries the URI-template variables. Same wire shape as the
HTTP transport's `ServiceView`, so providers can be shared between
transports.

### `SelectorSpec` for resources

`register_resource(selector=...)` requires a
[`SelectorSpec`](https://github.com/Artui/djangorestframework-services/blob/main/rest_framework_services/types/selector_spec.py),
mirroring `register_service_tool(spec=ServiceSpec(...))`. The unit of registration
is a spec on both surfaces.

- `.selector` is the callable that gets dispatched (must be set; specs with
  `selector=None` are rejected).
- `.output_serializer` fills in when the caller didn't pass one explicitly.
- `.kwargs` becomes the binding's per-request kwargs provider.

```python
from rest_framework_mcp import SelectorSpec

server.register_resource(
    name="invoice",
    uri_template="invoices://{pk}",
    selector=SelectorSpec(
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
  callable's kwarg pool.
  - `ArgumentBinding.DATA_ONLY` (default for service tools) — only
    `data=<validated>` enters the pool.
  - `ArgumentBinding.MERGE` (default for selector tools) — every key from
    the validated arguments is spread into the pool as a top-level kwarg,
    so selectors can declare individual parameters
    (`def list_drafts(*, project_id, page=1)`). `spec.kwargs(...)` wins
    on conflict so author-declared invariants beat client input.
  - `ArgumentBinding.REPLACE` — like `MERGE` but the spread wins on
    conflict, so `spec.kwargs(...)` supplies client-overridable defaults.

  Reserved transport-pool seeds (`request` / `user` / `data`) and the
  selector pipeline keys (`ordering` / `page` / `limit`) are stripped
  from the spread regardless of mode so clients can't poison
  transport-controlled state.

- **`unknown_arguments=`** — how `arguments` keys outside the binding's
  declared field set are handled.
  - `UnknownArguments.REJECT` (default) — outer `inputSchema` advertises
    `"additionalProperties": false` and the validator rejects unknown
    keys with `-32602`.
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
        selector=get_invoice,             # def get_invoice(*, pk): ...
        output_serializer=InvoiceOutputSerializer,
    ),
)
```

Concrete URIs (no placeholders) appear in `resources/list`; templated ones
appear in `resources/templates/list` so clients can fill them in.

## Dispatch flow

The MCP package owns its own dispatch flow. It does **not** import
`_execute_mutation` or anything under `rest_framework_services.viewsets`.

`tools/call`:

1. Look up the `ToolBinding` by name; reject unknown.
2. Evaluate per-binding `MCPPermission` classes (AND-combined). Denial → 403
   with `WWW-Authenticate` carrying any required scopes.
3. Validate `arguments` via `spec.input_serializer` (DRF `Serializer`,
   bare `@dataclass` auto-wrapped in `DataclassSerializer`, or `None`).
4. Build a kwarg pool: `{request, user, data}`.
5. `resolve_callable_kwargs(spec.service, pool)` →
   `run_service(spec.service, kwargs, atomic=spec.atomic)`.
6. Map `ServiceValidationError` → `-32602` and `ServiceError` → `-32000`.
   Validation errors carry `data.detail` (per-field DRF detail). Setting
   `REST_FRAMEWORK_MCP["INCLUDE_VALIDATION_VALUE"] = True` additionally
   echoes the offending `arguments` dict back as `data.value` — handy for
   debugging schema mismatches against opaque client SDKs, off by default
   because the dict can carry sensitive payloads.
7. Optionally post-process via `spec.output_selector` (also a callable; same
   kwarg-pool dispatch).
8. Render through `spec.output_serializer` if set, else pass through.
9. Wrap as a `ToolResult` with `OutputFormat`-driven encoding for the human-
   readable `content[0]` block. `structuredContent` is always JSON.

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
  re-initialize). Sessions are stored in a pluggable
  [`SessionStore`](reference/registries.md) — by default the Django cache.
- **`Origin`** — strict allowlist. Empty allowlist means "no cross-origin
  requests"; an empty `Origin` header is treated as same-origin and allowed.
  Configure via `REST_FRAMEWORK_MCP["ALLOWED_ORIGINS"]`. Use `["*"]` only for
  dev.

`DELETE /mcp/` with a session id terminates that session immediately. `GET
/mcp/` opens a server-initiated SSE stream — available on `async_urls` only
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
    spec=ServiceSpec(service=list_invoices, output_serializer=InvoiceOutputSerializer),
    output_format=OutputFormat.AUTO,   # JSON, TOON, or AUTO
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
