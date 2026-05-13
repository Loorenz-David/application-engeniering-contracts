# 40 — Identity Generation

## Purpose

Every addressable table carries one identifier:

| Column | Type | Purpose |
|---|---|---|
| `client_id` | `String(64)` PK | Prefixed ULID used for database identity, FKs, routes, events, and API payloads |

The `client_id` format is `{prefix}_{ULID}`, for example `task_01ARYZ6S41TSV4RRFFQ69G5FAV`.

**Why ULID over UUID:**
- Lexicographically sortable — `ORDER BY client_id` gives creation order
- Embeds millisecond timestamp — useful for debugging and auditing
- URL-safe — 26 chars, no hyphens, uppercase alphanumeric (Crockford Base32)
- Type prefix makes any `client_id` immediately identifiable in logs, errors, and support tickets

---

## Dependency

```
ulid-py>=2.2
```

---

## Generator — `services/infra/identity.py`

```python
# services/infra/identity.py
import ulid


def generate_id(prefix: str) -> str:
    return f"{prefix}_{ulid.new().str}"
```

`ulid.new().str` returns a 26-character uppercase ULID string. The resulting `client_id` is always `len(prefix) + 27` characters.

---

## `IdentityMixin` — `models/base/identity.py`

```python
# models/base/identity.py
from typing import ClassVar
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, declared_attr
from my_app.services.infra.identity import generate_id


class IdentityMixin:
    """
    Base mixin for all tables. Provides:
      client_id — prefixed ULID string primary key

    Inherit as: class MyModel(IdentityMixin, db.Model)
    Set CLIENT_ID_PREFIX on the concrete model class.
    """

    CLIENT_ID_PREFIX: ClassVar[str] = "obj"

    @declared_attr
    def client_id(cls) -> Mapped[str]:
        prefix = cls.CLIENT_ID_PREFIX
        return mapped_column(
            String(64), primary_key=True,
            default=lambda: generate_id(prefix),
        )
```

`@declared_attr` ensures each concrete model class gets its own column definition with the correct prefix captured at class construction time — not shared across classes.

---

## Usage on a concrete model

```python
from my_app.models.base.identity import IdentityMixin
from my_app.models import db


class Record(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "rec"
    __tablename__ = "records"

    # ... domain columns ...
```

Generated `client_id` values: `rec_01ARYZ6S41TSV4RRFFQ69G5FAV`, `rec_01ARYZ6S41TSV4RRFFQ69G5FAX`, ...

---

## Built-in prefix registry

Every model that ships with the framework scaffold uses a registered prefix. When you add a domain model, register its prefix here.

| Model | Table | Prefix | Example |
|---|---|---|---|
| `ExecutionTask` | `execution_tasks` | `task` | `task_01ARYZ6S41...` |
| `ExecutionPayload` | `execution_payloads` | `epl` | `epl_01ARYZ6S41...` |
| `DelayedScheduler` | `delayed_schedulers` | `dsch` | `dsch_01ARYZ6S41...` |
| `RecurringScheduler` | `recurring_schedulers` | `rsch` | `rsch_01ARYZ6S41...` |
| `Role` | `roles` | `role` | `role_01ARYZ6S41...` |
| `UIGroupPermissions` | `ui_group_permissions` | `uig` | `uig_01ARYZ6S41...` |
| `BackendGroupPermissions` | `backend_group_permissions` | `bkg` | `bkg_01ARYZ6S41...` |
| `AppName` | `app_names` | `apn` | `apn_01ARYZ6S41...` |
| `PageName` | `page_names` | `pgn` | `pgn_01ARYZ6S41...` |
| `ButtonName` | `button_names` | `btn` | `btn_01ARYZ6S41...` |
| `ActionName` | `action_names` | `act` | `act_01ARYZ6S41...` |
| `QueryFilter` | `query_filters` | `qfl` | `qfl_01ARYZ6S41...` |
| `Endpoints` | `endpoints` | `endp` | `endp_01ARYZ6S41...` |

| `User` | `users` | `usr` | `usr_01ARYZ6S41...` |
| `UserAppViewRecord` | `user_app_view_records` | `uavr` | `uavr_01ARYZ6S41...` |
| `UserHistoryRecord` | `user_history_records` | `uhr` | `uhr_01ARYZ6S41...` |

**Your domain models:** choose a short (2–5 char) lowercase prefix that does not collide with any entry above. Register it in this table.

---

## Prefix naming rules

- Lowercase letters only — no numbers, no underscores
- 2–5 characters
- Must be unique across the entire application
- Must be stable — changing a prefix invalidates all existing `client_id` values in storage and any external references

---

## Rules

- **There is no internal integer `id` on addressable tables.** `client_id` is the primary key and the public identifier.
- **`CLIENT_ID_PREFIX` must be set on every concrete model.** The fallback `"obj"` is intentionally generic — it is a signal that the prefix has not been defined, not a valid production value.
- **Backend application code should rely on the model default.** The column default calls `generate_id` at insert time. Create commands may accept a caller-provided `client_id` for optimistic frontend flows; otherwise omit it and let the default run.
- **`client_id` is immutable after creation.** Never update it. A changed `client_id` is an identity change — break external references, bookmarks, and audit trails.
- **Foreign keys reference `client_id`.** FK columns keep semantic names like `workspace_id`, `user_id`, and `record_id`, but their type is `String(64)` and they target `<table>.client_id`.
- **Junction / association tables do not need `IdentityMixin` when they are never directly addressed.** Use a composite primary key across the referenced `client_id` FK columns.

---

## File location

```
my_app/
├── models/
│   └── base/
│       ├── identity.py       ← IdentityMixin + CLIENT_ID_PREFIX
│       └── history_record.py ← HistoryRecord mixin (see 41_user.md)
└── services/
    └── infra/
        └── identity.py       ← generate_id()
```

`models/base/` is the canonical location for all reusable SQLAlchemy mixins. Add new base mixins here — never in a domain table file.
