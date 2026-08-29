# A selector tool with filtering, ordering, and pagination

Service tools wrap **mutations** — `register_service_tool` runs the
spec's `service` callable inside `transaction.atomic()` and renders the
result through an output serializer.

For **reads** you want a different shape. A "list invoices" tool is
read-only and benefits from filtering, ordering, and pagination — none
of which belong inside the selector. `register_selector_tool` keeps the
selector tiny (return a queryset) and gives the tool layer the
post-fetch knobs.

```text
arguments → validate(merged inputSchema)
          → run_selector
          → FilterSet(data=…).qs    (if spec.filter_set)
          → FilterSet applies ordering  (if it declares an OrderingFilter)
          → paginate                (if paginate=True)
          → output_serializer(many=True)
          → ToolResult
```

Each knob is opt-in. A bare `register_selector_tool` with no
filter/order/paginate behaves like a plain RPC read.

## Install the optional extra

`spec.filter_set` requires `django-filter`:

```bash
pip install "djangorestframework-mcp-server[filter]"
```

Without it, importing `rest_framework_mcp` still works — the
`ImportError` only fires when a selector spec actually carries a
`filter_set`.

## Define the pieces

A model:

```python
# invoices/models.py
from django.db import models


class Invoice(models.Model):
    number = models.CharField(max_length=32, unique=True)
    amount_cents = models.PositiveIntegerField()
    sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

An output serializer:

```python
# invoices/serializers.py
from rest_framework import serializers

from invoices.models import Invoice


class InvoiceOutputSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invoice
        fields = ["id", "number", "amount_cents", "sent", "created_at"]
```

A scoped selector — returns a raw queryset, no filtering or ordering:

```python
# invoices/selectors.py
from django.db.models import QuerySet

from invoices.models import Invoice


def list_invoices(*, user) -> QuerySet[Invoice]:
    """Return every invoice the caller is allowed to see."""
    return Invoice.objects.for_user(user)  # your scoping manager
```

A FilterSet describing the parametric reads the tool exposes:

```python
# invoices/filters.py
import django_filters

from invoices.models import Invoice


class InvoiceFilterSet(django_filters.FilterSet):
    sent = django_filters.BooleanFilter()
    min_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="gte")
    max_amount = django_filters.NumberFilter(field_name="amount_cents", lookup_expr="lte")
    created_after = django_filters.DateTimeFilter(field_name="created_at", lookup_expr="gte")
    # Ordering is declared here, like any other filter. `OrderingFilter`
    # subclasses `ChoiceFilter`, so it is reflected into the tool's
    # `inputSchema` as a choice over the names on the left — the public
    # vocabulary, not the ORM paths behind them — each carrying the label
    # django-filter derives for it.
    ordering = django_filters.OrderingFilter(
        fields=(("created_at", "created"), ("amount_cents", "amount")),
    )

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after"]
```

## Register the selector tool

```python
from rest_framework_services.types.selector_kind import SelectorKind
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_mcp import MCPServer

from invoices.filters import InvoiceFilterSet
from invoices.selectors import list_invoices
from invoices.serializers import InvoiceOutputSerializer

server = MCPServer(name="invoicing")

server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        output_serializer=InvoiceOutputSerializer,
        filter_set=InvoiceFilterSet,
    ),
    description="List invoices, optionally filtered / ordered / paginated.",
    paginate=True,
)
```

`filter_set` lives on the `SelectorSpec` (since
`djangorestframework-services` 0.18), so the same declaration drives the
HTTP transport and MCP — declare the filterable shape once. Ordering
rides along with it: nothing about ordering appears on the registration
call, because the `OrderingFilter` already declared it. `paginate` is
the one MCP-only pipeline mechanic left here.

!!! warning "`ordering_fields` was removed"

    The registration once took `ordering_fields=[...]`, a list of raw ORM
    paths handed to `.order_by()`. That was a second vocabulary for the
    same `ordering` argument, deprecated in 0.30.0 and removed since —
    passing it now raises `TypeError` at registration.

    Migrate by moving the field list into an `OrderingFilter` on the
    `FilterSet`, as above, mapping each ORM path to the public name you
    want the model to use. A spec with no `filter_set` needs one to order;
    a model's own `Meta.ordering` covers the default order without any
    argument at all.

The decorator form is symmetric with `@server.service_tool`. It
auto-builds the `SelectorSpec` from `kind` + the wrapped function, so it
covers `paginate` but **not** `filter_set` (a
`FilterSet` belongs on the spec) — and therefore not ordering either. For
a filtered or ordered tool, use the explicit
`register_selector_tool` form above, or hand the decorator a ready
`spec=` that carries the `filter_set`:

```python
@server.selector_tool(
    name="invoices.list",
    kind=SelectorKind.LIST,
    output_serializer=InvoiceOutputSerializer,
    paginate=True,
)
def list_invoices(*, user):
    return Invoice.objects.for_user(user)
```

## Generated `inputSchema`

`tools/list` advertises the merged shape:

```json
{
  "type": "object",
  "properties": {
    "sent": {"type": "boolean"},
    "min_amount": {
      "type": "number",
      "description": "Matches `amount_cents` with the `gte` lookup."
    },
    "max_amount": {
      "type": "number",
      "description": "Matches `amount_cents` with the `lte` lookup."
    },
    "created_after": {
      "type": "string",
      "format": "date-time",
      "description": "Matches `created_at` with the `gte` lookup."
    },
    "ordering": {
      "oneOf": [
        {"const": "created", "title": "Created"},
        {"const": "-created", "title": "Created (descending)"},
        {"const": "amount", "title": "Amount"},
        {"const": "-amount", "title": "Amount (descending)"}
      ],
      "title": "Ordering"
    },
    "page": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1, "maximum": 100}
  }
}
```

Filter properties are always optional — they narrow the queryset but
aren't required to call the tool. Ordering accepts both ascending
(`field`) and descending (`-field`) variants, and each variant carries
its label, so a model is told that `-amount` means descending rather
than having to infer it from the sign. `page` defaults to `1` and
`limit` to `100` when the model omits it.

The `description` on `min_amount`, `max_amount` and `created_after` is
derived, not written: where a filter's own name does not give away which
column it matches or with which lookup, drf-services states both. A
filter whose name, field and lookup already agree — `sent` — says nothing
extra, and a `help_text` you write yourself always wins over the derived
wording. A `label` becomes `title` the same way.

`limit`'s `maximum` is the effective [`MAX_PAGE_SIZE`](../reference/settings.md#outbound-bounds)
— the server-wide setting, or the `max_page_size=` passed at registration.
It is advertised here *and* clamped at dispatch, and it disappears from the
schema entirely when the bound is `None`. It is unrelated to `PAGE_SIZE`,
which bounds listing calls such as `tools/list` and never reaches a selector
tool's `limit`.

## Filter type mapping

The schema generator reads `FilterSet.base_filters` — no FilterSet
instantiation, so a `Meta`-driven set without an explicit queryset
still works. Common filter classes are mapped accurately:

| `django_filters` class       | JSON Schema fragment                              |
|------------------------------|---------------------------------------------------|
| `CharFilter`                 | `{"type": "string"}`                              |
| `BooleanFilter`              | `{"type": "boolean"}`                             |
| `NumberFilter`               | `{"type": "number"}`                              |
| `DateFilter`                 | `{"type": "string", "format": "date"}`            |
| `DateTimeFilter`             | `{"type": "string", "format": "date-time"}`       |
| `TimeFilter`                 | `{"type": "string", "format": "time"}`            |
| `UUIDFilter`                 | `{"type": "string", "format": "uuid"}`            |
| `ChoiceFilter`               | `{"oneOf": [{"const": <value>, "title": <label>}, …]}`, or `{"enum": [<values>]}` when the labels only restate their values (or `{"type":"string"}` if choices are deferred) |
| `MultipleChoiceFilter`       | `{"type": "array", "items": <the ChoiceFilter shape>}` |
| `BaseInFilter` (CSV)         | `{"type": "array", "items": <scalar>}`           |
| `BaseRangeFilter`            | `{"type": "object", "properties": {"min": <scalar>, "max": <scalar>}}` |
| `ModelChoiceFilter`          | `{"type": "string"}` (FK PK; coerced by FilterSet at dispatch) |

Custom filter classes that don't match any of the above fall through
to `{}` (JSON Schema's "any value" shape) so a niche filter never
breaks tool discovery — discoverability degrades gracefully rather
than failing the whole `tools/list` call.

## Paginated response shape

When `paginate=True`, `tools/call` wraps the rendered list in a
pagination envelope:

```json
{
  "items": [<rendered objects>],
  "page": 1,
  "totalPages": 7,
  "hasNext": true
}
```

Without `paginate`, the response is the rendered list directly. Choose
based on how many rows your selector can return — paginate as soon as
the list could outgrow a single tool-call response.

## Combining with `input_serializer`

`spec.filter_set` only describes the filter shape. If your tool also needs
non-filter arguments, declare them through `input_serializer=` — the
two schemas merge in `inputSchema` and the validated payload reaches
the selector via the kwargs pool.

```python
class InvoiceListInput(serializers.Serializer):
    include_drafts = serializers.BooleanField(required=False, default=False)


server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(
        kind=SelectorKind.LIST,
        selector=list_invoices,
        output_serializer=InvoiceOutputSerializer,
        filter_set=InvoiceFilterSet,
    ),
    input_serializer=InvoiceListInput,
    paginate=True,
)
```

`include_drafts` lands in `data` for the selector to consume (or any
`**kwargs`-shaped argument the selector declares); FilterSet-driven
properties are applied after the selector returns its base queryset.

## When to reach for a service tool instead

If the operation creates / updates / deletes rows, use
`register_service_tool`. The service-tool path runs inside
`transaction.atomic()` (by default) and renders through an output
*serializer*, not a queryset pipeline. Selector tools are the
read-shaped sibling — they should never have side effects.
