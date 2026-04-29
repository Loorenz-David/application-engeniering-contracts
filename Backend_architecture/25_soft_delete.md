# 25 — Soft Delete Contract

## What soft delete is

Soft delete means a record is never physically removed from the database. Instead, it is marked as deleted and excluded from normal queries. The record remains available for audit logs, event history, and recovery.

Hard delete (physically removing a row) is only acceptable for:
- Records with no audit or compliance requirements (e.g., temporary calculation rows)
- Records that are explicitly replaced by a newer record and carry no history

When in doubt, soft delete.

---

## The two required columns

Every soft-deletable table must have both:

```python
class Record(db.Model):
    __tablename__ = "records"

    # ... other columns ...

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

| Column | Type | Default | Meaning |
|---|---|---|---|
| `is_deleted` | `Boolean` | `False` | Whether the record is logically deleted |
| `deleted_at` | `DateTime(timezone=True)` | `NULL` | Timestamp of deletion — `NULL` means not deleted |

Both columns must be present. `is_deleted` is indexed for query performance. `deleted_at` provides an audit trail.

---

## Performing a soft delete

In a command, soft deletion sets both fields atomically within the write transaction:

```python
from datetime import datetime, timezone
from my_app.services.identity.records import resolve_record


def delete_record(ctx: ServiceContext) -> dict:
    request = parse_delete_record_request(ctx.incoming_data)
    # resolve_record enforces workspace_id and is_deleted == False — see 38_identity_resolution.md
    record = resolve_record(ctx, request.ref)

    if not can_record_be_deleted(record):
        raise PermissionDenied("This record cannot be deleted in its current state.")

    pending_events: list[dict] = []

    with db.session.begin():
        record.is_deleted = True
        record.deleted_at = datetime.now(timezone.utc)
        pending_events.append(build_record_deleted_event(record))

    emit_record_events(ctx, pending_events)
    return {"deleted": True, "client_id": record.client_id}
```

Never set `is_deleted = True` without also setting `deleted_at`. The two fields must always be consistent.

---

## Filtering deleted records in queries

Every query on a soft-deletable table must exclude deleted records by default:

```python
def list_records(ctx: ServiceContext) -> dict:
    query = (
        db.session.query(Record)
        .filter(Record.workspace_id == ctx.workspace_id)
        .filter(Record.is_deleted == False)    # always exclude soft-deleted
        ...
    )
```

The `is_deleted == False` filter is mandatory on every query that returns operational data. A query that returns deleted records without an explicit `include_deleted=True` intent is a bug.

The only place deleted records are returned is:
1. An explicit "archive" or "audit" query that clearly documents this behavior.
2. A recovery/restore command that needs to find deleted records.

---

## Fetching a single record — guard against deleted

When fetching by ID (in commands or queries), always check `is_deleted`:

```python
# Use resolve_record (or resolve_entity), which enforces workspace_id and is_deleted automatically.
# The resolver raises NotFound for missing, deleted, or wrong-workspace records,
# which avoids revealing to callers that a record existed but was deleted (IDOR risk).
record = resolve_record(ctx, request.ref)  # raises NotFound if missing, deleted, or wrong workspace
```

---

## Cascade behavior

When a parent record is soft-deleted, decide explicitly how children are handled:

| Pattern | When to use | Implementation |
|---|---|---|
| **Cascade soft-delete** | Children are meaningless without the parent | Set `is_deleted=True` on all children in the same command |
| **Orphan-protect** | Children have independent value or references | Block parent deletion if children exist |
| **No cascade** | Children are unaffected (e.g., global reference data) | Do nothing — children remain queryable |

Document the cascade strategy in the domain's `docs/domains/<domain>/states.md`.

Example — cascade to child items:

```python
with db.session.begin():
    now = datetime.now(timezone.utc)
    record.is_deleted = True
    record.deleted_at = now

    # cascade to line items
    for item in record.line_items:
        item.is_deleted = True
        item.deleted_at = now
```

Never rely on the database `ON DELETE CASCADE` for soft deletes — it only fires on hard deletes.

---

## Restore (undo soft delete)

If the domain supports restore, implement it as an explicit command:

```python
def restore_record(ctx: ServiceContext) -> dict:
    request = parse_restore_record_request(ctx.incoming_data)
    # include_deleted=True is required — restore must find the logically deleted record
    record = resolve_record(ctx, request.ref, include_deleted=True)
    if not record.is_deleted:
        raise ValidationFailed("Record is not deleted and cannot be restored.")

    with db.session.begin():
        record.is_deleted = False
        record.deleted_at = None

    return {"restored": True, "client_id": record.client_id}
```

---

## Migration for adding soft delete to existing tables

When retrofitting soft delete onto an existing table, the migration must:
1. Add both columns in a single migration.
2. Backfill existing rows: `UPDATE records SET is_deleted = FALSE WHERE is_deleted IS NULL`.
3. Then apply `NOT NULL` and `DEFAULT FALSE`.

```python
# migrations/versions/xxxx_add_soft_delete_to_records.py
def upgrade():
    op.add_column("records", sa.Column("is_deleted", sa.Boolean(), nullable=True))
    op.add_column("records", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE records SET is_deleted = FALSE WHERE is_deleted IS NULL")
    op.alter_column("records", "is_deleted", nullable=False)
    op.create_index("ix_records_is_deleted", "records", ["is_deleted"])
```

---

## Naming — when a soft delete field is named differently

Some tables may use a semantically clearer name. The convention is:

| Column name | Use when |
|---|---|
| `is_deleted` / `deleted_at` | General purpose record deletion |
| `is_archived` / `archived_at` | Records moved out of active view but retained (e.g., completed items) |
| `is_active` | Boolean that represents current state (use when deletion is not the concept) |

Do not mix naming conventions within a domain. Use the same pair for the entire domain's tables.

---

## Review checklist

- [ ] Both `is_deleted` and `deleted_at` are present
- [ ] `is_deleted` has an index
- [ ] All queries filter `is_deleted == False`
- [ ] Single-record fetches check `is_deleted`
- [ ] Cascade strategy is explicit and documented
- [ ] Restore command exists if the domain supports it
- [ ] Migration backfills existing rows before applying `NOT NULL`
