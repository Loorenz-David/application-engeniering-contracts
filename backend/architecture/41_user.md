# 41 — User Model & History Record Pattern

## Overview

This contract defines two foundational things used across all applications:

1. **`HistoryRecord` mixin** — a reusable base for any domain entity that needs a change log. Applies to `User`, `Customer`, `Order`, `Record`, or any other entity where you need to track what changed, when, by whom, and why.
2. **`User` model** — the core user table, with `UserAppViewRecord` and `UserHistoryRecord` as its concrete applications of the pattern.

---

## `HistoryRecord` mixin — `models/base/history_record.py`

`HistoryRecord` is a base mixin that lives in `models/base/`. It provides the standard audit columns. You apply it to any domain entity that needs a change log by creating a `<Entity>HistoryRecord` table that inherits from both `IdentityMixin` and `HistoryRecord`.

```python
# models/base/history_record.py
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, declared_attr


class HistoryRecord:
    """
    Mixin for history/audit tables. Captures what changed, when, by whom, and why.
    Always combine with IdentityMixin:
      class MyHistoryRecord(IdentityMixin, HistoryRecord, db.Model)
    """

    @declared_attr
    def updated_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True,
    )
    from_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    to_value: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
```

`updated_by_id` uses `@declared_attr` because it contains a `ForeignKey` — every concrete table that inherits this mixin must get its own column instance. Plain columns (`updated_at`, `from_value`, `to_value`, `reason`) are safe to share.

**Column semantics:**

| Column | Meaning |
|---|---|
| `updated_by_id` | The user who made the change |
| `updated_at` | When the change was committed |
| `from_value` | JSON snapshot of the relevant fields **before** the change |
| `to_value` | JSON snapshot of the relevant fields **after** the change |
| `reason` | Human-readable explanation — optional but encouraged |

---

## Applying `HistoryRecord` to any entity

The pattern is always the same:

**Step 1 — Create `<Entity>HistoryRecord`:**

```python
class <Entity>HistoryRecord(IdentityMixin, HistoryRecord, db.Model):
    CLIENT_ID_PREFIX = "<prefix>"
    __tablename__ = "<entity>_history_records"

    <entity>_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("<entity_table>.client_id"), nullable=False, index=True
    )

    <entity>: Mapped["<Entity>"] = relationship(
        "<Entity>", foreign_keys=[<entity>_id], back_populates="history_records"
    )
    updated_by: Mapped["User"] = relationship(
        "User", foreign_keys="[<Entity>HistoryRecord.updated_by_id]"
    )
```

`updated_by` must declare `foreign_keys` explicitly because every history table has multiple `client_id`-backed FKs (`<entity>_id` goes to the parent entity; `updated_by_id` goes to User).

**Step 2 — Add the FK pointer + relationships to the parent entity:**

```python
class <Entity>(IdentityMixin, db.Model):
    ...
    last_history_record_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("<entity>_history_records.client_id"), nullable=True
    )

    history_records: Mapped[list["<Entity>HistoryRecord"]] = relationship(
        "<Entity>HistoryRecord",
        foreign_keys="[<Entity>HistoryRecord.<entity>_id]",
        back_populates="<entity>",
    )
    last_history_record: Mapped["<Entity>HistoryRecord | None"] = relationship(
        "<Entity>HistoryRecord",
        foreign_keys="[<Entity>.last_history_record_id]",
    )
```

`last_history_record_id` is a FK shortcut — O(1) lookup for the most recent change without a `ORDER BY + LIMIT 1` query. It is updated by the same command that creates the history row, in the same transaction.

---

## Concrete example — Customer

```python
# models/tables/customers/customer_history_record.py
class CustomerHistoryRecord(IdentityMixin, HistoryRecord, db.Model):
    CLIENT_ID_PREFIX = "chr"
    __tablename__ = "customer_history_records"

    customer_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("customers.client_id"), nullable=False, index=True
    )

    customer: Mapped["Customer"] = relationship(
        "Customer", foreign_keys=[customer_id], back_populates="history_records"
    )
    updated_by: Mapped["User"] = relationship(
        "User", foreign_keys="[CustomerHistoryRecord.updated_by_id]"
    )
```

```python
# models/tables/customers/customer.py  (relevant columns only)
class Customer(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "cust"
    __tablename__ = "customers"
    ...

    last_history_record_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("customer_history_records.client_id"), nullable=True
    )

    history_records: Mapped[list["CustomerHistoryRecord"]] = relationship(
        "CustomerHistoryRecord",
        foreign_keys="[CustomerHistoryRecord.customer_id]",
        back_populates="customer",
    )
    last_history_record: Mapped["CustomerHistoryRecord | None"] = relationship(
        "CustomerHistoryRecord",
        foreign_keys="[Customer.last_history_record_id]",
    )
```

---

## Writing a history entry from a command

```python
# services/commands/customer/update_customer.py
def update_customer(ctx: ServiceContext) -> dict:
    customer = resolve_customer(ctx, ctx.incoming_data["customer_client_id"])

    from_snapshot = {"name": customer.name, "email": customer.email}

    customer.name  = ctx.incoming_data.get("name", customer.name)
    customer.email = ctx.incoming_data.get("email", customer.email)

    history = CustomerHistoryRecord(
        customer_id=customer.client_id,
        updated_by_id=ctx.user_id,
        from_value=from_snapshot,
        to_value={"name": customer.name, "email": customer.email},
        reason=ctx.incoming_data.get("reason"),
    )
    db.session.add(history)
    db.session.flush()                                      # assign history.client_id if generated by default
    customer.last_history_record_id = history.client_id     # update the pointer

    db.session.commit()
    return {"customer_id": customer.client_id}
```

`db.session.flush()` before setting `last_history_record_id` is required when the history `client_id` is generated by the model default. The pointer and the row are committed atomically.

---

## `User` — `models/tables/users/user.py`

The `User` model is the primary application of this pattern. It uses `UserHistoryRecord` exactly as described above.

```python
# models/tables/users/user.py
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class User(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "usr"
    __tablename__ = "users"

    # ── Timestamps & provenance ───────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=True
    )

    # ── Identity ──────────────────────────────────────────────────────────────
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    phone_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)

    # ── Localisation ─────────────────────────────────────────────────────────
    languages: Mapped[str | None] = mapped_column(String(512), nullable=True)
    language_preference: Mapped[str | None] = mapped_column(String(16), nullable=True)

    # ── Profile ───────────────────────────────────────────────────────────────
    profile_picture: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # ── Presence ─────────────────────────────────────────────────────────────
    online: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_online: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # FK shortcuts — updated in the same transaction as the new child record
    last_app_view_record_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_app_view_records.client_id"), nullable=True
    )
    last_history_record_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("user_history_records.client_id"), nullable=True
    )

    # ── Relationships ─────────────────────────────────────────────────────────
    created_by: Mapped["User | None"] = relationship(
        "User",
        foreign_keys="[User.created_by_id]",
        primaryjoin="User.created_by_id == User.client_id",
    )
    app_view_records: Mapped[list["UserAppViewRecord"]] = relationship(
        "UserAppViewRecord", back_populates="user"
    )
    user_history_records: Mapped[list["UserHistoryRecord"]] = relationship(
        "UserHistoryRecord",
        foreign_keys="[UserHistoryRecord.user_id]",
        back_populates="user",
    )
    last_app_view_record: Mapped["UserAppViewRecord | None"] = relationship(
        "UserAppViewRecord",
        foreign_keys="[User.last_app_view_record_id]",
    )
    last_history_record: Mapped["UserHistoryRecord | None"] = relationship(
        "UserHistoryRecord",
        foreign_keys="[User.last_history_record_id]",
    )
```

**Column notes:**
- `password` — store only bcrypt hashes. Never plaintext. Use `werkzeug.security.generate_password_hash`. See [10_auth.md](10_auth.md).
- `languages` — comma-separated IETF language tags: `"en,es,fr"`.
- `profile_picture` — stores the file `client_id` string. Resolve via the file storage layer (see [34_file_storage.md](34_file_storage.md)).
- `created_by_id` — nullable. The first user (superadmin or seeded user) has no creator.
- **No `role_id` or `workspace_id` lives on `User`.** Roles are resolved through the active `WorkspaceMembership` (`User → WorkspaceMembership → WorkspaceRole → Role`). See [24_multi_tenancy.md](24_multi_tenancy.md).
- `online` / `last_online` — maintained by the socket presence layer only (see [13_sockets.md](13_sockets.md)).
- There is no `currently_viewing` column on `User`. Real-time presence is owned by Redis and `ConnectionMeta`; history is owned by `UserAppViewRecord`. See [48_presence.md](48_presence.md).

---

## `UserAppViewRecord` — `models/tables/users/user_app_view_record.py`

Tracks each session a user spends viewing an entity. One row per continuous visit — created when the user sends `view_entity`, `ended_at` written when they send `leave_entity` or disconnect.

`entity_type` stores an `EntityType` enum value (see [48_presence.md](48_presence.md)). `entity_client_id` is the entity's `client_id`; it is null for list-page views that have no single entity identity.

```python
# models/tables/users/user_app_view_record.py
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class UserAppViewRecord(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "uavr"
    __tablename__ = "user_app_view_records"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="app_view_records")
```

---

## `UserHistoryRecord` — `models/tables/users/user_history_record.py`

```python
# models/tables/users/user_history_record.py
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin
from my_app.models.base.history_record import HistoryRecord


class UserHistoryRecord(IdentityMixin, HistoryRecord, db.Model):
    CLIENT_ID_PREFIX = "uhr"
    __tablename__ = "user_history_records"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="user_history_records"
    )
    updated_by: Mapped["User"] = relationship(
        "User", foreign_keys="[UserHistoryRecord.updated_by_id]"
    )
```

---

## Prefix registry additions

| Model | Table | Prefix | Example |
|---|---|---|---|
| `User` | `users` | `usr` | `usr_01ARYZ6S41...` |
| `UserAppViewRecord` | `user_app_view_records` | `uavr` | `uavr_01ARYZ6S41...` |
| `UserHistoryRecord` | `user_history_records` | `uhr` | `uhr_01ARYZ6S41...` |

Domain-specific history tables follow the same naming: `<Entity>HistoryRecord` → prefix `<short>hr` (e.g. `CustomerHistoryRecord` → `chr`).

---

## File structure

```
my_app/
├── models/
│   ├── base/
│   │   ├── identity.py            # IdentityMixin → 40
│   │   └── history_record.py      # HistoryRecord (reusable)
│   └── tables/
│       ├── users/
│       │   ├── user.py
│       │   ├── user_app_view_record.py
│       │   └── user_history_record.py
│       └── <domain>/
│           ├── <entity>.py               # has last_history_record_id FK
│           └── <entity>_history_record.py
```

---

## Rules

### `HistoryRecord` pattern — applies to all entities

- **History rows are append-only.** Never update or delete a history record. To reverse a change, write a new row documenting the reversal.
- **Always write a history row in the same transaction as the mutation.** If the mutation commits but the history row does not, the audit trail is broken.
- **`flush()` before setting `last_history_record_id`.** `flush()` assigns the generated `client_id` without committing when the default generated it. The pointer and the history row commit atomically.
- **`last_history_record_id` is updated by the command that creates the new history row**, not by a background process or trigger. Never derive the "latest" record with a query.
- **`from_value` / `to_value` contain only the fields changed in that operation**, not a full row snapshot.

### `User` — specific rules

- **Never return `password` in any API response.**
- **Never log passwords** — not even on sign-in failure.
- **`created_by_id` is set once at creation.** Never update it.
- **`online` and `last_online` are written by the socket/presence layer only**, not by user-facing commands.
- **`last_app_view_record_id` is updated by the `RECORD_VIEW_START` background task** (see [48_presence.md](48_presence.md)), not in the socket handler.
- **`UserAppViewRecord` rows are written by background tasks, not by the socket handler directly.** The socket handler writes Redis presence inline; DB activity recording is always fire-and-forget.
- **`entity_type` must be a valid `EntityType` enum value** (see [48_presence.md](48_presence.md)). Never store free-form strings in this column.
