# 06 — Command Contract (Write Operations)

## Definition

A command is a function that performs a state-changing operation: it writes to the database, emits events, or triggers side effects. It represents one business intent.

---

## File structure

One command = one file. The file is named after the operation:

```
services/commands/<domain>/
├── create_record.py
├── update_record.py
├── delete_record.py
├── archive_record.py
└── record_states/
    └── update_record_state.py
```

Commands that need private helpers prefix those files with `_`:

```
services/commands/<domain>/
├── create_record.py
├── _resolve_dependency.py     # used only by create_record
```

---

## Command signature

Every command is a module-level function with a typed signature:

```python
from my_app.services.context import ServiceContext


def create_record(ctx: ServiceContext) -> dict:
    ...
```

**Rules:**
- The function name is the verb+noun describing the operation.
- `ctx: ServiceContext` is always the only parameter. No second positional parameter, no `**kwargs`.
- Return type is always annotated. For write commands it is typically `dict` (the created/updated representation). For deletion it is `dict` (empty `{}` or a confirmation payload).
- Entity IDs from URL path parameters are injected into `ctx.incoming_data` by the router before `run_service` is called. The command reads them from `ctx.incoming_data` via its request parser — not as explicit function parameters.

---

## Request parsing

Commands parse and validate their own input immediately:

```python
# services/commands/<domain>/create_record.py
from .requests import RecordCreateRequest, parse_create_record_request


def create_record(ctx: ServiceContext) -> dict:
    request: RecordCreateRequest = parse_create_record_request(ctx.incoming_data)
    # request is now fully validated — use its typed fields
```

The request parser lives next to the command:

```
services/commands/<domain>/
├── create_record.py
└── requests/
    └── create_record_request.py   # Pydantic model + parse function
```

**Never** pass raw `ctx.incoming_data` dicts into domain functions or ORM constructors without parsing first.

When a command needs an existing entity, the request parser builds an `EntityRef` and the command resolves it through the domain resolver. Do not hand-roll `workspace_id` / `client_id` lookup filters in each command. See [38_identity_resolution.md](38_identity_resolution.md).

When a command changes multiple related entities or needs to return backend-derived cascading changes, use a command-local `WorkContext`. Do not add touched entities or pending events to `ServiceContext`. See [39_work_context.md](39_work_context.md).

---

## Request parser pattern

```python
# services/commands/<domain>/requests/create_record_request.py
from pydantic import BaseModel, field_validator


class RecordCreateRequest(BaseModel):
    client_id: str | None = None
    name: str
    category_id: int | None = None

    @field_validator("client_id")
    @classmethod
    def client_id_must_not_be_empty(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.strip():
            raise ValueError("client_id cannot be blank.")
        return v


def parse_create_record_request(data: dict) -> RecordCreateRequest:
    from my_app.errors import ValidationFailed
    try:
        return RecordCreateRequest.model_validate(data)
    except Exception as e:
        raise ValidationFailed(str(e)) from e
```

Pydantic V2 (`model_validate`) is the standard. Convert `ValidationError` into `ValidationFailed` inside the parser function so it crosses layer boundaries correctly.

For first-party create commands, `client_id` is accepted from the caller when provided. Frontend-created entities send it for optimistic UI. Backend-created entities may generate it server-side. Duplicate `client_id` requests are handled idempotently by returning the existing entity.

---

## Transaction boundaries

Commands own their own transaction. The preferred pattern is explicit transaction management:

```python
def create_record(ctx: ServiceContext) -> dict:
    ...
    pending_events: list[dict] = []

    with db.session.begin():
        _apply(...)   # all db.session.add / flush calls happen here
        pending_events.append(build_record_created_event(instance))

    emit_record_events(ctx, pending_events)   # after commit
    return result
```

**Rules:**
- Event emission (and all side effects) happens **after** the transaction commits. Never inside the `with db.session.begin()` block.
- If the caller already holds an open transaction (e.g., a batch command), the inner command must operate within that context. Use `db.session.flush()` within a shared transaction instead of beginning a new one.
- Never nest `with db.session.begin()` inside another `with db.session.begin()`. SQLAlchemy 2.x raises on double-begin. Structure commands to avoid it.

---

## Side effects: event emission

Events are emitted after the transaction commits, using the infra event layer:

```python
from my_app.services.infra.events.builders.<domain> import build_record_created_event
from my_app.services.infra.events.emitters.<domain> import emit_record_events


def create_record(ctx: ServiceContext) -> dict:
    pending_events: list[dict] = []

    with db.session.begin():
        # ... create record
        pending_events.append(build_record_created_event(instance))

    emit_record_events(ctx, pending_events)
    return result
```

Commands **must not** decide whether to suppress events based on a flag from `ctx`. If an operation must not emit events, it is a different command or the caller handles the result differently.

---

## Commands must not call other commands

If you need to share logic between commands, extract it into:

- A domain function (if it is pure logic)
- A private `_helper.py` module (if it involves ORM calls)
- An infra function (if it involves Redis / external systems)

```python
# Wrong
def create_record(ctx):
    resolve_or_create_dependency(ctx)   # another command

# Correct
def create_record(ctx):
    dependency = _resolve_dependency(ctx.workspace_id, request.dependency_id)  # private helper
```

---

## Batch commands

When one command needs to apply the same operation to multiple entities, it orchestrates the loop internally — it does not call the single-entity command in a loop:

```python
def create_records_batch(ctx: ServiceContext) -> dict:
    requests = [parse_create_record_request(item) for item in ctx.incoming_data.get("items", [])]

    instances = []
    pending_events = []

    with db.session.begin():
        for request in requests:
            instance = _create_single_record(ctx, request)
            instances.append(instance)
            pending_events.append(build_record_created_event(instance))

    emit_record_events(ctx, pending_events)
    return {"created": [serialize_record(r) for r in instances]}
```

For batch updates to existing rows, resolve all targets before mutating:

```python
def archive_records(ctx: ServiceContext) -> dict:
    request = parse_archive_records_request(ctx.incoming_data)
    ctx.require_permission(Permission.ARCHIVE_RECORDS)
    work = RecordWorkContext()

    with db.session.begin():
        records = resolve_records(ctx, request.refs, for_update=True)

        for record in records:
            archive_record(record, work)

    emit_record_events(ctx, work.events)
    return {"records": serialize_records(work.records.values(), ctx)}
```

---

## Returning data from commands

Commands return a plain `dict`. The router serializes it into an HTTP response — the command never touches Flask's `jsonify`.

For create operations, return the created entity representation:

```python
return {
    "record": serialize_record(instance),
}
```

For update operations, return the changed fields or the full updated representation.

For delete operations, return `{}`.
