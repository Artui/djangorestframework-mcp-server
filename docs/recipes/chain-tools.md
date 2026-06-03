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
from rest_framework_mcp import ChainStep, MCPServer, SelectorKind, SelectorSpec, ServiceSpec

server.register_chain_tool(
    name="onboard_account",
    input_serializer=OnboardInput,          # or omit → first step's schema
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
    output_alias="welcome",   # default: the last step
)
```

`inputs` is optional. When omitted, a step receives
`{"data": ctx.args}` (plus `request` / `user`), which suits a first
service step whose callable takes the validated input as `data`.

## Atomicity and errors

`atomic=True` (the default) wraps every step in a single
`transaction.atomic()`. If any step raises `ServiceError` or
`ServiceValidationError`, every prior write rolls back and the client
gets a JSON-RPC error whose `data` names the failing step:

```json
{"code": -32000, "message": "…", "data": {"failedStep": "sub"}}
```

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
single-spec tool does.

## Permissions

Each step's `spec.permission_classes` are AND-combined with the
chain-level `permissions=` and evaluated up front: a failing step
permission blocks the whole chain before any step runs.

## Scope

Chains deliberately do **not** run the selector post-fetch pipeline
(filter / order / paginate) — that belongs on a single
[`register_selector_tool`](selector-tool-with-filterset.md). A selector
step's result is used as-is (rendered `many=True` for `kind=LIST`).
