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

The marking is `FieldMarking`, from
[djangorestframework-services](https://artui.github.io/djangorestframework-services/),
in DRF's per-field `style` bag:

```python
from rest_framework import serializers
from django_service_specs import FieldMarking
from rest_framework_services import MARKING


class InvoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "status", "etag", "amount_cents"]
        extra_kwargs = {
            "id": {"style": {MARKING: FieldMarking.handle("Invoice handle.")}},
            "etag": {"style": {MARKING: FieldMarking.hidden()}},
            "number": {"style": {MARKING: FieldMarking.label()}},
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
> other tool calls, not for the reader: pass them to other tools that ask for
> one; refer to records by their name in anything you say, never by the
> identifier.

A handle that declares no wording of its own is described as "An opaque
identifier. Pass it to other tools that ask for one; refer to the record by its
name in anything you say, never by this value." Both sentences are the server's
to change, per server, through
[`MCPServer(conventions=...)`](../concepts.md#agent-conventions).

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
needs what its sibling hides, that is an override, and it belongs on the spec
the registry holds rather than on this mount:

```python
from django_service_specs import FieldMarking, SelectorKind
from rest_framework.permissions import IsAuthenticated
from rest_framework_services import SelectorSpec

invoice_spec = SelectorSpec(
    kind=SelectorKind.RETRIEVE,
    selector=get_invoice,
    output_serializer=InvoiceSerializer,
    permission_classes=[IsAuthenticated],
    # This tool returns the etag after all.
    field_audiences={"etag": FieldMarking()},
)
registry.register("lookup_invoice", invoice_spec, tags=("billing",))

server.register_specs(registry.by_tag("billing"))
```

Every registrar reads `field_audiences` off the spec, beside its `reads`.
Declaring it on the spec is what keeps an in-process Pydantic-AI toolset and this
server projecting the *same* field set: an audience is not a transport, so
a field hidden from one agent caller and visible to another is a bug you would
find in a transcript rather than in a test.

`SpecRegistry.register` takes no `agent_contract=` (drf-services 0.58.0 removed
it). For an entry registered elsewhere, re-declare the spec it holds with
`dataclasses.replace`:

```python
from dataclasses import replace

from django_service_specs import FieldMarking, SpecRegistry

entry = registry.get("lookup_invoice")
billing = SpecRegistry(
    [replace(entry, spec=replace(entry.spec, field_audiences={"etag": FieldMarking()}))]
)

server.register_specs(billing)
```

A server registering a spec directly, with no registry in front of it, gets the
same projection from the same declaration: `register_selector_tool(spec=...)`
and `register_service_tool(spec=...)` read the spec's `field_audiences` as
`register_specs` does.

A chain has no spec of its own to declare them on, so `register_chain_tool`
takes them as an
[`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]:

```python
server.register_chain_tool(
    name="lookup_and_bill",
    steps=[...],
    agent_contract=OfflineContract(field_audiences={"etag": FieldMarking()}),
)
```

On `register_selector_tool`, `register_service_tool` and as a `register_specs`
override, `agent_contract=` is deprecated and warns: it declares at the mount
what the spec declares itself, and replaces the spec's declaration outright
while it lasts.

Two fields left claiming `FieldMarking.label()` raises `ImproperlyConfigured`
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
