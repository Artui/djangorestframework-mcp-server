# Render a DRF list as an interactive table

Your list endpoint already projects a queryset with filtering, ordering and
pagination. This turns it into a table an MCP host draws inline in the chat,
instead of the model reading raw JSON aloud.

Two registrations: the view, then the tool that points at it.

## 1. The view

A single self-contained template. No context is passed, and that is
deliberate — see [the warning below](#keep-tenant-data-out).

```html
<!-- templates/mcp/invoices_table.html -->
<!doctype html>
<meta charset="utf-8" />
<style>
  /* Inherit the host's theme. Never hardcode colours — the same view is
     drawn in light and dark hosts. */
  body { font: 13px/1.5 var(--font-family, system-ui); color: var(--text-primary, #111); margin: 0; }
  table { border-collapse: collapse; width: 100%; }
  th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border, #e5e5e5); }
  .empty { padding: 16px; opacity: .7; }
</style>
<div id="root"><p class="empty">Waiting for results…</p></div>
<script type="module">
  import { App } from "https://esm.sh/@modelcontextprotocol/ext-apps";

  const app = new App({ name: "Invoices table", version: "1.0.0" });

  app.ontoolresult = (result) => {
    // `structuredContent` is what `list_invoices` already emits — the
    // selector tool's pagination envelope, unchanged.
    const rows = result.structuredContent?.results ?? [];
    document.getElementById("root").innerHTML = rows.length
      ? `<table><thead><tr><th>Number</th><th>Customer</th><th>Total</th></tr></thead><tbody>${
          rows.map(r => `<tr><td>${r.number}</td><td>${r.customer}</td><td>${r.total}</td></tr>`).join("")
        }</tbody></table>`
      : `<p class="empty">No invoices matched.</p>`;
    // Claude sizes the frame from documentElement height; tell the host when
    // the content changes so it doesn't clip or leave a gap.
    app.sendSizeChanged();
  };

  app.connect();
</script>
```

The view can also fetch fresh data itself — `app.callServerTool({ name:
"list_invoices", arguments: { ordering: "-total" } })` goes back through the
ordinary `tools/call` endpoint, permissions and all. The View API surface
(`ontoolresult`, `connect`, `callServerTool`, `sendSizeChanged`,
`requestDisplayMode`, …) belongs to
[`@modelcontextprotocol/ext-apps`](https://apps.extensions.modelcontextprotocol.io/),
not to this package — check its docs for the current shape.

```python
server.register_ui_resource(
    name="invoices_table",
    uri="ui://invoices/table.html",
    template_name="mcp/invoices_table.html",
    description="Invoices, as a sortable table.",
    ui=UIResourceMeta(
        # The view imports its module from esm.sh, so that origin has to be
        # declared or the host's CSP blocks it.
        csp=UICsp(resource_domains=["https://esm.sh"]),
        prefers_border=True,
    ),
)
```

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
- **Anything in the sandbox.** The iframe, CSP *enforcement* and the `ui/*`
  postMessage bridge are the host's.

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

- **Use the host's CSS custom properties**, never hardcoded colours — the same
  view is drawn in light and dark hosts.
- **Size to content.** Claude reads `documentElement` height; call
  `sendSizeChanged()` after you mutate the DOM, and set an explicit height as a
  fallback. For long tables, `requestDisplayMode()` a scroll viewport rather
  than growing without bound.
- **A view can be recreated at any time.** Hold no state you cannot rebuild
  from the next tool result.
- **Declare every origin you touch** in `csp`. Django `{% static %}` assets
  need the static origin in `resource_domains`; a single-file template needs
  nothing. The spec's own advice is to inline everything.

## Trying it

Any MCP Apps host: the extension's own `basic-host` example, MCPJam, or Goose.
Claude needs a publicly reachable URL, so tunnel your dev server and add it as
a custom connector.

**The MCP Inspector does not render apps** — it speaks base MCP only, so a view
shows up there as a resource with an HTML body, which is all it is.
