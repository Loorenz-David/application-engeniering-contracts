# 39 — WorkContext Contract

## Definition

`WorkContext` is a command-local tracker for complex write operations. It records which ORM instances, events, warnings, and derived outputs were touched during one command execution.

It is not a replacement for `ServiceContext`.

| Context | Scope | Carries |
|---|---|---|
| `ServiceContext` | Request/session | identity, incoming data, query params, warnings |
| `WorkContext` | One command execution | touched entities, pending events, operation-local warnings |

Use `WorkContext` when a command changes multiple related entities or when domain logic produces changes that the frontend did not directly request but must receive in the response.

---

## When to use it

Use `WorkContext` for complex commands:

- batch updates
- state transitions that update related rows
- parent aggregate recalculation
- derived status updates
- cascading soft deletes or restores
- operations that emit multiple events
- commands whose response must include all affected entities
- optimistic frontend flows that need authoritative correction data

Do not use it for simple commands that update one entity and return that entity.

---

## Canonical shape

Keep the base class small. Domain-specific work contexts can extend it with typed helpers.

```python
# services/work_context.py
from dataclasses import dataclass, field
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass
class WorkContext:
    events: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_event(self, event: dict) -> None:
        self.events.append(event)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
```

Domain-specific work contexts track typed entities:

```python
# services/commands/record/work_context.py
from dataclasses import dataclass, field

from my_app.models.tables.record.record import Record
from my_app.models.tables.customer.customer import Customer
from my_app.services.work_context import WorkContext


@dataclass
class RecordWorkContext(WorkContext):
    records: dict[str, Record] = field(default_factory=dict)
    customers: dict[str, Customer] = field(default_factory=dict)

    def track_record(self, record: Record) -> None:
        self.records[record.client_id] = record

    def track_customer(self, customer: Customer) -> None:
        self.customers[customer.client_id] = customer
```

Track by `client_id` when the entity has one. This keeps response assembly aligned with public API identity.

---

## Command pattern

Complex commands create a work context after parsing and authorization, then pass it into private helpers or domain orchestration functions.

```python
def archive_records(ctx: ServiceContext) -> dict:
    request = parse_archive_records_request(ctx.incoming_data)
    ctx.require_permission(Permission.ARCHIVE_RECORDS)
    work = RecordWorkContext()

    with db.session.begin():
        records = resolve_records(ctx, request.refs, for_update=True)

        for record in records:
            work.track_record(record)
            archive_record(record, work)

        recompute_customer_statuses(records, work)

    emit_record_events(ctx, work.events)

    return {
        "records": serialize_records(work.records.values(), ctx),
        "customers": serialize_customers(work.customers.values(), ctx),
        "warnings": work.warnings,
    }
```

The command resolves targets first, mutates resolved instances, tracks every affected entity, then serializes the final authoritative state from the work context.

---

## Domain interaction

Pure domain functions should not receive `WorkContext` if they only validate or calculate in memory. Use return values for pure logic.

```python
# Pure — no WorkContext needed
def can_record_be_archived(record: Record) -> bool:
    return record.status != RecordStatus.CLOSED
```

When an operation intentionally mutates multiple in-memory ORM instances and needs to report touched entities/events, use a service-layer orchestration helper, not a pure domain function:

```python
# services/commands/record/_archive_record.py
def archive_record(record: Record, work: RecordWorkContext) -> None:
    assert_record_can_be_archived(record)
    record.status = RecordStatus.ARCHIVED
    work.track_record(record)
    work.add_event(build_record_archived_event(record))
```

The helper may call pure domain guards/calculations, but it owns tracking side effects in the work context.

---

## Response assembly

The response should include every entity whose final state the frontend may need to reconcile:

```python
return {
    "records": serialize_records(work.records.values(), ctx),
    "customers": serialize_customers(work.customers.values(), ctx),
}
```

This is especially important for optimistic UI. The frontend may optimistically update records, but the backend may also update parent customer status, totals, counters, or derived flags. Return those authoritative changes in the command response.

---

## Events and warnings

`WorkContext.events` collects events during the transaction. Emit them after commit:

```python
with db.session.begin():
    archive_record(record, work)

emit_record_events(ctx, work.events)
```

Do not emit events inside the transaction. Do not let domain functions publish events directly.

Warnings in `WorkContext` are operation-local. The command decides whether to copy them into `ctx.warnings` or return them in the response payload. Use `ctx.warnings` for transport-level warnings that should appear in the standard response wrapper.

---

## What WorkContext must NOT do

- **Never replace `ServiceContext`.** It does not carry identity, incoming data, query params, or permissions.
- **Never carry configuration flags.** If behavior changes by flag, use a different command or explicit typed input.
- **Never own the database session.** Transaction boundaries remain in the command.
- **Never perform queries.** Resolve entities before mutation through identity resolvers.
- **Never serialize automatically.** Commands decide response shape explicitly.
- **Never emit events directly.** It collects pending events; emitters run after commit.
- **Never become a service locator.** Do not put clients, adapters, config, or dependency containers on it.
- **Never use it for trivial single-entity updates.** Keep simple commands simple.
