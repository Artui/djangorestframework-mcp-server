# Expose a service

The smallest possible end-to-end. A single service callable plus a JSON-Schema-
producing input serializer is all the MCP server needs.

```python
from rest_framework import serializers
from rest_framework_services.types.service_spec import ServiceSpec
from rest_framework_mcp import MCPServer


def calculate_total(*, data: dict) -> dict:
    """Return the total of a list of line-item subtotals."""
    return {"total": sum(item["amount"] for item in data["items"])}


class CalculateTotalInput(serializers.Serializer):
    items = serializers.ListField(
        child=serializers.DictField(child=serializers.IntegerField())
    )


server = MCPServer(name="cart")
server.register_tool(
    name="cart.calculate_total",
    spec=ServiceSpec(service=calculate_total, input_serializer=CalculateTotalInput),
    description="Sum the `amount` field across a list of line items.",
)
```

Mount in `urls.py`:

```python
from django.urls import include, path

urlpatterns = [path("mcp/", include(server.urls))]
```

Validation, error mapping (`ServiceValidationError` → `-32602`), and JSON-Schema
publication on `tools/list` come for free. The service callable still works as
a plain Python function — call it directly from your tests or from elsewhere
in the codebase, no MCP awareness required.
