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

## Base class

All models inherit from a single `Base` class defined in `models/base.py`:

```python
# models/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

There is no Flask-SQLAlchemy `db` instance. Session management lives in `models/database.py` (see [02_app_factory.md](02_app_factory.md)).

```python
# models/__init__.py
from my_app.models.base import Base

# Import every table so SQLAlchemy's mapper is fully configured before migrations run.
from .tables.<domain>.record import Record
from .tables.<domain>.record_state import RecordState
# ... all tables
```

Every new table must be imported in `models/__init__.py`. This is required for Alembic's `autogenerate` to detect the table.

---

## Table file contract

One table = one file. File lives in `models/tables/<domain>/<table_name>.py`.

```python
# models/tables/<domain>/record.py
from datetime import datetime, timezone
from sqlalchemy import String, ForeignKey, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base import Base
from my_app.models.base.identity import IdentityMixin


class Record(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "rec"
    __tablename__ = "records"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    line_items: Mapped[list["LineItem"]] = relationship(
        "LineItem", back_populates="record", lazy="raise"
    )
```

**Column rules:**
- Use SQLAlchemy 2.x `Mapped` + `mapped_column` style. Never use the legacy `Column()` style for new tables.
- All datetime columns use `DateTime(timezone=True)`. The engine is configured for UTC.
- All foreign key columns are `nullable=False` unless there is an explicit domain reason for nullability.
- Every addressable table uses `IdentityMixin`; `client_id` is the prefixed ULID primary key. See [40_identity.md](40_identity.md).
- Foreign key columns use `String(64)` and reference `<table>.client_id`. Keep semantic FK column names (`workspace_id`, `user_id`, `record_id`) even though they store `client_id` values.

**Mandatory index rules (apply at table creation, not after observing slowness):**
- Every FK column must have `index=True`. Postgres does not auto-index FK columns and they appear in virtually every `JOIN` and `WHERE`.
- `workspace_id` must be indexed on every domain table — it is the mandatory first filter in all multi-tenant list queries.
- `created_at` must be indexed on tables where list queries sort by time.
- State/status enum columns on high-traffic tables that filter by state must be indexed.

**Composite indexes** for queries that always filter on two columns together:

```python
from sqlalchemy import Index

class Case(IdentityMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_workspace_state", "workspace_id", "state"),
    )
```

Index naming: `ix_{table}_{columns}` — e.g., `ix_cases_workspace_state`. Never add indexes speculatively on columns that do not appear in queries — indexes have a write cost on every `INSERT` and `UPDATE`.

**Relationship rules:**
- Default relationship loading is `lazy="raise"` — not `"select"`. In async SQLAlchemy, lazy loading raises `MissingGreenlet` at runtime if accessed outside an active session; `lazy="raise"` makes this fail at development time with a clear error instead of silently in production.
- Always load relationships explicitly via `selectinload()` or `joinedload()` in queries. Never rely on implicit loading.

---

## UTC everywhere

All datetime storage is UTC. The engine option `server_settings={"timezone": "UTC"}` (set in `models/database.py`) enforces this at the Postgres level. Never store a naive datetime — always use `datetime.now(timezone.utc)`.

Timezone-aware display is a presentation concern handled at the serialization layer.

---

## Migrations

Alembic is used directly — no Flask-Migrate wrapper.

```
alembic/
├── env.py
├── script.py.mako
└── versions/
    └── *.py
```

`alembic/env.py` imports `Base` and all models so autogenerate can diff the schema:

```python
# alembic/env.py
from my_app.models import Base   # triggers all table imports
target_metadata = Base.metadata
```

Commands:

```bash
alembic revision --autogenerate -m "add records table"
alembic upgrade head
```

**Rules:**
- Never alter a table manually. Generate a migration, review the file, then apply.
- Never modify an already-applied migration. Create a new one.
- Backfill scripts must be idempotent. Use `ON CONFLICT DO NOTHING` or check existence before inserting.
- Destructive migrations (dropping columns, tables) require an explicit team confirmation.
- Read the existing migration chain before writing a new one.

---

## `updated_at` column

Every table that is modified after creation must have an `updated_at` column:

```python
class Record(Base):
    __tablename__ = "records"

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

Append-only tables (event logs, outbox rows) do not need `updated_at`.

---

## Enum columns

```python
import enum
from sqlalchemy import Enum as SAEnum


class RecordStatus(enum.Enum):
    DRAFT  = "draft"
    ACTIVE = "active"
    CLOSED = "closed"


class Record(Base):
    status: Mapped[RecordStatus] = mapped_column(
        SAEnum(RecordStatus, name="record_status_enum", create_type=True),
        nullable=False,
        default=RecordStatus.DRAFT,
    )
```

- Define enums in `domain/<domain>/enums.py`. Both `models/` and `domain/` import from there.
- Use `create_type=True` to let Alembic create the Postgres `ENUM` type automatically.
- Adding new values to an existing enum requires a migration (`ALTER TYPE ... ADD VALUE`). Plan enum fields conservatively.

---

## Composite unique constraints

```python
from sqlalchemy import UniqueConstraint


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"

    user_id:      Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id"),      nullable=False)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id"), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_memberships_user_workspace"),
    )
```

Naming convention: `uq_<table>_<columns>`.

---

## Naming conventions

| Entity | Convention | Example |
|---|---|---|
| Table name | snake_case plural | `records`, `categories` |
| Model class | PascalCase singular | `Record`, `Category` |
| Foreign key column | `<referenced_table_singular>_id` | `workspace_id`, `record_id` |
| Client-facing ID | `client_id` | Prefixed ULID string |
| Timestamp columns | `*_at` for events, `*_date` for dates | `created_at`, `scheduled_date` |
| Updated timestamp | `updated_at` | Always UTC, uses `onupdate` |
| Enum type name | `<column>_enum` in Postgres | `record_status_enum` |
| Unique constraint | `uq_<table>_<columns>` | `uq_workspace_memberships_user_workspace` |
