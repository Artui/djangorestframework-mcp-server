# Register tools from a shared spec registry

If MCP is the only way your project exposes its services, keep calling
`register_service_tool` / `register_selector_tool` directly — this recipe buys
you nothing.

It pays off when the **same specs** are exposed over more than one transport:
MCP here, plus an agent toolset via
[`djangorestframework-pydantic-ai`](https://github.com/Artui/djangorestframework-pydantic-ai),
plus HTTP views. Each transport has to be told which specs it exposes, so you
end up writing the list once per transport — and the lists drift. A spec gets
added to the MCP wiring and forgotten in the agent's, or the same operation
ends up named `create_order` here and something else there.

`SpecRegistry` (`djangorestframework-services` 0.27+) is the one declaration
site. `MCPServer.register_specs` is the MCP end of it.

## Declare once

```python
# orders/registry.py
from rest_framework_services import SpecRegistry

registry = SpecRegistry()


# orders/apps.py
from django.apps import AppConfig


class OrdersConfig(AppConfig):
    name = "orders"

    def ready(self) -> None:
        from orders import specs
        from orders.registry import registry

        registry.register("list_orders", specs.list_orders, tags=("read", "public"))
        registry.register("get_order", specs.get_order, tags=("read", "public"))
        registry.register("refund_order", specs.refund_order, tags=("write", "admin"))
```

The registry holds only what every transport agrees on — which spec, its
canonical name, its tags.

## Register them as tools

```python
# orders/mcp.py
from rest_framework_mcp import MCPServer

from orders.registry import registry

server = MCPServer(name="orders")
server.register_specs(registry)
```

Each entry becomes a tool: a `ServiceSpec` goes through
`register_service_tool`, a `SelectorSpec` through `register_selector_tool`.
Registration order is the registry's order, so `tools/list` stays stable.

This is a **source for** the server's own `ToolRegistry`, not a replacement for
it. Every tool still lands as a normal binding, and names still share the one
MCP tool namespace — registering a name twice raises, exactly as before.

## Keep the MCP knobs where they belong

The registry is deliberately transport-agnostic: it has no idea what
`paginate` or `annotations` mean. Everything MCP-specific stays here, per tool,
via `overrides`:

```python
server.register_specs(
    registry,
    overrides={
        "list_orders": {
            "paginate": True,
            "description": "List orders, newest first by default.",
        },
        "refund_order": {
            "annotations": {"destructiveHint": True, "idempotentHint": False},
            "permissions": [ScopeRequired("orders:write")],
        },
    },
)
```

Each value is the keyword arguments for that entry's registration method, so
anything those methods accept works — `title`, `output_format`,
`include_output_schema`, `rate_limits`, `url_kwargs`, and the rest.

## What the entry already says

An entry may carry an
[`OfflineContract`][rest_framework_services.types.offline_contract.OfflineContract]:
the `url_kwargs`, `query_params` and `field_audiences` a caller with **no HTTP
request** has to be told, because the URLconf and query string tell an HTTP one
for free. `register_specs` reads it as this mount's default, so the same entry
mounted here and in an in-process Pydantic-AI toolset synthesises the same
absent request:

```python
registry.register(
    "list_project_orders",
    orders_spec,
    agent_contract=OfflineContract(url_kwargs=(UrlKwarg("project_pk", type="integer"),)),
)

server.register_specs(registry)  # the tool takes project_pk, declared once
```

A per-tool `url_kwargs` / `query_params` override wins over it. Overriding
`agent_contract` itself replaces the entry's outright — the only way to mount an
entry with *fewer* channels than it declares, since an empty tuple at the mount
reads as saying nothing rather than saying none.

Two things fail loudly rather than quietly:

- An `overrides` key naming a spec the registry doesn't hold raises
  `ValueError`. A typo would otherwise be a silent no-op.
- A knob that belongs to the other spec kind — `paginate` on a `ServiceSpec` —
  raises `TypeError` from the registration method, since it has no such
  parameter.

## Two mounts, two surfaces

The registry does not force one surface. Filtered views (`by_tag`, `subset`)
each return a **new** registry, so a multi-mount deployment feeds each server
its own projection with no shared state:

```python
internal = MCPServer(name="orders-internal")
public = MCPServer(name="orders-public")

internal.register_specs(registry)  # everything
public.register_specs(registry.by_tag("public"))  # reads only
```

Independent registries work too, when the surfaces have nothing in common:
names are unique *within* a registry, so two registries may reuse one.

## Permissions are not bypassed

`register_specs` calls the same per-tool methods you would have called, so the
permission-declaration guard still applies to every entry. A spec with no
`permission_classes` and no `permissions` override is **refused** with
`ImproperlyConfigured` — or, under
`REST_FRAMEWORK_MCP["REQUIRE_TOOL_PERMISSIONS"] = False`, downgraded to an
`UnguardedToolWarning`.

Bulk registration is a convenience, not a way to register a surface you
haven't secured. If a spec can't declare its own `permission_classes`, guard it
at the binding:

```python
server.register_specs(
    registry,
    overrides={"refund_order": {"permissions": [ScopeRequired("orders:write")]}},
)
```
