# Worked examples

Each subdirectory is a complete, runnable Django project that exercises
a specific corner of `djangorestframework-mcp-server`. They are
intentionally small — minimal `settings.py`, SQLite, a single
Django app — so you can read the whole thing in one sitting and copy
the bits you need into your own project.

| Example                        | What it shows                                                     |
|--------------------------------|-------------------------------------------------------------------|
| [`invoicing/`](invoicing/)     | Service tools, selector tool with `FilterSet` + ordering + pagination, resource by PK, prompt. |
| [`job_status/`](job_status/)   | A long-running job exposed as a templated resource, with SSE push notification on completion. |

## Running an example

Each example ships its own `manage.py`. From the example's directory:

```bash
# Set up the deps in a fresh venv (uses the workspace install, no extras unless noted)
uv pip install -e "../.." "../..[filter,oauth]"

# Migrate, run, smoke-test
python manage.py migrate
python manage.py runserver

# In another terminal: drive it
curl -X POST http://localhost:8000/mcp/ \
  -H 'Content-Type: application/json' \
  -H 'Mcp-Protocol-Version: 2025-11-25' \
  -H 'Origin: http://localhost' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

The session id comes back as the `Mcp-Session-Id` response header — use
it on every subsequent request.

You can also drive each example through
[mcp-inspector](https://github.com/modelcontextprotocol/inspector):

```bash
npx @modelcontextprotocol/inspector
# Connect to http://localhost:8000/mcp/ — Inspector lists tools, resources, prompts.
```

## Notes

- Examples use `AllowAnyBackend` for simplicity. Production deployments
  should use `DjangoOAuthToolkitBackend` (or your own
  `MCPAuthBackend`); see `docs/auth.md`.
- SQLite is used for portability. Swap `DATABASES` in the example's
  `settings.py` for Postgres/MySQL when adapting to your stack.
- Each example registers tools imperatively in `mcp.py` to keep the
  flow visible. Decorator forms work identically.
