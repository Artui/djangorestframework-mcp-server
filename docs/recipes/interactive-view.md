# Render a DRF list as an interactive table

Your list endpoint already projects a queryset with filtering, ordering and
pagination. This turns it into a table an MCP host draws inline in the chat,
instead of the model reading raw JSON aloud.

Two registrations: the view, then the tool that points at it.

## 1. The view

Write the markup only. `body_template_name=` wraps it in a document that
already carries the `ui/*` postMessage bridge — the handshake, the tool-result
and host-context notifications, size reporting, and view-initiated `tools/call`.
That bridge is not optional and it is not the host's; see
[Writing the bridge yourself](../concepts.md#writing-the-bridge-yourself) for
what you take on by skipping it.

```html
<!-- templates/mcp/invoices_table.html -->
<style>
  /* Inherit the host's theme. Never hardcode colours — the same view is
     drawn in light and dark hosts, and the host sends its palette as CSS
     custom properties during the handshake. */
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border, currentColor); }
  .empty { padding: 16px; opacity: .7; }
</style>

<div id="root"><p class="empty">Waiting for results…</p></div>

<script>
  // `mcpApp` is defined before this fragment is parsed, so assigning here is
  // always early enough.
  mcpApp.onToolResult = function (structuredContent) {
    // What `list_invoices` already emits — the selector tool's pagination
    // envelope, unchanged.
    var rows = (structuredContent && structuredContent.results) || [];
    document.getElementById("root").innerHTML = rows.length
      ? "<table><thead><tr><th>Number</th><th>Customer</th><th>Total</th></tr></thead><tbody>" +
        rows.map(function (r) {
          return "<tr><td>" + r.number + "</td><td>" + r.customer + "</td><td>" + r.total + "</td></tr>";
        }).join("") +
        "</tbody></table>"
      : "<p class='empty'>No invoices matched.</p>";
  };
</script>
```

```python
server.register_ui_resource(
    name="invoices_table",
    uri="ui://invoices/table.html",
    body_template_name="mcp/invoices_table.html",
    title="Invoices",
    description="Invoices, as a sortable table.",
)
```

No `csp=` is needed for this view, because the document fetches nothing. That
is the point of the shipped bridge being inlined rather than imported: a view
that pulls its runtime from a CDN needs that origin in `resource_domains`
before it will boot at all, on every host.

The view can also fetch fresh data itself. `mcpApp.callTool("list_invoices",
{ordering: "-total"})` returns a promise and goes back through the ordinary
`tools/call` endpoint, permissions and all. The rest of the surface —
`onToolInput`, `onToolCancelled`, `onHostContext`, `onTeardown`,
`requestDisplayMode`, `sendMessage`, `openLink`, `hostContext` — is documented
on [`build_app_document`][rest_framework_mcp.ui.build_app_document.build_app_document].

## 2. The tool

```python
server.register_selector_tool(
    name="list_invoices",
    spec=SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        output_serializer=InvoiceOutputSerializer,
        filter_set=InvoiceFilterSet,
        permission_classes=[IsAuthenticated],
    ),
    paginate=True,
    ui=UIToolMeta(resource_uri="ui://invoices/table.html"),
)
```

That's the whole server side. The host fetches `ui://invoices/table.html`,
sandboxes it in an iframe, and pushes each `list_invoices` result into it.

**Register the view first.** A tool's `resource_uri` is checked against this
server's resources at registration, so a link nothing answers to is refused
rather than reaching the host as a dangling reference.

## What you did not have to write

- **A second serialisation path.** The view renders from the tool's
  `structuredContent`, which selector tools already emit — the pagination
  envelope included.
- **Auth for the view's own calls.** A `tools/call` the view makes arrives at
  the ordinary endpoint, so `permission_classes`, per-binding `MCPPermission`s
  and rate limits all apply unchanged.
- **The view half of the `ui/*` bridge**, as long as you use
  `body_template_name=`. The *host* half — the iframe, CSP enforcement, and the
  host's end of the protocol — is the host's and is not implemented here.

## Keep tenant data out

!!! warning

    Hosts may prefetch and cache a view **before any tool call**, so the
    template renders with no context and you should keep it that way. A view is
    a *shell* that hydrates itself from tool results.

    Rendering the queryset into the template is a Django author's instinct and
    it is wrong here: the cached document would carry one tenant's rows to
    whoever the host serves next. Per-instance data arrives by notification,
    never in the template and never in the URI.

This is also why there is no `ui://invoices/{pk}/detail.html`. The host fetches
a view once, and the spec defines no expansion mechanism for a tool's
`resource_uri` — so a templated view URI would be a hook no host implements.
"A different view per report type" is just N concrete registrations. RFC 6570
templating for ordinary *data* resources is unaffected.

## Host gotchas worth knowing

The bridge handles the first three. They are here because they explain what you
are seeing when a view misbehaves, and because they are yours again the moment
you write the document yourself.

- **Size to content, and push it.** The host never asks; a silent view is left
  at whatever the host guessed, which is usually two rows tall. Note that
  `document.documentElement.scrollWidth` is the wrong measure when the content
  sits in an `overflow-x: auto` container — an overflowing child does not widen
  its scrolling ancestor, so the document measures exactly as wide as the frame
  it already has and the view asks for the width it was just given. The bridge
  measures `#mcp-app-root` for this reason.
- **Width may be fixed.** Hosts can flex only the height
  (`containerDimensions: {width}`), and a request for more width is refused
  silently. Lay out for the width you are given.
- **Use the host's CSS custom properties**, never hardcoded colours. The
  handshake result carries them and the bridge applies them to `:root`, along
  with `data-theme` and the host's locale.
- **A view can be recreated at any time.** Hold no state you cannot rebuild
  from the next tool result.
- **Declare every origin you touch** in `csp`. Django `{% static %}` assets need
  the static origin in `resource_domains`; a view built with
  `body_template_name=` and no assets of its own needs nothing.
- **`prefers_border=True` is for edge-to-edge views.** The host already draws
  its own chrome, and the packaged shell paints no background of its own for the
  same reason — a view that also paints a card ends up inside three frames.

## Caching, and why a negative result can lie to you

A host may prefetch a `ui://` document and is **not obliged to honour**
`ttlMs: 0`, which is what `RESOURCE_CACHE_TTL_MS` defaults to. A view served
stale across a template change will happily disprove a fix you actually made.

Until this package can version a view for you, the reliable workaround is the
hashed-asset-filename trick: hash the template's contents and put a few hex
characters in the URI, so a changed view is a different resource.

```python
digest = hashlib.sha256(
    (Path(settings.BASE_DIR) / "templates/mcp/invoices_table.html").read_bytes()
).hexdigest()[:8]
uri = f"ui://invoices/table.{digest}.html"

server.register_ui_resource(name="invoices_table", uri=uri, body_template_name=...)
server.register_selector_tool(name="list_invoices", spec=..., ui=UIToolMeta(resource_uri=uri))
```

Both registrations have to name the same URI, which is the reason this is a
recipe rather than a parameter: the tool link is checked against the resource
registry at registration, so a hash the package computed on its own would have
to be threaded back into `UIToolMeta` for you.

## End the URI in `.html`

Not required by the spec. Every reference implementation does it, and the one
public report of a host resolving a view and rendering nothing also used an
extensionless URI. It costs nothing to match the convention.
