# Add a custom permission

Permissions are a Protocol — anything with `has_permission(request, token)` and
`required_scopes()` qualifies. They're AND-combined per binding, evaluated
after authentication, and any `required_scopes()` from a denying class are
surfaced in the `WWW-Authenticate` header.

## Example: tenant-scoped access

A multi-tenant app might want to gate every tool by the requesting user's
tenant matching a configured value:

```python
from django.http import HttpRequest

from rest_framework_mcp import TokenInfo


class TenantMatches:
    def __init__(self, tenant_id: int) -> None:
        self._tenant_id = tenant_id

    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return getattr(token.user, "tenant_id", None) == self._tenant_id

    def required_scopes(self) -> list[str]:
        return []
```

Stack with `ScopeRequired` to keep both surfaces honest:

```python
server.register_service_tool(
    name="invoices.refund",
    spec=ServiceSpec(service=refund_invoice),
    permissions=[
        ScopeRequired(["invoices:write"]),
        TenantMatches(tenant_id=request.user.tenant_id),
    ],
)
```

## Tips

- **Cheap to construct, side-effect-free at evaluation.** Permissions get
  instantiated once per binding at registration time, so any expensive lookup
  belongs in `has_permission` (and even then, cache it).
- **Don't read mutable settings inside `has_permission`.** Any per-process
  changes (test settings overrides, etc.) won't be observed if you snapshot at
  `__init__`. Read settings each call when you need them.
- **Surface scopes only when they're actually scope-shaped.** For row-level
  rules like `TenantMatches`, return `[]` — the failing client can't fix it by
  obtaining a different scope.
