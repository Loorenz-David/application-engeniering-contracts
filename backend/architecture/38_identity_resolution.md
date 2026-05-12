# 38 — Identity Resolution Contract

## Definition

Backend entities have two identifiers:

| Identifier | Owner | Visibility | Purpose |
|---|---|---|---|
| `id` | Database/backend | Internal only | Primary key, joins, internal jobs, cursor pagination |
| `client_id` | Client or backend | Public API | Stable external identifier for routes, frontend cache keys, optimistic UI, events |

The public API identifies resources by `client_id`. Internal service code may use `id` when it is already operating on trusted internal data. Commands and queries do not hand-roll lookup logic; they resolve references through a shared resolver.

```
HTTP route path       /api/v1/records/<record_client_id>
        ↓
Router               incoming_data["client_id"] = record_client_id
        ↓
Request parser       RecordRef(client_id=...)
        ↓
Resolver             workspace_id + client_id + not deleted
        ↓
Service              receives ORM instance or raises NotFound
```

---

## Public vs internal identifiers

### Public API

Public HTTP routes use `client_id`:

```python
@record_bp.route("/<string:record_client_id>", methods=["GET"])
def get_record_route(record_client_id: str):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=get_jwt(),
    )
    return run_service(get_record, ctx)
```

Do not expose internal integer IDs as route identifiers.

### Internal service calls

Internal jobs, event handlers, and commands may use `id` when the value came from the database or a trusted internal event:

```python
record = resolve_record(ctx, EntityRef(id=record_id))
```

Never accept an internal `id` from an untrusted public request body.

---

## EntityRef

Use a small typed reference object to avoid passing loose dictionaries into resolvers.

```python
# services/identity/entity_ref.py
from dataclasses import dataclass

from my_app.errors import ValidationFailed


@dataclass(frozen=True)
class EntityRef:
    id: int | None = None
    client_id: str | None = None

    def __post_init__(self) -> None:
        if self.id is None and self.client_id is None:
            raise ValidationFailed("Provide either id or client_id.")
        if self.id is not None and self.client_id is not None:
            raise ValidationFailed("Provide only one identifier.")
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

Frontend create requests still send `client_id`; backend-only flows may omit it and let the command generate one.

If an internal job needs `id`, build the ref explicitly from trusted data:

```python
ref = EntityRef(id=record_id)
```

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

    if ref.client_id is not None:
        query = query.filter(model.client_id == ref.client_id)
    else:
        query = query.filter(model.id == ref.id)

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

    has_client_ids = [ref.client_id is not None for ref in refs]
    if any(has_client_ids) and not all(has_client_ids):
        raise ValidationFailed(f"Resolve {label} by either id or client_id, not both.")

    query: Query = db.session.query(model)

    if hasattr(model, "workspace_id"):
        query = query.filter(model.workspace_id == ctx.workspace_id)

    if hasattr(model, "is_deleted") and not include_deleted:
        query = query.filter(model.is_deleted == False)  # noqa: E712

    if all(has_client_ids):
        keys = [ref.client_id for ref in refs]
        query = query.filter(model.client_id.in_(keys))
        key_of = lambda instance: instance.client_id
    else:
        keys = [ref.id for ref in refs]
        query = query.filter(model.id.in_(keys))
        key_of = lambda instance: instance.id

    if for_update:
        query = query.with_for_update()

    found = query.all()
    by_key = {key_of(instance): instance for instance in found}
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

Trusted internal jobs may pass internal IDs:

```python
refs = [EntityRef(id=record_id) for record_id in record_ids]
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

Create commands accept `client_id` for first-party entities. If `client_id` is missing in a non-frontend flow, the backend may generate one.

```python
from uuid import uuid4


client_id = request.client_id or str(uuid4())

record = Record(
    client_id=client_id,
    workspace_id=ctx.workspace_id,
    name=request.name,
)
```

Frontend-created records should always send `client_id` so optimistic navigation and cache seeding work. Backend-created records may generate it server-side.

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

- Public HTTP routes use `client_id`, never internal `id`.
- Internal jobs may use `id` only when it came from trusted internal data.
- Services resolve references through domain wrapper resolvers, not ad hoc query filters.
- The resolver always applies workspace scope when the model has `workspace_id`.
- The resolver filters soft-deleted rows unless `include_deleted=True`.
- Commands that mutate a resolved row pass `for_update=True` when race conditions are possible.
- Batch commands resolve all targets before mutating any target.
- Batch resolvers reject mixed identifier types. Resolve by all `client_id`s or all trusted internal `id`s.
- Batch resolvers return results in the same order as the input refs.
- If both `id` and `client_id` are provided, reject the request instead of guessing.
- Permission checks remain in commands/queries. The resolver is not an authorization layer.

---

## What identity resolution must NOT do

- **Never expose internal `id` as the primary public API identifier.** Use `client_id`.
- **Never accept internal `id` from public request bodies for user-facing resources.**
- **Never duplicate `workspace_id` / `client_id` lookup code in every command.** Use the resolver.
- **Never resolve entities one-by-one inside a batch modification loop.** Resolve the batch first, then mutate resolved instances.
- **Never partially mutate a batch when one requested target is missing.** Missing targets fail the whole command before modification.
- **Never resolve cross-workspace data.** Workspace filtering is mandatory.
- **Never include soft-deleted rows by default.** Use `include_deleted=True` only for restore/audit workflows.
- **Never perform permission checks inside the resolver.** Authorization remains a service-layer concern.
- **Never serialize or mutate inside the resolver.** It returns an ORM instance only.
