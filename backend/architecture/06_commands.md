# 06 — Command Contract (Write Operations)

## Definition

A command is an async function that performs a state-changing operation: it writes to the database, emits events, or triggers side effects. It represents one business intent.

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

## Minimum skeleton — copy this, never read another command as a template

```python
# services/commands/<domain>/create_record.py
from pydantic import BaseModel, ValidationError as PydanticValidationError, field_validator

from my_app.errors.validation import ValidationError
from my_app.services.context import ServiceContext


class RecordCreateRequest(BaseModel):
    name: str

    @field_validator("name", mode="before")
    @classmethod
    def normalize_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name must not be blank.")
        return v


def parse_create_record_request(data: dict) -> RecordCreateRequest:
    try:
        return RecordCreateRequest.model_validate(data)
    except PydanticValidationError as exc:
        first_error = exc.errors()[0]
        field = ".".join(str(loc) for loc in first_error["loc"])
        raise ValidationError(f"{field}: {first_error['msg']}") from exc


async def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)

    async with ctx.session.begin():
        # reads and writes here — never ctx.incoming_data inside this block
        pass

    # event dispatch here, after commit
    return {}
```

This file is self-contained. The request parser and the command live together. Add fields, validators, and DB logic — but do not change the skeleton shape.

---

## Command signature

Every command is a module-level async function:

```python
from my_app.services.context import ServiceContext


async def create_record(ctx: ServiceContext) -> dict:
    ...
```

**Rules:**
- The function name is the verb+noun describing the operation.
- `ctx: ServiceContext` is always the only parameter. No second positional parameter, no `**kwargs`.
- All DB access goes through `ctx.session` — the `AsyncSession` injected by the router.
- Return type is always annotated. For write commands it is `dict`. For deletion it is `dict` (empty `{}` or a confirmation payload).
- Entity IDs from URL path parameters are injected into `ctx.incoming_data` by the router before `run_service` is called. The command reads them via its request parser.

---

## Request parsing

Commands parse and validate their own input immediately:

```python
# services/commands/<domain>/create_record.py
from .requests import RecordCreateRequest, parse_create_record_request


async def create_record(ctx: ServiceContext) -> dict:
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

---

## Request parser pattern

```python
# services/commands/<domain>/requests/create_record_request.py
from pydantic import BaseModel, field_validator


class RecordCreateRequest(BaseModel):
    client_id:   str | None = None
    name:        str
    category_id: str | None = None

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

Pydantic V2 (`model_validate`) is the standard. Convert `ValidationError` into `ValidationFailed` inside the parser so it crosses layer boundaries correctly.

### Rules — enforced by contract

1. **One parse entry point**: `parse_<name>_request(data)` calls `model_validate(data)` directly. There is no intermediate classmethod.
2. **Validators on the model**: blank checks and normalization go in `@field_validator` methods. They raise `ValueError` — Pydantic converts this into a `ValidationError` automatically.
3. **Domain error conversion in the parser only**: the `parse_` function is the only place that converts `PydanticValidationError` into a domain `ValidationError`. The model itself never raises domain errors.
4. **No `validate_fields` classmethod**: do not add a custom classmethod that calls `model_validate` internally — this creates two entry points and means domain errors escape the model layer uncaught.

### Anti-pattern — do NOT do this

```python
# WRONG: two entry points; model raises a domain error directly
class MyRequest(BaseModel):
    name: str

    @classmethod
    def validate_fields(cls, data: dict) -> "MyRequest":
        if not data.get("name", "").strip():
            raise ValidationError("name must not be blank.")   # domain error inside model — wrong
        return cls.model_validate(data)                        # second entry point — wrong

def parse_my_request(data: dict) -> MyRequest:
    try:
        return MyRequest.validate_fields(data)   # should call model_validate directly
    except PydanticValidationError as exc:       # domain errors from validate_fields slip through
        ...
```

The `validate_fields` classmethod is a known AI over-engineering pattern introduced to "cleanly separate" structural and business validation. The `@field_validator` decorator already provides that separation — no extra method is needed.

---

## Transaction boundaries

Commands own their own transaction. The preferred pattern is `async with ctx.session.begin()`:

```python
async def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)
    pending_events: list[dict] = []

    async with ctx.session.begin():
        record = Record(
            workspace_id=ctx.workspace_id,
            created_by_id=ctx.user_id,
            name=request.name,
        )
        ctx.session.add(record)
        await ctx.session.flush()   # assigns DB-generated id — needed if referenced below
        pending_events.append(build_record_created_event(record))

    emit_record_events(ctx, pending_events)   # after commit
    return {"record": serialize_record_full(record)}
```

**Rules:**
- `ctx.session.add()` does not require `await`.
- `await ctx.session.flush()` is needed when you require the DB-assigned `id` or want to validate constraints before the full commit.
- `await ctx.session.commit()` happens automatically when the `async with ctx.session.begin()` block exits normally.
- Event emission and all side effects happen **after** the transaction commits. Never inside the `begin()` block.
- If the caller already holds an open transaction (batch command), the inner command must operate within that context — use `await ctx.session.flush()` instead of beginning a new one.
- Never nest `async with ctx.session.begin()` inside another `begin()`. SQLAlchemy raises on double-begin. Structure commands to avoid it.

---

## Reading before writing (within a command)

When a command needs to load an existing entity before mutating it, use `select()`:

```python
from sqlalchemy import select
from my_app.models.tables.<domain>.record import Record


async def archive_record(ctx: ServiceContext) -> dict:
    request = parse_archive_record_request(ctx.incoming_data)

    async with ctx.session.begin():
        result = await ctx.session.execute(
            select(Record).where(
                Record.workspace_id == ctx.workspace_id,
                Record.client_id == request.client_id,
                Record.deleted_at.is_(None),
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            raise NotFound("Record not found.")

        record.archived_at = datetime.now(timezone.utc)

    return {}
```

`result.scalar_one_or_none()` returns the single ORM instance or `None`. Use `scalar_one()` when you expect exactly one result and want to raise on zero.

---

## Side effects: event emission

Events are dispatched after the transaction commits via the event bus. Commands never import handlers or push functions directly:

```python
from my_app.services.infra.events.build_event import build_workspace_event
from my_app.services.infra.events import event_bus


async def create_record(ctx: ServiceContext) -> dict:
    pending_events = []

    async with ctx.session.begin():
        # ... create record
        pending_events.append(build_workspace_event(record, "record:created"))

    event_bus.dispatch(pending_events)   # after commit — never inside begin()
    return {"record": serialize_record_full(record)}
```

With extra context:

```python
pending_events.append(build_workspace_event(
    record,
    "record:state-changed",
    extra={"new_state": record.state.value},
))
```

With a user-specific event alongside a workspace event:

```python
from my_app.services.infra.events.build_event import build_workspace_event, build_user_event

pending_events.append(build_workspace_event(message, "message:created"))
pending_events.append(build_user_event(
    user_id=ctx.user_id,
    event_name="message:sent-receipt",
    client_id=message.client_id,
))

event_bus.dispatch(pending_events)
```

Commands must not decide whether to suppress events based on a flag from `ctx`. If an operation must not emit events, it is a different command. See [11_infra_events.md](11_infra_events.md) for the full event bus contract, handler registration, and batch event patterns.

---

## Commands must not call other commands

If you need to share logic, extract it into:

- A domain function (pure logic)
- A private `_helper.py` module (ORM calls)
- An infra function (Redis / external systems)

```python
# Wrong
async def create_record(ctx):
    await resolve_or_create_dependency(ctx)   # another command

# Correct
async def create_record(ctx):
    dependency = await _resolve_dependency(ctx.session, ctx.workspace_id, request.dependency_id)
```

---

## Batch commands

When one command applies the same operation to multiple entities, it orchestrates the loop internally:

```python
async def create_records_batch(ctx: ServiceContext) -> dict:
    requests = [parse_create_record_request(item) for item in ctx.incoming_data.get("items", [])]
    instances = []
    pending_events = []

    async with ctx.session.begin():
        for req in requests:
            instance = _build_record(ctx, req)
            ctx.session.add(instance)
            instances.append(instance)
            pending_events.append(build_record_created_event(instance))
        await ctx.session.flush()

    emit_record_events(ctx, pending_events)
    return {"created": [serialize_record_full(r) for r in instances]}
```

---

## Returning data from commands

Commands return a plain `dict`. The router serializes it into an HTTP response:

```python
# Create — return the created representation
return {"record": serialize_record_full(record)}

# Update — return changed fields or full updated representation
return {"record": serialize_record_full(record)}

# Delete / archive — return empty ack
return {}
```

---

## Domain validators vs. request validators

Two different kinds of validation exist. Know which you need before creating a standalone function.

| Kind | What it checks | Where it lives | Example |
|---|---|---|---|
| Structural | Shape or format of the value itself | `@field_validator` on the request model | `email` contains `@`, `name` is not blank |
| Business policy | A rule about what values the system accepts | Domain function, called from the command body | Minimum password length, disallowed username reserved words |

**Structural validation belongs in `@field_validator`**, not in a separate `domain/validators.py` function. A standalone function that only checks format (e.g. `validate_email_format`) is the wrong layer: it duplicates what Pydantic already provides and runs at the wrong time (after parsing instead of during).

**Business policy validation belongs in the domain layer** as a pure function with no I/O — called by the command after the request is parsed:

```python
# domain/users/password_policy.py
def validate_password_policy(password: str) -> None:
    if len(password) < 8:
        raise ValidationError("Password must be at least 8 characters long.")
```

If you are writing a domain function that calls `split("@")` or `strip()` to check string shape — stop. That is a `@field_validator`.

---

## Domain guards — when to use them

A domain guard is a pure predicate that checks business authorization beyond role membership:

```python
# domain/cases/case_guards.py
def can_close_case(case_state: str, caller_role: str) -> bool:
    """Only admins can close cases that are already escalated."""
    return caller_role == "admin" or case_state != "escalated"
```

Guards answer questions the role system cannot: "is this operation allowed given the current state of this entity?" They are called from the command body after the entity is loaded.

**Do not write a domain guard if the only check is role membership.** Router-level `require_roles([ADMIN])` already enforces that. A guard that only wraps `caller_role == "admin"` is dead code — it will never be called because unauthorized requests are rejected before they reach the command.

Decision:

| Question the guard answers | Use guard? |
|---|---|
| Does the caller hold the required role? | No — use `require_roles` on the router |
| Is this operation allowed given the entity's current state? | Yes |
| Does the caller own this resource (e.g., created_by_id matches)? | Yes |
| Is a combination of role + state required? | Yes |
