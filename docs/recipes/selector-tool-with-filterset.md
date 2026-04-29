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
          → FilterSet(data=…).qs    (if filter_set)
          → qs.order_by(…)          (if ordering_fields)
          → paginate                (if paginate=True)
          → output_serializer(many=True)
          → ToolResult
```

Each knob is opt-in. A bare `register_selector_tool` with no
filter/order/paginate behaves like a plain RPC read.

## Install the optional extra

`filter_set=` requires `django-filter`:

```bash
pip install "djangorestframework-mcp-server[filter]"
```

Without it, importing `rest_framework_mcp` still works — the
`ImportError` only fires when you actually pass `filter_set=` to a
binding.

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

    class Meta:
        model = Invoice
        fields = ["sent", "min_amount", "max_amount", "created_after"]
```

## Register the selector tool

```python
from rest_framework_services.types.selector_spec import SelectorSpec
from rest_framework_mcp import MCPServer

from invoices.filters import InvoiceFilterSet
from invoices.selectors import list_invoices
from invoices.serializers import InvoiceOutputSerializer

server = MCPServer(name="invoicing")

server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(selector=list_invoices, output_serializer=InvoiceOutputSerializer),
    description="List invoices, optionally filtered / ordered / paginated.",
    filter_set=InvoiceFilterSet,
    ordering_fields=["created_at", "amount_cents"],
    paginate=True,
)
```

The decorator form is symmetric with `@server.service_tool`:

```python
@server.selector_tool(
    name="invoices.list",
    output_serializer=InvoiceOutputSerializer,
    filter_set=InvoiceFilterSet,
    ordering_fields=["created_at", "amount_cents"],
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
    "min_amount": {"type": "number"},
    "max_amount": {"type": "number"},
    "created_after": {"type": "string", "format": "date-time"},
    "ordering": {
      "type": "string",
      "enum": ["created_at", "-created_at", "amount_cents", "-amount_cents"]
    },
    "page": {"type": "integer", "minimum": 1},
    "limit": {"type": "integer", "minimum": 1}
  }
}
```

Filter properties are always optional — they narrow the queryset but
aren't required to call the tool. Ordering accepts both ascending
(`field`) and descending (`-field`) variants. `page` defaults to `1`,
`limit` to the configured page size.

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
| `ChoiceFilter`               | `{"enum": [<values>]}` (or `{"type":"string"}` if choices are deferred) |
| `MultipleChoiceFilter`       | `{"type": "array", "items": {"enum": [...]}}`    |
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

`filter_set=` only describes the filter shape. If your tool also needs
non-filter arguments, declare them through `input_serializer=` — the
two schemas merge in `inputSchema` and the validated payload reaches
the selector via the kwargs pool.

```python
class InvoiceListInput(serializers.Serializer):
    include_drafts = serializers.BooleanField(required=False, default=False)


server.register_selector_tool(
    name="invoices.list",
    spec=SelectorSpec(selector=list_invoices, output_serializer=InvoiceOutputSerializer),
    input_serializer=InvoiceListInput,
    filter_set=InvoiceFilterSet,
    ordering_fields=["created_at"],
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
