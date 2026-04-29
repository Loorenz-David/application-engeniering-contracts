# 03 — Model Contract

## What models are

Models are SQLAlchemy table definitions. They describe columns, relationships, and constraints. That is all.

**Models must not contain:**
- Business logic
- Validation logic
- Computed fields that encode business rules
- Methods that perform writes or side effects

If you find yourself writing a `Record.can_be_deleted()` method on a model, that logic belongs in `domain/<domain>/record_guards.py`.

---

## `db` instance

A single SQLAlchemy instance is created once:

```python
# models/__init__.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
```

All table imports follow in `models/__init__.py` so SQLAlchemy's mapper is fully configured before the app starts. Every new table must be imported here.

```python
# models/__init__.py — after db definition
from .tables.<domain>.record import Record
from .tables.<domain>.record_state import RecordState
# ... all tables
```

Do not import `db` from anywhere else. Always import from `my_app.models`.

---

## Table file contract

One table = one file. File lives in `models/tables/<domain>/<table_name>.py`.

```python
# models/tables/<domain>/record.py
from datetime import datetime, timezone
from my_app.models import db
from sqlalchemy import String, Integer, ForeignKey, DateTime
from sqlalchemy.orm import relationship, Mapped, mapped_column

class Record(db.Model):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Relationships
    line_items: Mapped[list["LineItem"]] = relationship("LineItem", back_populates="record", lazy="select")
```

**Column rules:**
- Use SQLAlchemy 2.x `Mapped` + `mapped_column` style. Never use the legacy `Column()` style for new tables.
- All datetime columns use `DateTime(timezone=True)`. The engine is configured for UTC (`-c timezone=UTC`).
- All foreign key columns are `nullable=False` unless there is an explicit domain reason for nullability.
- Every user-facing table has a `client_id` column (string UUID) that uniquely identifies the record to the client. This decouples API identity from DB identity. See [38_identity_resolution.md](38_identity_resolution.md).
- Indexes go on columns used in `WHERE` clauses in queries. Explain the index in a migration comment if it is non-obvious.

**Relationship rules:**
- Default relationship loading is `lazy="select"` (explicit, not inherited). Change only with a documented reason.
- Use `selectinload` / `joinedload` in queries when you need related data, rather than setting `lazy="joined"` on the relationship definition.

---

## UTC everywhere

All datetime storage is UTC. The SQLAlchemy engine option `"-c timezone=UTC"` enforces this at the Postgres level. Never store a naive datetime — always use `datetime.now(timezone.utc)`.

Timezone-aware display is a presentation concern handled at the query serialization layer.

---

## Migrations

- **Never alter a table manually.** Run `flask db migrate` to auto-generate a migration, then review the generated file before applying it.
- **Never modify an already-applied migration.** Create a new one.
- **Backfill scripts** must be idempotent. Use `ON CONFLICT DO NOTHING` or check existence before inserting.
- **Destructive migrations** (dropping columns, tables) require an explicit confirmation from the team before execution.
- Read the existing migration chain (`migrations/versions/`) before writing a new one to understand the current schema state.

---

## `updated_at` column

Every table that is modified after creation must have an `updated_at` column that tracks the last write:

```python
class Record(db.Model):
    __tablename__ = "records"

    # ...
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

**Rules:**
- `updated_at` uses SQLAlchemy's `onupdate` hook — it updates automatically on every `UPDATE`.
- Both `created_at` and `updated_at` use `DateTime(timezone=True)` and default to UTC.
- Append-only tables (event logs, outbox rows) do not need `updated_at` — they are never updated.

---

## Enum columns

Use Python `enum.Enum` + SQLAlchemy `Enum` type for columns with a fixed set of string values:

```python
import enum
from sqlalchemy import Enum as SAEnum

class RecordStatus(enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    CLOSED = "closed"

class Record(db.Model):
    __tablename__ = "records"

    status: Mapped[RecordStatus] = mapped_column(
        SAEnum(RecordStatus, name="record_status_enum", create_type=True),
        nullable=False,
        default=RecordStatus.DRAFT,
    )
```

**Rules:**
- Define the Python `Enum` class in the same file as the model, or in a shared `domain/<domain>/enums.py` if it is used across multiple files.
- Use `create_type=True` to let Alembic create the Postgres `ENUM` type automatically.
- Never store string constants directly in a `String` column when the set of values is fixed and known at design time — use the typed enum.
- Adding new values to an existing enum requires a migration (`ALTER TYPE ... ADD VALUE`). Plan enum fields conservatively.

---

## Composite unique constraints

When uniqueness spans more than one column, use `__table_args__` with `UniqueConstraint`:

```python
from sqlalchemy import UniqueConstraint

class WorkspaceMembership(db.Model):
    __tablename__ = "workspace_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_memberships_user_workspace"),
    )
```

Naming convention for constraint: `uq_<table>_<columns>`.

Never enforce multi-column uniqueness in application code alone — the database constraint is the ground truth and prevents race conditions.

---

## Naming conventions

| Entity | Convention | Example |
|---|---|---|
| Table name | snake_case plural | `records`, `categories` |
| Model class | PascalCase singular | `Record`, `Category` |
| Foreign key column | `<referenced_table_singular>_id` | `workspace_id`, `record_id` |
| Client-facing ID | `client_id` | UUID string |
| Timestamp columns | `*_at` for events, `*_date` for dates | `created_at`, `scheduled_date` |
| Updated timestamp | `updated_at` | Always UTC, uses `onupdate` |
| Enum type name | `<column>_enum` in Postgres | `record_status_enum` |
| Unique constraint | `uq_<table>_<columns>` | `uq_workspace_memberships_user_workspace` |
