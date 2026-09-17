# Chain several specs into one tool

A single MCP tool usually wraps one spec. Sometimes a meaningful
operation is a *sequence*: retrieve a record, write a related one, then
write a third that depends on both. Doing that as three separate tool
calls pushes orchestration onto the client and leaves three chances to
end up with half-written state.

`register_chain_tool` runs an ordered list of steps as one tool. Each
step binds its result to an alias; later steps read earlier outputs
through `ctx[alias]`. The whole sequence runs in one transaction by
default.

```text
arguments → validate(input_serializer or first step's)   → ctx.args
          → step "acct"  (selector)  → ctx["acct"]
          → step "sub"   (service)   → ctx["sub"]
          → step "welcome" (service, reads acct + sub)
          → render the output step
          → ToolResult
        (all inside transaction.atomic() when atomic=True)
```

## Define the steps

Each `ChainStep` is an alias, a `ServiceSpec` / `SelectorSpec`, and an
optional `inputs(ctx)` callable that builds that step's kwargs from the
validated arguments (`ctx.args`) and any prior output (`ctx[alias]`):

```python
from rest_framework_mcp import ChainStep, MCPServer
from rest_framework_services import SelectorKind, SelectorSpec, ServiceSpec

server.register_chain_tool(
    name="onboard_account",
    input_serializer=OnboardInput,  # or omit → first step's schema
    steps=[
        ChainStep(
            "acct",
            SelectorSpec(kind=SelectorKind.RETRIEVE, selector=get_account),
            inputs=lambda ctx: {"pk": ctx.args["account_id"]},
        ),
        ChainStep(
            "sub",
            ServiceSpec(service=create_subscription, output_selector_spec=SUB_OUT),
            inputs=lambda ctx: {"account": ctx["acct"], "plan": ctx.args["plan"]},
        ),
        ChainStep(
            "welcome",
            ServiceSpec(service=send_welcome, output_selector_spec=WELCOME_OUT),
            # derives from BOTH prior steps
            inputs=lambda ctx: {"account": ctx["acct"], "subscription": ctx["sub"]},
        ),
    ],
    output_alias="welcome",  # default: the last step
)
```

`inputs` is optional. When omitted, a step receives
`{"data": ctx.args}` (plus `request` / `user`), which suits a first
service step whose callable takes the validated input as `data`.

## Atomicity and errors

`atomic=True` (the default) wraps every step in a single
`transaction.atomic()`. If any step raises `ServiceError` or
`ServiceValidationError`, every prior write rolls back and the call
answers with an `isError: true` tool result, as a single-spec tool does,
whose error object names the failing step:

```json
{"error": {"type": "service_error", "message": "…", "failedStep": "sub"}}
```

A step refused by its service's `affordances` also carries the refusal's
`code` beside `failedStep`, so a client can tell which rule stopped the
chain without matching on the sentence:

```json
{"error": {"type": "service_error", "message": "The books are closed.",
           "code": "books_closed", "failedStep": "void"}}
```

Any other `ServiceError` carries no `code` key.

Set `atomic=False` to let each step commit independently (no rollback).

## What the tool returns

- Default: the **last** step's rendered output.
- `output_alias="acct"`: render a specific step instead.
- `output_all=True`: return `{alias: rendered}` for every step that
  declares an output serializer.

A step is rendered through its serializer — `ServiceSpec.
output_selector_spec.output_serializer` or
`SelectorSpec.output_serializer` — and its output-context provider sees
the resolved data (`result` / `instance` / `page`), exactly as a
single-spec tool does. A `LIST` renders as a list, whether the step is a
`LIST` selector or a service whose `output_selector_spec` re-fetches a
`LIST`, and a chain never paginates, so the tool's `outputSchema` advertises
a bare array for such an output step.

### Affordances on a rendered step

A selector spec's `affordances` render as an `affordances` object on each
item, from answers the selector tool's dispatch computes as it fetches the
rows. A chain step runs its selector directly and computes none, so
registering a chain **refuses** a rendered step — the output step, or any
step under `output_all` — whose serializer would render affordances that ask
a condition:

```text
ImproperlyConfigured: Chain tool 'orders': step 'out' is rendered through a
selector spec declaring affordances ['cancel'], ...
```

Register that spec as a selector or service tool of its own, where the
answers are computed and rendered, or drop the affordances from the step.
An intermediate step is not rendered, so it may declare them freely.

## Permissions

Each step's `spec.permission_classes` are AND-combined with the
chain-level `permissions=` and evaluated up front: a failing step
permission blocks the whole chain before any step runs.

The object-level half, `has_object_permission`, cannot run up front, because
there is no row yet. It runs on each row as its step resolves it: a `RETRIEVE`
selector step's row, and the instance a service step's `instance_selector_spec`
fetches. A denial answers the whole call as a JSON-RPC permission error, not as
a failed step, and under `atomic=True` every earlier write rolls back.

## Scope

Chains deliberately do **not** run the selector post-fetch pipeline
(filter / order / paginate) — that belongs on a single
[`register_selector_tool`](selector-tool-with-filterset.md). A selector
step's result is used as-is (rendered `many=True` for `kind=LIST`).

A `RETRIEVE` step resolves to its one row the way the selector tool does, so a
selector may return a queryset (`Invoice.objects.filter(pk=pk)`) or the instance
(`Invoice.objects.get(pk=pk)`) and the next step receives the row either way. A
service step whose `output_selector_spec` re-fetches a `RETRIEVE` resolves it the
same way. When there is no row, the step fails as the selector tool does, naming
the step, and an atomic chain rolls back:

```json
{"error": {"type": "not_found", "message": "target: no matching instance found",
           "failedStep": "target"}}
```

A spec with `allow_none=True` passes `None` on instead, and renders it as `null`.
