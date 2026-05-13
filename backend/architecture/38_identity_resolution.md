# 38 — Identity Resolution Contract

## Definition

Backend entities use one durable identifier:

| Identifier | Owner | Visibility | Purpose |
|---|---|---|---|
| `client_id` | Backend generated, client visible | Database and public API | Primary key, FKs, routes, frontend cache keys, optimistic UI, events, jobs |

`client_id` is a prefixed ULID string provided by `IdentityMixin` (see [40_identity.md](40_identity.md)). There is no separate internal integer `id` for addressable entities. Commands and queries do not hand-roll lookup logic; they resolve references through a shared resolver so workspace scope and soft-delete filtering stay consistent.

```
HTTP route path       /api/v1/records/<record_client_id>
        ↓
Router               incoming_data["client_id"] = record_client_id
        ↓
Request parser       EntityRef(client_id=...)
        ↓
Resolver             workspace_id + client_id + not deleted
        ↓
Service              receives ORM instance or raises NotFound
```

---

## Identifier Use

Public HTTP routes, workers, events, and internal service calls all use `client_id`.

```python
@record_bp.route("/<string:record_client_id>", methods=["GET"])
def get_record_route(record_client_id: str):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=get_jwt(),
    )
    return run_service(get_record, ctx)
```

Never accept or expose an integer database ID for an addressable entity. If a table needs a cursor, use an explicit cursor field such as `created_at` plus `client_id`, not a hidden surrogate identifier.

---

## EntityRef

Use a small typed reference object to avoid passing loose dictionaries into resolvers.

```python
# services/identity/entity_ref.py
from dataclasses import dataclass

from my_app.errors import ValidationFailed


@dataclass(frozen=True)
class EntityRef:
    client_id: str

    def __post_init__(self) -> None:
        if not self.client_id:
            raise ValidationFailed("client_id is required.")
```

Request parsers create `EntityRef` objects:

```python
# services/commands/record/requests/update_record_request.py
from pydantic import BaseModel
from my_app.services.identity.entity_ref import EntityRef


class RecordUpdateRequest(BaseModel):
    client_id: str
    name: str | None = None

    @property
    def ref(self) -> EntityRef:
        return EntityRef(client_id=self.client_id)
```

Create requests may make `client_id` optional when records can be created by backend-only flows:

```python
class RecordCreateRequest(BaseModel):
    client_id: str | None = None
    name: str
```

Frontend create requests still send `client_id`; backend-only flows may omit it and let the model default generate one.

---

## Generic resolver

The generic resolver centralizes workspace enforcement, soft-delete filtering, and error behavior.

```python
# services/identity/resolve_entity.py
from typing import TypeVar

from sqlalchemy.orm import Query

from my_app.errors import NotFound
from my_app.models import db
from my_app.services.context import ServiceContext
from my_app.services.identity.entity_ref import EntityRef

ModelT = TypeVar("ModelT")


def resolve_entity(
    ctx: ServiceContext,
    model: type[ModelT],
    ref: EntityRef,
    *,
    label: str,
    include_deleted: bool = False,
    for_update: bool = False,
) -> ModelT:
    query: Query = db.session.query(model)

    if hasattr(model, "workspace_id"):
        query = query.filter(model.workspace_id == ctx.workspace_id)

    if hasattr(model, "is_deleted") and not include_deleted:
        query = query.filter(model.is_deleted == False)  # noqa: E712

    query = query.filter(model.client_id == ref.client_id)

    if for_update:
        query = query.with_for_update()

    instance = query.one_or_none()
    if instance is None:
        raise NotFound(f"{label} not found.")

    return instance
```

The resolver is infrastructure for lookup only. It does not check permissions, run domain guards, serialize responses, or emit events.

---

## Domain wrapper resolvers

Each domain exposes small wrapper functions so command/query code stays readable and type-aware.

```python
# services/identity/records.py
from my_app.models.tables.record.record import Record
from my_app.services.context import ServiceContext
from my_app.services.identity.entity_ref import EntityRef
from my_app.services.identity.resolve_entity import resolve_entity


def resolve_record(
    ctx: ServiceContext,
    ref: EntityRef,
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> Record:
    return resolve_entity(
        ctx,
        Record,
        ref,
        label="Record",
        include_deleted=include_deleted,
        for_update=for_update,
    )
```

Commands and queries import the domain resolver:

```python
def update_record(ctx: ServiceContext) -> dict:
    request = parse_update_record_request(ctx.incoming_data)
    ctx.require_permission(Permission.EDIT_RECORDS)

    with db.session.begin():
        record = resolve_record(ctx, request.ref, for_update=True)
        # mutate record

    return {"record": serialize_record(record)}
```

---

## Batch resolution

Batch commands resolve all target entities before applying modifications. The resolver fails before any mutation if one or more requested entities cannot be resolved.

```python
# services/identity/resolve_entity.py
from collections.abc import Sequence


def resolve_entities(
    ctx: ServiceContext,
    model: type[ModelT],
    refs: Sequence[EntityRef],
    *,
    label: str,
    include_deleted: bool = False,
    for_update: bool = False,
) -> list[ModelT]:
    if not refs:
        return []

    keys = [ref.client_id for ref in refs]

    query: Query = db.session.query(model)

    if hasattr(model, "workspace_id"):
        query = query.filter(model.workspace_id == ctx.workspace_id)

    if hasattr(model, "is_deleted") and not include_deleted:
        query = query.filter(model.is_deleted == False)  # noqa: E712

    query = query.filter(model.client_id.in_(keys))

    if for_update:
        query = query.with_for_update()

    found = query.all()
    by_key = {instance.client_id: instance for instance in found}
    missing = [key for key in keys if key not in by_key]
    if missing:
        raise NotFound(f"{label} not found: {missing[0]}")

    return [by_key[key] for key in keys]
```

Domain wrappers expose plural resolver functions:

```python
# services/identity/records.py
def resolve_records(
    ctx: ServiceContext,
    refs: list[EntityRef],
    *,
    include_deleted: bool = False,
    for_update: bool = False,
) -> list[Record]:
    return resolve_entities(
        ctx,
        Record,
        refs,
        label="Record",
        include_deleted=include_deleted,
        for_update=for_update,
    )
```

Public batch requests pass `client_ids`:

```python
class ArchiveRecordsRequest(BaseModel):
    client_ids: list[str]

    @property
    def refs(self) -> list[EntityRef]:
        return [EntityRef(client_id=client_id) for client_id in self.client_ids]
```

Batch mutation command pattern:

```python
def archive_records(ctx: ServiceContext) -> dict:
    request = parse_archive_records_request(ctx.incoming_data)
    ctx.require_permission(Permission.ARCHIVE_RECORDS)

    with db.session.begin():
        records = resolve_records(ctx, request.refs, for_update=True)

        for record in records:
            assert_record_can_be_archived(record)
            record.status = RecordStatus.ARCHIVED

    return {"records": serialize_records(records, ctx)}
```

The resolver preserves the input order in its return value. This keeps response ordering predictable for the caller.

---

## Create command behavior

Create commands accept optional `client_id` for first-party entities. If `client_id` is missing, the model default generates one.

```python
record = Record(
    workspace_id=ctx.workspace_id,
    name=request.name,
)
if request.client_id:
    record.client_id = request.client_id
```

Frontend-created records should send `client_id` so optimistic navigation and cache seeding work. Backend-created records may rely on the model default.

Duplicate `client_id` handling belongs in the command:

```python
existing = (
    db.session.query(Record)
    .filter(
        Record.workspace_id == ctx.workspace_id,
        Record.client_id == request.client_id,
    )
    .one_or_none()
)
if existing:
    return {"record": serialize_record(existing)}
```

This makes retried create requests idempotent.

---

## Resolver rules

- All addressable entities are resolved by `client_id`.
- Services resolve references through domain wrapper resolvers, not ad hoc query filters.
- The resolver always applies workspace scope when the model has `workspace_id`.
- The resolver filters soft-deleted rows unless `include_deleted=True`.
- Commands that mutate a resolved row pass `for_update=True` when race conditions are possible.
- Batch commands resolve all targets before mutating any target.
- Batch resolvers return results in the same order as the input refs.
- Permission checks remain in commands/queries. The resolver is not an authorization layer.

---

## What identity resolution must NOT do

- **Never introduce or expose internal integer IDs for addressable resources.**
- **Never duplicate `workspace_id` / `client_id` lookup code in every command.** Use the resolver.
- **Never resolve entities one-by-one inside a batch modification loop.** Resolve the batch first, then mutate resolved instances.
- **Never partially mutate a batch when one requested target is missing.** Missing targets fail the whole command before modification.
- **Never resolve cross-workspace data.** Workspace filtering is mandatory.
- **Never include soft-deleted rows by default.** Use `include_deleted=True` only for restore/audit workflows.
- **Never perform permission checks inside the resolver.** Authorization remains a service-layer concern.
- **Never serialize or mutate inside the resolver.** It returns an ORM instance only.
