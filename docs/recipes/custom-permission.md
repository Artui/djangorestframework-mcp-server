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

Stack with `ScopeRequired` to keep both surfaces honest. The `tenant_id` is
captured at registration time — read it from settings or any other
process-wide source, never from a `request` (no request exists during
registration):

```python
from django.conf import settings

server.register_service_tool(
    name="invoices.refund",
    spec=ServiceSpec(service=refund_invoice),
    permissions=[
        ScopeRequired(["invoices:write"]),
        TenantMatches(tenant_id=settings.ACTIVE_TENANT_ID),
    ],
)
```

## Permissions are synchronous, on both transports

`has_permission` — and the optional `is_listable` — must be a plain `def`.
Writing either `async def` raises `ImproperlyConfigured` at evaluation time,
under ASGI just as much as under WSGI.

That is not an oversight of the async transport. Permissions are reached
through an aggregate helper that the async path bridges to a thread, so an
`async def has_permission` inside it is never awaited on *either* transport —
and an un-awaited coroutine is truthy, which means `not result` is `False` and
**every caller is granted**. The refusal converts a silent, total bypass into a
misconfiguration you can see.

If a permission genuinely needs to await something, do it inside the
synchronous method:

```python
from asgiref.sync import async_to_sync


class TenantMatches:
    def has_permission(self, request: HttpRequest, token: TokenInfo) -> bool:
        return async_to_sync(self._check)(token)

    async def _check(self, token: TokenInfo) -> bool: ...
```

The same rule holds for [rate limiters](rate-limiting.md) (`consume`). Session
stores are the deliberate exception — see
[Swap the session store](swap-session-store.md) — and may be `async def`, but
only when mounted under `server.async_urls`; the sync transport refuses those
too rather than treating the coroutine as an answer.

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
