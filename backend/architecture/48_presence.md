# 48 — Presence & View Activity Contract

## Purpose

Two layers, two responsibilities:

| Layer | State | Mechanism | Drives |
|---|---|---|---|
| **Redis** | Who is viewing what, right now | `presence.mark_viewing` / `presence.mark_left` | Notification exclusion (`exclude_viewing`) |
| **DB** | When a user started and stopped viewing each entity | `UserAppViewRecord` rows | Analytics, support tooling, user activity history |

The `EntityType` enum is the canonical source for all entity type strings across both layers, the socket protocol, and notification dispatch payloads. Nothing in the codebase passes raw entity type strings.

---

## `EntityType` — `domain/presence/enums.py`

```python
import enum


class EntityType(str, enum.Enum):
    CASE_LIST         = "case_list"         # list-page view; entity_client_id = workspace_client_id
    CASE              = "case"
    CONVERSATION_LIST = "conversation_list" # list-page view; entity_client_id = workspace_client_id
    CONVERSATION      = "conversation"

    # Extend here when a new domain gains presence support.
    # Never pass raw entity_type strings in application code — import from this enum.
```

**List-page views** (`CASE_LIST`, `CONVERSATION_LIST`) have no single-entity `client_id`. Use the workspace `client_id` as `entity_client_id`. This lets `CREATE_NOTIFICATIONS` union viewers across individual entity views and the workspace list page with the same `exclude_viewing` loop.

---

## Architecture

```
Frontend sends view_entity / leave_entity via WebSocket
│
├── EntityType validation (socket handler, inline)
│     invalid type → silent ignore
│
├── Layer 1 — Redis presence (inline, blocking)
│     view_entity  → presence.mark_viewing(entity_type, entity_client_id, user_id)
│     leave_entity → presence.mark_left(entity_type, entity_client_id, user_id)
│     disconnect   → mark_left for all entries in ConnectionMeta.entity_views
│
└── Layer 2 — DB activity record (background task, fire-and-forget)
      view_entity  → TaskType.RECORD_VIEW_START
      leave_entity → TaskType.RECORD_VIEW_END
      disconnect   → TaskType.RECORD_VIEW_END for all tracked views
                │
                DB: INSERT / UPDATE UserAppViewRecord
                    (entity_type, entity_client_id, started_at, ended_at)
```

The two layers are independent. Redis failure does not block activity recording. DB task failure does not affect notification exclusion.

---

## Socket handler integration

The socket handler validates `entity_type` against `EntityType` before writing to either layer. Unknown types are silently ignored.

```python
# routers/websocket/handlers.py
from my_app.domain.presence.enums import EntityType
from my_app.services.infra.presence import presence
from my_app.services.infra.execution.task_factory import create_instant_task
from my_app.domain.execution.enums import TaskType


def _handle_view_entity(websocket: WebSocket, meta: ConnectionMeta, msg: dict) -> None:
    try:
        entity_type = EntityType(msg.get("entity_type", ""))
    except ValueError:
        return

    entity_client_id: str = msg.get("entity_client_id", "")
    if not entity_client_id:
        return

    # Layer 1 — Redis (inline)
    presence.mark_viewing(entity_type.value, entity_client_id, meta.user_id)
    meta.entity_views.add((entity_type.value, entity_client_id))

    # Layer 2 — DB (background task)
    create_instant_task(TaskType.RECORD_VIEW_START, {
        "user_id":          meta.user_id,
        "entity_type":      entity_type.value,
        "entity_client_id": entity_client_id,
    })

    # Domain-specific branching
    if entity_type == EntityType.CONVERSATION:
        manager.join_conversation(websocket, entity_client_id)
        _broadcast_presence(meta, entity_client_id, "conversation:user-joined")


def _handle_leave_entity(websocket: WebSocket, meta: ConnectionMeta, msg: dict) -> None:
    try:
        entity_type = EntityType(msg.get("entity_type", ""))
    except ValueError:
        return

    entity_client_id: str = msg.get("entity_client_id", "")
    if not entity_client_id:
        return

    # Layer 1 — Redis (inline)
    presence.mark_left(entity_type.value, entity_client_id, meta.user_id)
    meta.entity_views.discard((entity_type.value, entity_client_id))

    # Layer 2 — DB (background task)
    create_instant_task(TaskType.RECORD_VIEW_END, {
        "user_id":          meta.user_id,
        "entity_type":      entity_type.value,
        "entity_client_id": entity_client_id,
    })

    # Domain-specific branching
    if entity_type == EntityType.CONVERSATION:
        manager.leave_conversation(websocket, entity_client_id)
        _broadcast_presence(meta, entity_client_id, "conversation:user-left")
```

Disconnect cleanup fires `RECORD_VIEW_END` for every tracked view:

```python
def _cleanup_presence(ws: WebSocket) -> None:
    meta = manager._connections.get(ws)
    if meta is None:
        return
    for entity_type, entity_client_id in meta.entity_views:
        presence.mark_left(entity_type, entity_client_id, meta.user_id)
        create_instant_task(TaskType.RECORD_VIEW_END, {
            "user_id":          meta.user_id,
            "entity_type":      entity_type,
            "entity_client_id": entity_client_id,
        })
```

---

## Background task handlers

### `RECORD_VIEW_START` — `services/tasks/presence/record_view_start.py`

Creates a new `UserAppViewRecord` and updates the `last_app_view_record_id` pointer on `User`.

The task payload carries `user_id` = the JWT `user_id` claim = `user.client_id`. Validate it through the identity lookup before writing.

```python
from datetime import datetime, timezone
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.tables.users.user import User
from my_app.models.tables.users.user_app_view_record import UserAppViewRecord
from my_app.services.infra.execution.db import get_db_session
from my_app.services.infra.identity import resolve_user_client_id


async def record_view_start_handler(raw: dict) -> None:
    user_client_id   = raw["user_id"]
    entity_type      = raw["entity_type"]
    entity_client_id = raw["entity_client_id"]

    async with get_db_session() as session:
        resolved_user_id = await resolve_user_client_id(session, user_client_id)

        record = UserAppViewRecord(
            user_id=resolved_user_id,
            entity_type=entity_type,
            entity_client_id=entity_client_id,
        )
        session.add(record)
        await session.flush()   # assign record.client_id before the pointer update

        await session.execute(
            update(User)
            .where(User.client_id == resolved_user_id)
            .values(last_app_view_record_id=record.client_id)
        )
        await session.commit()
```

### `RECORD_VIEW_END` — `services/tasks/presence/record_view_end.py`

Finds the latest open `UserAppViewRecord` for the user + entity pair and sets `ended_at`. Idempotent — if no open record exists (e.g. duplicate disconnect signal), it does nothing.

```python
from datetime import datetime, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.tables.users.user_app_view_record import UserAppViewRecord
from my_app.services.infra.execution.db import get_db_session
from my_app.services.infra.identity import resolve_user_client_id


async def record_view_end_handler(raw: dict) -> None:
    user_client_id   = raw["user_id"]
    entity_type      = raw["entity_type"]
    entity_client_id = raw["entity_client_id"]

    async with get_db_session() as session:
        resolved_user_id = await resolve_user_client_id(session, user_client_id)

        stmt = (
            select(UserAppViewRecord)
            .where(
                UserAppViewRecord.user_id          == resolved_user_id,
                UserAppViewRecord.entity_type      == entity_type,
                UserAppViewRecord.entity_client_id == entity_client_id,
                UserAppViewRecord.ended_at.is_(None),
            )
            .order_by(UserAppViewRecord.started_at.desc())
            .limit(1)
        )
        record = (await session.execute(stmt)).scalar_one_or_none()

        if record is not None:
            record.ended_at = datetime.now(timezone.utc)
            await session.commit()
```

---

## Querying view activity

View records are for analytics and support tooling — never queried on the hot path.

```python
# services/queries/users/user_view_activity.py
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.tables.users.user_app_view_record import UserAppViewRecord


async def list_user_view_activity(
    session: AsyncSession,
    *,
    user_id: str,
    limit: int = 50,
) -> list[UserAppViewRecord]:
    stmt = (
        select(UserAppViewRecord)
        .where(UserAppViewRecord.user_id == user_id)
        .order_by(desc(UserAppViewRecord.started_at))
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()
```

---

## File structure

```
my_app/
├── domain/
│   └── presence/
│       └── enums.py                        # EntityType — imported by sockets, tasks, notifications
├── services/
│   ├── infra/
│   │   └── presence/
│   │       └── presence.py                 # mark_viewing / mark_left / get_viewers → 47
│   └── tasks/
│       └── presence/
│           ├── record_view_start.py        # RECORD_VIEW_START handler
│           └── record_view_end.py          # RECORD_VIEW_END handler
└── models/
    └── tables/
        └── users/
            └── user_app_view_record.py     # entity_type + entity_client_id columns → 41
```

---

## View record debouncing

`UserAppViewRecord` grows fast — a single user navigating actively generates hundreds of rows per hour. At 1 000 concurrent users this becomes millions of rows per day.

The `RECORD_VIEW_START` handler debounces writes: if an open record for the same user + entity already exists and was started within `DEBOUNCE_WINDOW_SECONDS`, no new row is inserted — the existing row continues:

```python
# services/tasks/presence/record_view_start.py
from datetime import datetime, timezone, timedelta
from sqlalchemy import select

DEBOUNCE_WINDOW_SECONDS = 30   # configurable via settings.presence_debounce_seconds


async def handle_record_view_start(raw: dict, task_id: str) -> None:
    ...
    async with task_db_session() as session:
        now = datetime.now(timezone.utc)
        window = now - timedelta(seconds=DEBOUNCE_WINDOW_SECONDS)

        existing = (await session.execute(
            select(UserAppViewRecord)
            .where(
                UserAppViewRecord.user_id          == resolved_user_id,
                UserAppViewRecord.entity_type      == entity_type,
                UserAppViewRecord.entity_client_id == entity_client_id,
                UserAppViewRecord.ended_at.is_(None),
                UserAppViewRecord.started_at       >= window,
            )
            .limit(1)
        )).scalar_one_or_none()

        if existing is not None:
            return   # within debounce window — extend the existing record silently

        record = UserAppViewRecord(
            user_id=resolved_user_id,
            entity_type=entity_type,
            entity_client_id=entity_client_id,
        )
        session.add(record)
        await session.flush()
        await session.execute(
            update(User).where(User.client_id == resolved_user_id)
            .values(last_app_view_record_id=record.client_id)
        )
        await session.commit()
```

**Expected reduction:** at 100 events/user/hour → approximately 3 200 rows/user/hour with 30s debounce → 97% reduction in row count.

**Rule:** `DEBOUNCE_WINDOW_SECONDS` is always sourced from settings (`settings.presence_debounce_seconds`, default `30`). Never hardcode it — the correct window depends on the application's UX patterns.

---

## Retention policy

`UserAppViewRecord` is an activity log — it grows without bound. Implement a background cleanup job or Postgres partition strategy:

- **Recommended:** Postgres table partitioning by `started_at` month. Drop partitions older than the retention window (e.g., 90 days).
- **Alternative:** A nightly `DELETE FROM user_app_view_records WHERE started_at < now() - interval '90 days'` via a scheduled task.

Retention window is an operational decision. Document it in `docs/privacy/retention_policy.md` before going live.

---

## Rules

- **`EntityType` is the only valid source for entity type strings.** Never pass raw strings in socket handlers, task payloads, or `exclude_viewing` lists. Import `EntityType` and use its `.value`.
- **Redis writes are inline; DB writes are background tasks.** The socket handler must not write `UserAppViewRecord` synchronously. Redis is the blocking path; the DB task is fire-and-forget.
- **`RECORD_VIEW_START` is debounced.** The handler checks for a recent open record before inserting. This is the primary defence against unbounded table growth.
- **`RECORD_VIEW_END` is idempotent.** Multiple disconnect signals for the same entity produce no duplicate writes — the handler finds no open record and exits cleanly.
- **Multi-tab concurrent views are valid.** Multiple open `UserAppViewRecord` rows for the same user + entity pair are allowed. `RECORD_VIEW_END` closes the latest one.
- **List-page views use workspace `client_id` as `entity_client_id`.** There is no list-page `client_id` — the workspace is the stable identifier for that view context.
- **`app_viewing` does not exist on `User`.** Real-time state lives in `ConnectionMeta.entity_views` (in-process) and Redis (cross-process). History lives in `UserAppViewRecord`. Do not add a "currently viewing" column to `User`.
- **Extend `EntityType` when a new domain gains presence support.** Add the value to the enum and add a migration entry for the new string if any index or query references it by value.
