# Hide plumbing from the model

A serializer written for your REST API is handed to the model verbatim when the
same spec is exposed as a tool. Everything in it is equally visible, and the
model has no way to tell which fields are for it and which are for the person it
is talking to — so records get named by primary key, a status reads as
`PENDING_REVIEW` rather than "Awaiting review", and an ETag gets narrated as if
it were content.

Declare the difference on the serializer, once, and this server applies it to
both the payload and the advertised `outputSchema`.

## Mark the fields

The marking is `AgentField`, from
[djangorestframework-services](https://artui.github.io/djangorestframework-services/),
in DRF's per-field `style` bag:

```python
from rest_framework import serializers
from rest_framework_services import AGENT, AgentField


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "status", "etag", "amount_cents"]
        extra_kwargs = {
            "id": {"style": {AGENT: AgentField.handle("Invoice handle.")}},
            "etag": {"style": {AGENT: AgentField.hidden()}},
            "number": {"style": {AGENT: AgentField.label()}},
        }
```

Nothing else changes at registration. A tool whose spec renders through that
serializer now returns:

```json
{
  "id": 8821,
  "number": "FV/2026/0043",
  "status": "Awaiting review",
  "amount_cents": 124000
}
```

`etag` is gone. `status` reads as a person would say it. `id` is untouched —
a handle is another tool's input, so its value is never re-spelled — and its
`outputSchema` entry says what it is:

```json
"id": {"type": "integer", "description": "Invoice handle."}
```

The tool description gains one generated line, and only when the tool actually
has a handle to explain:

> Identify records by `number`. Fields described as opaque identifiers are for
> other tool calls, not for the reader: pass them on where a tool asks for one,
> and never read them out.

## Why the payload and not just the description

Both. The description is read once per listing and the schema sits next to the
field it describes, so that is where the wording belongs. But a field the model
should never use is *removed* rather than relabelled: a tool result is emitted
as `structuredContent` **and** rendered into a text content block, so the model
reads every byte twice. Relocating a field under some reserved subtree would
cost its keys twice over and hide nothing.

## Override it for one tool

The serializer stays authoritative — it is the one declaration your REST API,
this server, and any in-process toolset all read. When a single tool genuinely
needs what its sibling hides, that is an override, and it belongs on the
registry entry rather than on this mount:

```python
registry.register(
    "lookup_invoice",
    invoice_spec,
    # This tool returns the etag after all.
    agent_contract=AgentContract(field_audiences={"etag": AgentField()}),
)

server.register_specs(registry.by_tag("billing"))
```

`AgentContract` is drf-services' carrier for what a caller with **no HTTP
request** has to be told — the URL kwargs and query params an agent must supply
by hand, and this. Declaring it on the entry is what keeps an in-process
Pydantic-AI toolset and this server projecting the *same* field set: an audience
is not a transport, so a field hidden from one agent caller and visible to
another is a bug you would find in a transcript rather than in a test.

A server registering a spec directly, with no registry in front of it, passes
the same object:

```python
server.register_selector_tool(
    name="lookup_invoice",
    spec=invoice_spec,
    agent_contract=AgentContract(field_audiences={"etag": AgentField()}),
)
```

It works the same on `register_service_tool` and `register_chain_tool` — a chain
has no registry entry, so this is its only route — and as an `overrides` key on
`register_specs`, which **replaces** the entry's contract rather than merging
into it.

Two fields left claiming `AgentField.label()` raises `ImproperlyConfigured`
naming the tool: a record has one name, and picking one silently is the kind of
thing you find in a transcript weeks later.

!!! note "It raises on first use, not at registration"

    The projection is resolved lazily, so a clash surfaces the first time the
    tool is listed or called rather than aborting startup. That is deliberate —
    resolving a serializer at registration would run before the app registry is
    necessarily ready — but it means a mistyped override reaches a request. A
    smoke test that lists your tools once catches it at deploy time.

## Chains and pagination

A chain renders each step through its own spec, so each step is projected by
**its own** serializer's markings rather than the output step's.

For a paginated list the projection lands on the items, never on the envelope —
`page`, `totalPages` and `hasNext` are this server's keys and belong to no
serializer.
