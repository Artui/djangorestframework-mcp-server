# Expose a polymorphic action as per-variant tools

drf-services 0.25 added `PolymorphicServiceSpec` — one action that accepts
*N* mutually exclusive payload shapes and picks a variant at dispatch time
from a `discriminator` callable reading the request body. It exists because
an HTTP URL is a scarce, addressable resource: sometimes you want one
endpoint to accept a tagged-union body instead of *N* endpoints.

MCP has no such scarcity. You can register as many tools as you like, and a
model calls tools far more reliably when each is a single-purpose tool with a
flat schema than when it has to pick a `anyOf` arm *and* set a discriminator
field correctly. So the right move is not to mirror the union — it's to
**expand a `PolymorphicServiceSpec` into one tool per variant**. The tool
*name* selects the variant, so the discriminator never runs and the model
never sees a union.

`PolymorphicServiceSpec.specs` is a public `Mapping[str, ServiceSpec]`
(variant key → full `ServiceSpec`, each with its own `input_serializer`,
`service`, and output pipeline), so the expansion is a loop over existing
API:

```python
from rest_framework_services import PolymorphicServiceSpec, ServiceSpec
from rest_framework_mcp import MCPServer

# The same spec you'd hand a viewset's `action_specs` on the HTTP side.
moderate = PolymorphicServiceSpec(
    discriminator=lambda *, data: data["op"],       # HTTP-only; unused here
    specs={
        "approve": ServiceSpec(service=approve_document, output_selector_spec=DOC_OUT),
        "reject": ServiceSpec(service=reject_document, output_selector_spec=DOC_OUT),
    },
)

server = MCPServer(name="docs")
for variant_key, variant in moderate.specs.items():
    server.register_service_tool(
        name=f"moderate_document_{variant_key}",   # → moderate_document_approve, …
        spec=variant,
        description=f"Moderate a document ({variant_key}).",
    )
```

Each registered tool is an ordinary service tool: its `inputSchema` comes
from that variant's `input_serializer`, and dispatch runs the usual
`input_serializer → run_service(atomic) → output` pipeline. Nothing about
`PolymorphicServiceSpec` leaks into the wire — the discriminator is a
server-side HTTP concern, and the MCP surface stays a flat menu of clear
tools.

Give each variant its own `permissions`, `output_format`, or `annotations`
as needed — they're independent tools that happen to share an origin spec:

```python
server.register_service_tool(
    name="moderate_document_reject",
    spec=moderate.specs["reject"],
    permissions=[ScopeRequired("documents:moderate")],
    annotations={"destructiveHint": True},
)
```

If several variants genuinely belong together as one atomic operation
(retrieve-then-write, say), that's a *chain*, not a union — see
[Chain several specs into one tool](chain-tools.md).
