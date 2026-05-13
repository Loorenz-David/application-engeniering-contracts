# 42 — Event Record Pattern

## Overview

The `Event` mixin is a base for tables that track the lifecycle of an operation on a specific entity. Where `HistoryRecord` answers "what data changed?", `Event` answers "what operation was attempted, what happened, and why did it fail?".

An event record ties a domain intent (sync, notify, send) to the async execution system. A command creates the event row, hands it off to the worker via `create_instant_task()`, and the worker updates the event's state as it progresses. The parent entity holds a `last_event_id` FK pointer to the latest event's `client_id` for O(1) access — the same pattern as `last_history_record_id`.

---

## Shared state enum — `domain/base/enums.py`

`EventStateEnum` is shared across all event tables. It lives in `domain/base/` alongside other cross-cutting domain constants.

```python
# domain/base/enums.py
import enum


class EventStateEnum(enum.Enum):
    REQUESTED   = "requested"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    FAILED      = "failed"
```

The async execution task system uses `ExecutionTaskStateEnum` in `domain/execution/enums.py` — a separate, more granular state machine for the worker layer (OPEN, PENDING, IN_PROGRESS, RETRYING, RETRY_SCHEDULED, FAIL, CANCEL).

---

## `Event` mixin — `models/base/event.py`

```python
# models/base/event.py
import enum
from datetime import datetime, timezone
from typing import ClassVar
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, declared_attr
from my_app.domain.base.enums import EventStateEnum


class Event:
    """
    Mixin for event/operation tables. Tracks the lifecycle of a domain operation.
    Always combine with IdentityMixin:
      class MyEvent(IdentityMixin, Event, db.Model)
    Set EVENT_TYPE_ENUM and EVENT_ERROR_ENUM on the concrete model class.
    """

    EVENT_TYPE_ENUM:  ClassVar[type[enum.Enum]]
    EVENT_ERROR_ENUM: ClassVar[type[enum.Enum]]

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(String(64), ForeignKey("users.client_id"), nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    state: Mapped[EventStateEnum] = mapped_column(
        SAEnum(EventStateEnum, name="event_record_state_enum", create_type=True),
        nullable=False,
        default=EventStateEnum.REQUESTED,
        index=True,
    )

    @declared_attr
    def type(cls) -> Mapped[enum.Enum]:
        return mapped_column(
            SAEnum(cls.EVENT_TYPE_ENUM, name=f"{cls.__tablename__}_type_enum", create_type=True),
            nullable=False,
        )

    @declared_attr
    def last_error(cls) -> Mapped[enum.Enum | None]:
        return mapped_column(
            SAEnum(cls.EVENT_ERROR_ENUM, name=f"{cls.__tablename__}_error_enum", create_type=True),
            nullable=True,
        )

    attempts:     Mapped[int]      = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int]      = mapped_column(Integer, nullable=False, default=3)
    description:  Mapped[str | None] = mapped_column(String(512), nullable=True)
```

`type` and `last_error` use `@declared_attr` so each concrete table gets its own Postgres `ENUM` type named after its table (`task_events_type_enum`, `order_events_type_enum`, etc.). `state` uses a single shared Postgres type `event_record_state_enum` across all event tables.

`created_by_id` uses `@declared_attr` because it contains a `ForeignKey` — same rule as `HistoryRecord.updated_by_id`.

---

## Applying `Event` to any entity

**Step 1 — Define the domain enums in `domain/<domain>/enums.py`:**

```python
class <Entity>EventTypeEnum(enum.Enum):
    OPERATION_A = "operation_a"
    OPERATION_B = "operation_b"
    ...

class <Entity>EventErrorEnum(enum.Enum):
    ERROR_A = "error_a"
    ERROR_B = "error_b"
    ...
```

**Step 2 — Create `<Entity>Event`:**

```python
class <Entity>Event(IdentityMixin, Event, db.Model):
    CLIENT_ID_PREFIX  = "<prefix>"
    EVENT_TYPE_ENUM   = <Entity>EventTypeEnum
    EVENT_ERROR_ENUM  = <Entity>EventErrorEnum
    __tablename__     = "<entity>_events"

    <entity>_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("<entity_table>.client_id"), nullable=False, index=True
    )

    <entity>: Mapped["<Entity>"] = relationship(
        "<Entity>", foreign_keys=[<entity>_id], back_populates="events"
    )
    created_by: Mapped["User"] = relationship(
        "User", foreign_keys="[<Entity>Event.created_by_id]"
    )
```

**Step 3 — Add the FK pointer + relationships to the parent entity:**

```python
class <Entity>(IdentityMixin, db.Model):
    ...
    last_event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("<entity>_events.client_id"), nullable=True
    )

    events: Mapped[list["<Entity>Event"]] = relationship(
        "<Entity>Event",
        foreign_keys="[<Entity>Event.<entity>_id]",
        back_populates="<entity>",
    )
    last_event: Mapped["<Entity>Event | None"] = relationship(
        "<Entity>Event",
        foreign_keys="[<Entity>.last_event_id]",
    )
```

---

## Concrete example — Task

```python
# domain/tasks/enums.py
import enum

class TaskEventTypeEnum(enum.Enum):
    SYNC_WITH_DELIVERY_APP  = "sync_with_delivery_app"
    SEND_CONFIRMATION_EMAIL = "send_confirmation_email"
    SEND_READINESS_EMAIL    = "send_readiness_email"
    SEND_SMS                = "send_sms"

class TaskEventErrorEnum(enum.Enum):
    EMAIL_NOT_REACHABLE  = "email_not_reachable"
    WRONG_EMAIL_FORMAT   = "wrong_email_format"
    PHONE_NOT_REACHABLE  = "phone_not_reachable"
    WRONG_PHONE_FORMAT   = "wrong_phone_format"
```

```python
# models/tables/tasks/task_event.py
from my_app.domain.tasks.enums import TaskEventTypeEnum, TaskEventErrorEnum

class TaskEvent(IdentityMixin, Event, db.Model):
    CLIENT_ID_PREFIX  = "te"
    EVENT_TYPE_ENUM   = TaskEventTypeEnum
    EVENT_ERROR_ENUM  = TaskEventErrorEnum
    __tablename__     = "task_events"

    task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("tasks.client_id"), nullable=False, index=True
    )

    task:       Mapped["Task"] = relationship("Task", foreign_keys=[task_id], back_populates="events")
    created_by: Mapped["User"] = relationship("User", foreign_keys="[TaskEvent.created_by_id]")
```

```python
# models/tables/tasks/task.py  (relevant additions only)
class Task(IdentityMixin, db.Model):
    ...
    last_event_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("task_events.client_id"), nullable=True
    )
    events:     Mapped[list["TaskEvent"]]   = relationship("TaskEvent", foreign_keys="[TaskEvent.task_id]", back_populates="task")
    last_event: Mapped["TaskEvent | None"]  = relationship("TaskEvent", foreign_keys="[Task.last_event_id]")
```

---

## Worker flow integration

Event records connect the domain intent to the async execution system (see [16_background_jobs.md](16_background_jobs.md)):

```
Command
  │
  ├─ 1. INSERT TaskEvent(state=REQUESTED, type=SEND_CONFIRMATION_EMAIL)
  │        flush() → assign event.client_id if generated by default
  │
  ├─ 2. UPDATE task.last_event_id = event.client_id
  │
  ├─ 3. create_instant_task(
  │        task_type=TaskType.NOTIFICATION,
  │        payload=asdict(NotificationPayload(task_client_id=task.client_id, ...)),
  │        event_client_id=event.client_id,   ← stored as column on ExecutionPayload
  │     )
  │
  └─ commit()

Worker (on claim)
  │
  ├─ 4. Read ExecutionPayload.event_client_id → resolve TaskEvent (column, not payload JSON)
  ├─ 5. Guard: if event.state in (COMPLETED, FAILED) → return early (idempotency)
  ├─ 6. UPDATE event.state = IN_PROGRESS, event.attempts += 1  →  commit()
  ├─ 7. Do the work (send email, call API, etc.)
  │
  ├─ Success → event.state = COMPLETED  →  commit()
  └─ Failure → event.state = FAILED, event.last_error = TaskEventErrorEnum.EMAIL_NOT_REACHABLE  →  commit()
```

`event_client_id` is a proper column on `ExecutionPayload` — not buried in the payload JSON. Workers always read it from the column so the event reference is visible at the infrastructure level and queryable. Workers import the domain enum to set `last_error` — they never hardcode strings.

---

## File structure

```
my_app/
├── models/
│   ├── base/
│   │   ├── identity.py        # IdentityMixin → 40
│   │   ├── history_record.py  # HistoryRecord → 41
│   │   └── event.py           # Event (this contract)
│   └── tables/
│       └── <domain>/
│           ├── <entity>.py              # has last_event_id FK
│           └── <entity>_event.py        # EVENT_TYPE_ENUM + EVENT_ERROR_ENUM set here
└── domain/
    ├── base/
    │   └── enums.py           # EventStateEnum (shared)
    └── <domain>/
        └── enums.py           # <Entity>EventTypeEnum + <Entity>EventErrorEnum
```

---

## Rules

- **`EVENT_TYPE_ENUM` and `EVENT_ERROR_ENUM` must be set on every concrete event model.** The mixin raises `AttributeError` at class creation time if they are missing — this is intentional.
- **Event rows are append-only from the domain perspective.** Only the worker updates `state`, `attempts`, and `last_error`. Commands only create rows with `state=REQUESTED`.
- **`last_event_id` is updated in the same transaction as the `INSERT`.** Use `flush()` to assign the event `client_id` before committing when the default generated it.
- **Workers must set `state=IN_PROGRESS` and increment `attempts` atomically when claiming the task.** Do not assume the event row reflects the worker's current state until the worker writes it.
- **Always guard on terminal states before processing.** Check `if event.state in (COMPLETED, FAILED): return` as the first action after resolving the event. This is the idempotency guard — a retry after a crash finds the event in `IN_PROGRESS` and proceeds safely; a duplicate delivery finds it `COMPLETED` and exits early.
- **`event_client_id` is read from `ExecutionPayload.event_client_id` — never from the payload JSON.** The column is the authoritative reference; the payload carries only domain data.
- **Max retry logic lives in the execution layer** (`ExecutionTask.max_try`), not in the event record. The event `max_attempts` is a domain-visible limit for display and alerting — the actual retry enforcement is in `worker_base.py`.
- **Never use `type` as a Python variable name** — it shadows the builtin. Use `event_type` in local variables when destructuring.
- **`state = FAILED` is terminal only from the event perspective.** The execution task may still retry — on each retry the worker resets `state = IN_PROGRESS` and increments `attempts`.
