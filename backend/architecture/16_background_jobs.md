# 16 — Async Execution System Contract

## What the async execution system is

The async execution system handles all work that should not block an HTTP response: notifications, image uploads, reports, reminders, and any other deferred or scheduled operation.

`execution_task` is the durable source of truth for every unit of work. All scheduled work, retries, and instant async actions ultimately become an `execution_task`. PostgreSQL stores the authoritative state; Redis acts only as a fast transport layer between the task router and workers.

---

## Architecture

```
Commands / Schedulers
        │
        │ create execution_task (state: OPEN)
        ▼
┌─────────────────────┐
│    Task Router      │  scans OPEN tasks → publishes task_client_id to Redis queue
└──────────┬──────────┘
           │  { "task_client_id": "task_01..." }
           ▼
┌─────────────────────┐
│    Redis Queues     │  transport only — not authoritative
│  queue:notifications│
│  queue:uploads      │
│  queue:reports      │
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│      Workers        │  atomic DB claim → execute → update state
└─────────────────────┘
```

**Core principles:**

- PostgreSQL is the durable workflow truth. Redis is transport only.
- Queues carry task IDs only — never payloads.
- Workers must be idempotent.
- Execution ownership is guaranteed by a DB-level atomic state transition — not by Redis delivery.
- Payloads are immutable execution snapshots stored in `execution_payload`.
- Schedulers define **when** and **what**. Workers define **how**.

---

## Enums

```python
# domain/execution/enums.py
import enum


class ExecutionTaskStateEnum(enum.Enum):
    OPEN = "open"
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    RETRYING = "retrying"
    RETRY_SCHEDULED = "retry_scheduled"
    COMPLETED = "completed"
    FAIL = "fail"
    CANCEL = "cancel"


class TaskType(enum.Enum):
    # Instant tasks — triggered directly by commands
    NOTIFICATION    = "notification"
    UPLOAD_IMAGE    = "upload_image"
    DELIVER_WEBHOOK = "deliver_webhook"

    # Notification pipeline tasks (phase 8)
    CREATE_NOTIFICATIONS   = "create_notifications"
    SEND_PUSH_NOTIFICATION = "send_push_notification"

    # Delayed scheduler tasks — created by DelayedScheduler when due
    DELAYED_NOTIFY_TO_CUSTOMER  = "delayed_notify_to_customer"
    DELAYED_SEND_REPORT         = "delayed_send_report"
    DELAYED_REMINDER            = "delayed_reminder"
    DELAYED_BATCH_NOTIFICATION  = "delayed_batch_notification"

    # Recurring scheduler tasks — created by RecurringScheduler on each interval
    RECURRING_SEND_REPORT = "recurring_send_report"
    RECURRING_REMINDER    = "recurring_reminder"
    RECURRING_PIN_TASK    = "recurring_pin_task"

    # Presence view-record tasks — enqueued by socket connect/disconnect handlers
    RECORD_VIEW_START = "record_view_start"
    RECORD_VIEW_END   = "record_view_end"


class EventTaskOriginSourceEnum(enum.Enum):
    DELAYED_SCHEDULER = "delayed_scheduler"
    RECURRING_SCHEDULER = "recurring_scheduler"
    INSTANT = "instant"
```

---

## Models

### ExecutionTask

```python
# models/tables/execution/execution_task.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin
from my_app.domain.execution.enums import ExecutionTaskStateEnum, TaskType


class ExecutionTask(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "task"
    __tablename__ = "execution_tasks"

    task_type: Mapped[TaskType] = mapped_column(
        SAEnum(TaskType, name="task_type_enum", create_type=True),
        nullable=False,
        index=True,
    )
    state: Mapped[ExecutionTaskStateEnum] = mapped_column(
        SAEnum(ExecutionTaskStateEnum, name="execution_task_state_enum", create_type=True),
        nullable=False,
        default=ExecutionTaskStateEnum.OPEN,
        index=True,
    )

    try_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_try: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    payload: Mapped["ExecutionPayload"] = relationship(
        "ExecutionPayload", back_populates="execution_task", uselist=False
    )
```

### ExecutionPayload

```python
# models/tables/execution/execution_payload.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, ForeignKey, JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin
from my_app.domain.execution.enums import EventTaskOriginSourceEnum


class ExecutionPayload(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "epl"
    __tablename__ = "execution_payloads"

    origin_source: Mapped[EventTaskOriginSourceEnum] = mapped_column(
        SAEnum(EventTaskOriginSourceEnum, name="event_task_origin_source_enum", create_type=True),
        nullable=False,
    )
    origin_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    execution_task_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("execution_tasks.client_id"), nullable=False, unique=True
    )
    execution_task: Mapped["ExecutionTask"] = relationship(
        "ExecutionTask", back_populates="payload"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

**Payload rules:**
- `payload` is an immutable snapshot captured at the time the task is created. Workers must not modify it.
- `origin_id` is the `client_id` of the scheduler row that created this task (`DelayedScheduler.client_id` or `RecurringScheduler.client_id`). It is `None` for instant tasks.
- `event_client_id` is the `client_id` of the domain `Event` row this task is serving. `NULL` for tasks with no associated event (recurring reports, batch jobs, etc.). Workers read this column to locate and update the event — never from the payload JSON.
- Workers read from `payload` only for domain data. They never re-query the source record to re-derive the snapshot.

---

## State machine

```
OPEN ──► PENDING ──► IN_PROGRESS ──► COMPLETED
                                  │
                                  ├──► RETRY_SCHEDULED ──► OPEN (re-queued)
                                  │
                                  └──► FAIL
```

| Transition | Who | When |
|---|---|---|
| Created → `OPEN` | Command / Scheduler | Task row is inserted |
| `OPEN` → `PENDING` | Task Router | After publishing `task_client_id` to Redis queue |
| `PENDING` → `IN_PROGRESS` | Worker | Atomic `UPDATE ... WHERE state='pending'` claim succeeds |
| `IN_PROGRESS` → `COMPLETED` | Worker | Domain logic succeeded; `completed_at` is set |
| `IN_PROGRESS` → `RETRY_SCHEDULED` | Worker | Domain logic failed; `try_count < max_try`; `next_retry_at` is set |
| `RETRY_SCHEDULED` → `OPEN` | Retry scanner | `next_retry_at <= now`; task becomes re-routable |
| `IN_PROGRESS` → `FAIL` | Worker | Domain logic failed; `try_count >= max_try` |
| Any → `CANCEL` | Command | Task is explicitly cancelled before execution |

`RETRYING` is a transient label used internally in worker logs to distinguish a re-attempt from a first attempt. It is not persisted as a state — the state during re-attempt remains `IN_PROGRESS`.

---

## Task Router

The task router is a lightweight background process that continuously scans for `OPEN` tasks and routes them into the correct Redis queue.

```python
# services/infra/execution/task_router.py
import logging
import time
from datetime import datetime, timezone

from my_app import create_app
from my_app.models import db
from my_app.models.tables.execution.execution_task import ExecutionTask
from my_app.domain.execution.enums import ExecutionTaskStateEnum, TaskType
from my_app.services.infra.redis import get_redis_client

logger = logging.getLogger(__name__)

QUEUE_MAP: dict[TaskType, str] = {
    TaskType.NOTIFICATION:               "queue:notifications",
    TaskType.UPLOAD_IMAGE:               "queue:uploads",
    TaskType.DELIVER_WEBHOOK:            "queue:webhooks",
    TaskType.CREATE_NOTIFICATIONS:       "queue:notifications",
    TaskType.SEND_PUSH_NOTIFICATION:     "queue:notifications",
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: "queue:notifications",
    TaskType.DELAYED_SEND_REPORT:        "queue:reports",
    TaskType.DELAYED_REMINDER:           "queue:notifications",
    TaskType.DELAYED_BATCH_NOTIFICATION: "queue:notifications",
    TaskType.RECURRING_SEND_REPORT:      "queue:reports",
    TaskType.RECURRING_REMINDER:         "queue:notifications",
    TaskType.RECURRING_PIN_TASK:         "queue:tasks",
    TaskType.RECORD_VIEW_START:          "queue:presence",
    TaskType.RECORD_VIEW_END:            "queue:presence",
}

POLL_INTERVAL_SECONDS = 0.5   # fallback only — primary wake is pg_notify (see below)
BATCH_SIZE = 50


def run_task_router() -> None:
    app = create_app("production")
    redis = get_redis_client(app.config["REDIS_URI"])

    logger.info("Task router started.")

    with app.app_context():
        while True:
            _route_open_tasks(redis)
            _requeue_retry_scheduled_tasks()
            time.sleep(POLL_INTERVAL_SECONDS)


def _route_open_tasks(redis) -> None:
    tasks = (
        db.session.query(ExecutionTask)
        .filter(ExecutionTask.state == ExecutionTaskStateEnum.OPEN)
        .limit(BATCH_SIZE)
        .all()
    )

    for task in tasks:
        queue_name = QUEUE_MAP.get(task.task_type)
        if not queue_name:
            logger.error("No queue mapped for task_type=%s task_id=%s", task.task_type, task.client_id)
            continue

        redis.rpush(queue_name, task.client_id)
        task.state = ExecutionTaskStateEnum.PENDING

    if tasks:
        db.session.commit()
        logger.info("task_router | routed=%d", len(tasks))


def _requeue_retry_scheduled_tasks() -> None:
    now = datetime.now(timezone.utc)
    tasks = (
        db.session.query(ExecutionTask)
        .filter(
            ExecutionTask.state == ExecutionTaskStateEnum.RETRY_SCHEDULED,
            ExecutionTask.next_retry_at <= now,
        )
        .limit(BATCH_SIZE)
        .all()
    )

    for task in tasks:
        task.state = ExecutionTaskStateEnum.OPEN
        task.next_retry_at = None

    if tasks:
        db.session.commit()
        logger.info("task_router | requeued_retries=%d", len(tasks))
```

**Router rules:**
- The router publishes only `{"task_client_id": <str>}` — never the payload. Workers fetch from the DB.
- After publishing, the router sets `state = PENDING`. If the publish fails, `state` stays `OPEN` and the task is retried on the next scan.
- The router also drives the retry cycle: tasks with `state=RETRY_SCHEDULED` and `next_retry_at <= now` are reset to `OPEN`.

---

## Workers

Workers subscribe to one or more queues. They do not trust Redis delivery as execution ownership — ownership is established via an atomic DB claim.

### Worker flow

```
1. CONSUME task_client_id from Redis queue (blocking pop)
2. CLAIM: UPDATE execution_task
          SET state='in_progress', worker_id=<id>, locked_at=now, started_at=now
          WHERE client_id=<task_client_id> AND state='pending'
3. if 0 rows updated → another worker already claimed it → discard and continue
4. FETCH execution_payload for the task
5. EXECUTE domain logic
6. On success → UPDATE state='completed', completed_at=now
7. On failure:
   - try_count += 1
   - if try_count < max_try:
       state = 'retry_scheduled'
       next_retry_at = now + backoff(try_count)
       last_error = str(exception)
   - else:
       state = 'fail'
       last_error = str(exception)
```

### Base worker pattern

```python
# services/infra/execution/worker_base.py
import logging
import socket
import time
from datetime import datetime, timezone, timedelta
from typing import Callable

from my_app import create_app
from my_app.models import db
from my_app.models.tables.execution.execution_task import ExecutionTask
from my_app.domain.execution.enums import ExecutionTaskStateEnum, TaskType
from my_app.services.infra.redis import get_redis_client

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = [30, 120, 300]  # base retry intervals by attempt index
BACKOFF_JITTER  = 0.15           # ±15% random jitter — prevents thundering-herd retries


def run_worker(queue_name: str, handler_map: dict[TaskType, Callable[[dict, str], None]]) -> None:
    app = create_app("production")
    redis = get_redis_client(app.config["REDIS_URI"])
    worker_id = f"{socket.gethostname()}:{queue_name}:{int(time.time())}"

    logger.info("Worker started | queue=%s worker_id=%s", queue_name, worker_id)

    with app.app_context():
        while True:
            raw = redis.blpop(queue_name, timeout=5)
            if not raw:
                continue

            task_client_id = raw[1].decode() if isinstance(raw[1], bytes) else raw[1]
            _process_task(task_client_id, worker_id, handler_map)


def _process_task(task_client_id: str, worker_id: str, handler_map: dict[TaskType, Callable[[dict, str], None]]) -> None:
    now = datetime.now(timezone.utc)

    # Atomic claim
    rows_updated = (
        db.session.query(ExecutionTask)
        .filter(ExecutionTask.client_id == task_client_id, ExecutionTask.state == ExecutionTaskStateEnum.PENDING)
        .update(
            {
                "state": ExecutionTaskStateEnum.IN_PROGRESS,
                "worker_id": worker_id,
                "locked_at": now,
                "started_at": now,
            },
            synchronize_session=False,
        )
    )
    db.session.commit()

    if rows_updated == 0:
        logger.info("task_id=%s already claimed — skipping", task_client_id)
        return

    task = db.session.get(ExecutionTask, task_client_id)
    handler = handler_map.get(task.task_type)

    if not handler:
        logger.error("No handler for task_type=%s task_id=%s", task.task_type, task_client_id)
        _mark_failed(task, "No handler registered for task_type.")
        return

    try:
        handler(task.payload.payload, task.client_id)
        task.state = ExecutionTaskStateEnum.COMPLETED
        task.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info("task_id=%s completed | type=%s", task_client_id, task.task_type)

    except Exception as exc:
        _schedule_retry_or_fail(task, exc)


def _schedule_retry_or_fail(task: ExecutionTask, exc: Exception) -> None:
    import random
    task.try_count += 1
    task.last_error = str(exc)[:1024]

    if task.try_count < task.max_try:
        base  = BACKOFF_SECONDS[min(task.try_count - 1, len(BACKOFF_SECONDS) - 1)]
        jitter = base * BACKOFF_JITTER
        delay = base + random.uniform(-jitter, jitter)
        task.state = ExecutionTaskStateEnum.RETRY_SCHEDULED
        task.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=max(delay, 1))
        logger.warning(
            "task_id=%s retry_scheduled | attempt=%d next_retry_at=%s",
            task.client_id, task.try_count, task.next_retry_at,
        )
    else:
        task.state = ExecutionTaskStateEnum.FAIL
        logger.error(
            "task_id=%s permanently failed | attempt=%d error=%s",
            task.client_id, task.try_count, task.last_error,
        )

    db.session.commit()


def _mark_failed(task: ExecutionTask, reason: str) -> None:
    task.state = ExecutionTaskStateEnum.FAIL
    task.last_error = reason
    db.session.commit()
```

### Worker entry point

One entry point per queue. Each entry point defines which task types it handles and their handlers:

```python
# workers/notification_worker.py
from my_app.services.infra.execution.worker_base import run_worker
from my_app.domain.execution.enums import TaskType
from my_app.services.infra.jobs.handlers.notification import handle_notification
from my_app.services.infra.jobs.handlers.reminder import handle_reminder
from my_app.services.tasks.notifications.create_notifications import handle_create_notifications
from my_app.services.tasks.notifications.send_push_notification import handle_send_push_notification

HANDLER_MAP = {
    TaskType.NOTIFICATION:               handle_notification,
    TaskType.CREATE_NOTIFICATIONS:       handle_create_notifications,
    TaskType.SEND_PUSH_NOTIFICATION:     handle_send_push_notification,
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: handle_notification,
    TaskType.DELAYED_REMINDER:           handle_reminder,
    TaskType.DELAYED_BATCH_NOTIFICATION: handle_notification,
    TaskType.RECURRING_REMINDER:         handle_reminder,
}

if __name__ == "__main__":
    run_worker("queue:notifications", HANDLER_MAP)

# workers/presence_worker.py
from my_app.services.infra.execution.worker_base import run_worker
from my_app.domain.execution.enums import TaskType
from my_app.services.tasks.presence.record_view_start import handle_record_view_start
from my_app.services.tasks.presence.record_view_end import handle_record_view_end

HANDLER_MAP = {
    TaskType.RECORD_VIEW_START: handle_record_view_start,
    TaskType.RECORD_VIEW_END:   handle_record_view_end,
}

if __name__ == "__main__":
    run_worker("queue:presence", HANDLER_MAP)
```

---

## Payload dataclasses

Each task type has its own typed payload dataclass. It is the single source of truth for the payload shape — used at both ends of the async boundary:

- **At creation (command):** acts as a schema. The command must supply every required field. `asdict()` serialises it to the JSON stored in `execution_payloads.payload`.
- **At execution (handler):** `MyPayload(**raw)` deserialises and validates the stored JSON. If a field is missing or misnamed, it raises before any side effect runs. The handler can trust the result completely.

Because both ends use the same class, the command and handler can never silently drift apart — a mismatch is caught at the call site, not at runtime in a background process.

Payload dataclasses live in `domain/execution/payloads/` — one file per task type. They are domain concepts, not infrastructure.

```python
# domain/execution/payloads/notification.py
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationPayload:
    recipient_id: str
    workspace_id: str
    channel: str     # "email" | "sms" | "push"
    message: str
```

**Why `frozen=True`:** the payload is an immutable snapshot. Freezing the dataclass enforces that handlers cannot mutate it, matching the contract that payload is read-only.

**Payload rules:**
- One dataclass per task type. No shared base class — payload shapes are task-specific.
- All fields must be serialisable to JSON (strings, ints, lists, dicts). The payload is stored as a JSON column.
- Always use `asdict()` to build the dict passed to `create_instant_task()` — never construct the dict by hand.
- Always deserialise with `MyPayload(**raw)` as the first line of the handler — never access `raw["key"]` directly.
- Never add ORM instances or non-serialisable objects to a payload. Store IDs; let the handler query if it needs the live record.
- For schedulers, the same rule applies — `payload_snapshot` stored in the scheduler row must also be built with `asdict()` from the typed dataclass.

---

## Handler contract

A handler receives the raw payload dict, deserialises it into the typed dataclass, and performs one side effect. It must be idempotent.

```python
# services/infra/jobs/handlers/notification.py
import logging
from my_app.domain.execution.payloads.notification import NotificationPayload

logger = logging.getLogger(__name__)


def handle_notification(raw: dict, task_id: str) -> None:
    payload = NotificationPayload(**raw)
    # payload is now typed — IDE-complete, validated at entry
    # task_id is available if this handler needs to create a scheduler with origin tracing
    # ... send notification via payload.channel, payload.recipient_id, etc.
    logger.info("Notification sent | recipient_id=%s channel=%s", payload.recipient_id, payload.channel)
```

Deserialising at handler entry means a missing or misnamed key raises `TypeError` immediately, before any side effect is attempted. The worker catches it, records the error, and schedules a retry.

**Handler rules:**
- Handlers receive `(raw: dict, task_id: str)` — no ORM instances, no `ServiceContext`.
- First line of every handler: deserialise into the typed payload dataclass.
- `task_id` is the current `ExecutionTask.client_id`. Pass it as `origin_id` to `create_delayed_scheduler()` or `create_recurring_scheduler()` when the handler needs to schedule follow-up work.
- Handlers must be idempotent. A handler called twice with the same payload must produce the same outcome.
- Handlers must not modify the payload.
- Handlers raise on unrecoverable failure — the worker's retry logic handles it.
- One handler = one side effect. Do not combine multiple integrations in one handler.

**DB access in handlers:**

Handlers run outside of a request context, so they cannot use the FastAPI `get_db` dependency. Use `task_db_session()` from `services/infra/execution/db.py` instead — it is an `asynccontextmanager` backed by the same shared session factory:

```python
# services/infra/execution/db.py
from contextlib import asynccontextmanager
from typing import AsyncIterator
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.database import _session_factory

@asynccontextmanager
async def task_db_session() -> AsyncIterator[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("DB not initialised. Call init_db() before running workers.")
    async with _session_factory() as session:
        yield session
```

Usage in a handler:
```python
from my_app.services.infra.execution.db import task_db_session

async def handle_record_view_start(raw: dict, task_id: str) -> None:
    async with task_db_session() as session:
        # ... query and commit
```

---

## Task factory

A single function is the only place in the codebase that creates an `ExecutionTask` + `ExecutionPayload` pair. Commands, scheduler runners, and event flows all go through it — nothing creates these rows directly.

```python
# services/infra/execution/task_factory.py
from datetime import datetime, timezone

from my_app.models import db
from my_app.models.tables.execution.execution_task import ExecutionTask
from my_app.models.tables.execution.execution_payload import ExecutionPayload
from my_app.domain.execution.enums import ExecutionTaskStateEnum, TaskType, EventTaskOriginSourceEnum


def create_execution_task(
    task_type: TaskType,
    payload: dict,
    origin_source: EventTaskOriginSourceEnum,
    origin_id: str | None = None,
    scheduled_at: datetime | None = None,
    event_client_id: str | None = None,
    max_try: int = 3,
) -> ExecutionTask:
    now = datetime.now(timezone.utc)
    task = ExecutionTask(
        task_type=task_type,
        state=ExecutionTaskStateEnum.OPEN,
        max_try=max_try,
        created_at=now,
        scheduled_at=scheduled_at,
    )
    db.session.add(task)
    db.session.flush()

    db.session.add(ExecutionPayload(
        origin_source=origin_source,
        origin_id=origin_id,
        event_client_id=event_client_id,
        payload=payload,
        execution_task_id=task.client_id,
        created_at=now,
    ))
    return task


def create_instant_task(
    task_type: TaskType,
    payload: dict,
    event_client_id: str | None = None,
    max_try: int = 3,
) -> ExecutionTask:
    return create_execution_task(
        task_type=task_type,
        payload=payload,
        origin_source=EventTaskOriginSourceEnum.INSTANT,
        event_client_id=event_client_id,
        max_try=max_try,
    )
```

`create_instant_task()` is a thin convenience wrapper for commands. Scheduler runners call `create_execution_task()` directly, passing their `origin_source` and scheduler `client_id` as `origin_id`.

---

## Instant tasks (triggered by commands)

Commands call `create_instant_task()` — always inside the same `db.session.begin()` block as the domain write. Build the payload with `dataclasses.asdict()` from the typed payload dataclass:

```python
# services/commands/record/create_record.py
from dataclasses import asdict
from my_app.services.infra.execution.task_factory import create_instant_task
from my_app.domain.execution.enums import TaskType
from my_app.domain.execution.payloads.notification import NotificationPayload


def create_record(ctx: ServiceContext) -> dict:
    with db.session.begin():
        record = Record(workspace_id=ctx.workspace_id, ...)
        db.session.add(record)
        db.session.flush()

        create_instant_task(
            task_type=TaskType.NOTIFICATION,
            payload=asdict(NotificationPayload(
                recipient_id=ctx.user_id,
                workspace_id=ctx.workspace_id,
                channel="email",
                message=f"Record {record.name} created.",
            )),
        )
    # task is committed with the record — atomic
    return {"client_id": record.client_id}
```

The task is committed in the same transaction as the domain write. If the write fails, no task is created. If the task row fails to insert, the write rolls back. Both succeed together or neither does.

---

## Idempotency

All handlers must guard against duplicate execution. When the task is backed by a domain `Event` row, the event state is the idempotency guard — no separate flag needed:

```python
def handle_notification(raw: dict, task_id: str) -> None:
    payload = NotificationPayload(**raw)

    execution_payload = (
        db.session.query(ExecutionPayload)
        .filter_by(execution_task_id=task_id)
        .one()
    )
    event = db.session.query(TaskEvent).filter_by(
        client_id=execution_payload.event_client_id
    ).one()

    # Guard: terminal states mean the work is already done
    if event.state in (EventStateEnum.COMPLETED, EventStateEnum.FAILED):
        return

    # Safe to proceed whether state is REQUESTED (first attempt) or IN_PROGRESS (retry after crash)
    event.state = EventStateEnum.IN_PROGRESS
    event.attempts += 1
    db.session.commit()

    # ... do the work ...

    event.state = EventStateEnum.COMPLETED
    db.session.commit()
```

On a retry after a crash, the event may already be `IN_PROGRESS`. The handler proceeds normally — `IN_PROGRESS` is not terminal, so retries are safe. Only `COMPLETED` and `FAILED` are guards that cause an early return.

For tasks with no event (recurring reports, batch jobs where `event_client_id` is `NULL`), use a DB flag on the affected entity or a Redis idempotency key with a TTL covering the maximum retry window.

---

## Retry backoff with jitter

`BACKOFF_SECONDS` defines the base delay per attempt. A ±15% random jitter is applied so that batch failures (e.g., 50 tasks failing simultaneously) do not retry at the exact same moment — staggering the retry storm:

| Attempt | Base delay | With ±15% jitter |
|---|---|---|
| 1st retry | 30s | 25.5s – 34.5s |
| 2nd retry | 120s | 102s – 138s |
| 3rd retry | 300s | 255s – 345s |

**Rule:** Never use a fixed retry delay. Every worker using `_schedule_retry_or_fail` inherits jitter automatically from `BACKOFF_JITTER`.

---

## Task timeout enforcement

Wrap handler execution in `asyncio.wait_for()` to prevent a hanging task from blocking the worker indefinitely. Default timeout is 5 minutes; override per handler via the timeout map:

```python
# services/infra/execution/worker_base.py
import asyncio

HANDLER_TIMEOUT_SECONDS: dict[str, int] = {
    "default":        300,   # 5 minutes
    "upload_image":   3600,  # 1 hour — large file operations
    "send_report":    600,   # 10 minutes — report generation
}


async def _execute_with_timeout(handler, raw_payload: dict, task_client_id: str, task_type_value: str) -> None:
    timeout = HANDLER_TIMEOUT_SECONDS.get(task_type_value, HANDLER_TIMEOUT_SECONDS["default"])
    try:
        await asyncio.wait_for(handler(raw_payload, task_client_id), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Handler timed out after {timeout}s")
```

The worker calls `await _execute_with_timeout(handler, ...)` instead of `await handler(raw, task_id)` directly. On timeout, the worker raises, which triggers `_schedule_retry_or_fail` — the task enters `RETRY_SCHEDULED` if retries remain, or `FAIL` if exhausted.

**Rule:** Long-running handlers (file processing, report generation) must declare their timeout in `HANDLER_TIMEOUT_SECONDS`. Never use the default timeout for a handler that is expected to run longer than 5 minutes.

---

## Worker observability

Every task execution must emit structured log lines that carry enough context to diagnose failures without reading the DB. Add elapsed time tracking to `_process_task`:

```python
# services/infra/execution/worker_base.py
import time

def _process_task(task_client_id: str, worker_id: str, handler_map: ...) -> None:
    start = time.monotonic()
    # ... atomic claim (unchanged) ...

    try:
        await _execute_with_timeout(handler, task.payload.payload, task.client_id, task.task_type.value)
        elapsed_ms = (time.monotonic() - start) * 1000
        task.state      = ExecutionTaskStateEnum.COMPLETED
        task.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info(
            "task_completed | task_id=%s task_type=%s worker=%s elapsed_ms=%.1f",
            task_client_id, task.task_type.value, worker_id, elapsed_ms,
        )
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.error(
            "task_failed | task_id=%s task_type=%s worker=%s elapsed_ms=%.1f error=%s",
            task_client_id, task.task_type.value, worker_id, elapsed_ms, str(exc)[:200],
        )
        _schedule_retry_or_fail(task, exc)
```

The task router logs queue depth each cycle so ops teams can detect backlog without querying the DB:

```python
def _route_open_tasks(redis) -> None:
    tasks = ...
    if tasks:
        depths = {name: redis.llen(name) for name in set(QUEUE_MAP.values())}
        logger.info("task_router | routed=%d queue_depths=%s", len(tasks), depths)
```

**Rules:**
- Every `task_completed` and `task_failed` log must include `task_id`, `task_type`, `worker_id`, and `elapsed_ms`. These four fields are the minimum required for incident correlation.
- Log at `INFO` for completions, `WARNING` for retries, `ERROR` for permanent failures and stale recoveries.

---

## Stale task recovery

A worker process that crashes mid-execution leaves tasks stuck in `IN_PROGRESS` with no worker to complete or fail them. The task router scans for these and resets them to `OPEN` on each poll cycle:

```python
# services/infra/execution/task_router.py
STALE_IN_PROGRESS_MINUTES = 30   # configurable via settings.task_stale_threshold_minutes


def _cleanup_stale_tasks() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_IN_PROGRESS_MINUTES)
    tasks = (
        db.session.query(ExecutionTask)
        .filter(
            ExecutionTask.state    == ExecutionTaskStateEnum.IN_PROGRESS,
            ExecutionTask.locked_at < cutoff,
        )
        .limit(BATCH_SIZE)
        .all()
    )
    for task in tasks:
        task.state      = ExecutionTaskStateEnum.OPEN
        task.worker_id  = None
        task.locked_at  = None
        logger.warning(
            "stale_task_recovered | task_id=%s task_type=%s locked_at=%s",
            task.client_id, task.task_type.value, task.locked_at,
        )
    if tasks:
        db.session.commit()
```

Add `_cleanup_stale_tasks()` to the router loop:

```python
while True:
    _route_open_tasks(redis)
    _requeue_retry_scheduled_tasks()
    _cleanup_stale_tasks()
    time.sleep(POLL_INTERVAL_SECONDS)
```

**Rules:**
- `STALE_IN_PROGRESS_MINUTES` must exceed the longest declared handler timeout. **Invariant: `STALE_IN_PROGRESS_MINUTES > max(HANDLER_TIMEOUT_SECONDS.values()) / 60`.** With `upload_image = 3600s = 60 min`, the default must be at least 90 minutes — not 30. A stale threshold shorter than a legitimate handler runtime causes a live task to be recovered and re-claimed by a second worker while the first is still running.
- Recovered tasks re-enter as `OPEN` — they are retried by the next available worker. The `try_count` is not reset, so they consume a retry slot. If the task reaches `max_try`, it fails permanently as normal.
- Stale recovery is a safety net for crashed workers, not a substitute for graceful shutdown. Workers must catch `SIGTERM` and mark in-flight tasks as `RETRY_SCHEDULED` before exiting.

---

## Stuck `PENDING` task recovery

A task enters `PENDING` when the router commits the state update and pushes the `task_client_id` to Redis. If the Redis push succeeds but the subsequent DB commit fails (rare but possible on transient DB error), the task re-enters `OPEN` on the next router cycle — safe. If the commit succeeds but the Redis entry is silently lost (eviction, Redis restart, `allkeys-lru` under memory pressure), the task is `PENDING` in the DB with no corresponding queue entry. Stale recovery ignores `PENDING` state. The task is permanently stuck.

Add a `_recover_stuck_pending_tasks()` pass to the router loop. Any task that has been `PENDING` for longer than a short window (5 minutes) was never picked up by a worker and should be reset to `OPEN`:

```python
# services/infra/execution/task_router.py
STUCK_PENDING_MINUTES = 5


async def _recover_stuck_pending_tasks() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STUCK_PENDING_MINUTES)
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(
                ExecutionTask.state == ExecutionTaskStateEnum.PENDING,
                ExecutionTask.created_at < cutoff,
            ).limit(BATCH_SIZE)
        )
        tasks = result.scalars().all()
        for task in tasks:
            task.state = ExecutionTaskStateEnum.OPEN
            logger.warning(
                "stuck_pending_recovered | task_id=%s task_type=%s",
                task.client_id, task.task_type.value,
            )
        if tasks:
            await session.commit()
```

Add to the router loop alongside stale recovery:

```python
await _route_open_tasks(redis)
await _requeue_retry_scheduled_tasks()
await _cleanup_stale_tasks()
await _recover_stuck_pending_tasks()
```

**Rules:**
- `STUCK_PENDING_MINUTES = 5` is appropriate. A task picked up by a worker transitions to `IN_PROGRESS` within seconds. Any task still `PENDING` after 5 minutes has a lost queue entry.
- Do not set this lower than 60 seconds — a brief Redis connection hiccup should not cause premature recovery before the worker has a chance to claim the task.

---

## Worker session isolation for long-running handlers

The default worker pattern holds a single DB session open from claim through handler completion. For short handlers (< 1s) this is harmless. For `upload_image` handlers (up to 3600s), one connection pool slot is held per active upload. With `pool_size=20`, 20 concurrent uploads exhaust the pool and block all HTTP requests from obtaining DB connections.

Split worker execution into three discrete sessions:

```python
# services/infra/execution/worker_base.py

async def _process_task(task_client_id, worker_id, handler_map) -> None:
    # 1. Claim session — short, closes immediately after claim
    task_type_value, raw_payload = await _claim_task(task_client_id, worker_id)
    if task_type_value is None:
        return

    handler = handler_map.get(task_type_value)
    if not handler:
        await _mark_no_handler(task_client_id)
        return

    # 2. Handler runs — opens its own session via task_db_session()
    # Connection pool slot is free during handler execution
    start = time.monotonic()
    try:
        await _execute_with_timeout(handler, raw_payload, task_client_id, task_type_value.value)
        elapsed_ms = (time.monotonic() - start) * 1000
        # 3. Finalize session — short, closes immediately after state update
        await _finalize_task(task_client_id, worker_id, elapsed_ms)
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        await _fail_task(task_client_id, worker_id, exc, elapsed_ms)
```

**Rules:**
- The claim session must close before the handler starts. Never hold a connection open during handler execution.
- Handlers access the DB via `task_db_session()` — not via the claim session. The claim session's connection is returned to the pool before the handler runs.
- The finalize session is a fresh connection, opened only to write the terminal state (`COMPLETED` or `RETRY_SCHEDULED`/`FAIL`).

---

## Worker graceful shutdown (SIGTERM)

Workers killed mid-handler leave tasks in `IN_PROGRESS`. Without a SIGTERM handler, the only recovery is stale recovery (90 minutes after fix). Graceful shutdown marks the in-flight task as `RETRY_SCHEDULED` immediately, so the task is picked up by another worker within seconds.

```python
# services/infra/execution/worker_base.py
import signal
import asyncio

_shutdown_event: asyncio.Event = asyncio.Event()


def _register_shutdown_handler() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown_event.set)


async def run_worker(queue_name, handler_map) -> None:
    _register_shutdown_handler()
    redis = get_redis_client(settings.redis_url)
    worker_id = f"{socket.gethostname()}:{queue_name}:{int(time.time())}"
    logger.info("worker_start | queue=%s worker_id=%s", queue_name, worker_id)

    current_task_id: str | None = None
    try:
        while not _shutdown_event.is_set():
            raw = redis.blpop(queue_name, timeout=2)
            if not raw:
                continue
            current_task_id = raw[1] if isinstance(raw[1], str) else raw[1].decode()
            await _process_task(current_task_id, worker_id, handler_map)
            current_task_id = None
    finally:
        if current_task_id:
            await _rescue_in_flight_task(current_task_id)
        logger.info("worker_shutdown | queue=%s worker_id=%s", queue_name, worker_id)


async def _rescue_in_flight_task(task_client_id: str) -> None:
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_client_id)
        )
        task = result.scalar_one_or_none()
        if task and task.state == ExecutionTaskStateEnum.IN_PROGRESS:
            task.state = ExecutionTaskStateEnum.RETRY_SCHEDULED
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            logger.warning("worker_sigterm_rescue | task_id=%s", task_client_id)
            await session.commit()
```

**Rules:**
- `blpop` timeout must be short (2s) so the shutdown event is checked frequently. A 5s timeout means up to 5s delay after SIGTERM before the loop notices the shutdown signal.
- `_rescue_in_flight_task` must be called in a `finally` block — it must run even if the handler raised an unhandled exception.
- The rescued task enters `RETRY_SCHEDULED` with a 30s delay, not `OPEN`. This prevents the task from being immediately re-claimed by another worker before the dying process has fully exited.

---

## Task router — PostgreSQL NOTIFY/LISTEN hybrid

The task router uses a hybrid wake model: a Postgres trigger fires `pg_notify` the instant a task enters `OPEN` state; a dedicated `asyncpg` connection listens and unblocks the router immediately. A slow fallback poll (`POLL_INTERVAL_SECONDS = 0.5`) runs concurrently as a safety net for notifications lost during reconnects.

This eliminates idle DB hammering (no polling when no tasks exist) while keeping sub-10ms pickup latency under load.

### Postgres trigger (migration)

```sql
CREATE OR REPLACE FUNCTION notify_task_open()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('task_open', NEW.client_id::text);
  RETURN NEW;
END;
$$;

CREATE TRIGGER trg_task_open
AFTER INSERT OR UPDATE OF state ON execution_tasks
FOR EACH ROW WHEN (NEW.state = 'open')
EXECUTE FUNCTION notify_task_open();
```

Add this trigger in its own migration, after the `execution_tasks` table migration. The trigger fires on both `INSERT` (instant tasks) and `UPDATE OF state` (retry re-queues).

### LISTEN connection

A dedicated `asyncpg` connection — separate from the SQLAlchemy pool — holds the `LISTEN` channel. It reconnects automatically on drop so a transient disconnect never silently disables the wake mechanism:

```python
# services/infra/execution/task_router.py
import asyncpg

_notify_event: asyncio.Event = asyncio.Event()


async def _listen_for_task_events() -> None:
    while True:
        try:
            dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(dsn)

            async def _on_notify(conn, pid, channel, payload):
                _notify_event.set()

            await conn.add_listener("task_open", _on_notify)
            logger.info("task_router | LISTEN connection established")

            while not conn.is_closed():
                await asyncio.sleep(10)
                await conn.execute("SELECT 1")  # keepalive
        except Exception:
            logger.exception("task_router | LISTEN connection lost — reconnecting in 5s")
            await asyncio.sleep(5)
```

### Router loop

The router waits on `_notify_event` with a timeout. Either the event fires (NOTIFY received) or the timeout expires (fallback poll). Both paths drain OPEN tasks identically:

```python
FALLBACK_POLL_SECONDS = 30   # safety net for missed notifications only


async def run_task_router() -> None:
    logger.info("Task router started.")
    redis = get_redis_client(settings.redis_url)
    asyncio.create_task(_listen_for_task_events())
    while True:
        try:
            await asyncio.wait_for(_notify_event.wait(), timeout=FALLBACK_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
        _notify_event.clear()
        try:
            await _route_open_tasks(redis)
            await _requeue_retry_scheduled_tasks()
            await _cleanup_stale_tasks()
        except Exception:
            logger.exception("task_router: poll error")
```

**Rules:**
- The LISTEN connection is always alive — including during sleep mode (see [22_performance.md](22_performance.md)). A `pg_notify` from an external write wakes the router even when the app is otherwise idle.
- `FALLBACK_POLL_SECONDS = 30` is a correctness guard, not a throughput mechanism. Do not lower it to increase throughput — use the NOTIFY path instead.
- The DSN conversion (`postgresql+asyncpg://` → `postgresql://`) is required because `asyncpg.connect()` does not accept the SQLAlchemy driver prefix.
- Never share the LISTEN connection with the SQLAlchemy session pool. It must be a dedicated connection so its lifecycle is independent of request-scoped sessions.

---

## File structure

```
my_app/
├── domain/
│   └── execution/
│       ├── enums.py                   # ExecutionTaskStateEnum, TaskType, EventTaskOriginSourceEnum
│       └── payloads/
│           ├── notification.py        # NotificationPayload — add one file per task type
│           ├── reminder.py            # ReminderPayload
│           └── upload.py              # UploadPayload
└── services/
    └── infra/
        ├── execution/
        │   ├── task_factory.py        # create_execution_task() + create_instant_task() — never changes
        │   ├── task_router.py         # QUEUE_MAP + router loop — never changes
        │   └── worker_base.py         # run_worker(), atomic claim, retry logic — never changes
        └── jobs/
            └── handlers/
                ├── notification.py    # handle_notification() — add one file per task type
                ├── reminder.py        # handle_reminder()
                └── upload.py          # handle_upload()

workers/                               # process entry points — run as separate processes
    ├── notification_worker.py         # HANDLER_MAP + run_worker("queue:notifications", ...)
    ├── upload_worker.py
    └── report_worker.py

models/
└── tables/
    └── execution/
        ├── execution_task.py
        └── execution_payload.py
```

---

## Extending the system

Adding a new task type requires changes in exactly five places. Nothing else in the infrastructure changes.

**Step 1 — Add the task type to `TaskType` enum (`domain/execution/enums.py`):**

```python
class TaskType(enum.Enum):
    ...
    MY_NEW_TASK = "my_new_task"
```

**Step 2 — Map it to a queue in the task router (`services/infra/execution/task_router.py`):**

```python
QUEUE_MAP: dict[TaskType, str] = {
    ...
    TaskType.MY_NEW_TASK: "queue:uploads",  # or whichever queue is appropriate
}
```

**Step 3 — Define the payload dataclass (`domain/execution/payloads/my_new_task.py`):**

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class MyNewTaskPayload:
    entity_client_id: str
    workspace_id: str
    # ... whatever the handler needs
```

**Step 4 — Implement the handler (`services/infra/jobs/handlers/my_new_task.py`):**

```python
from my_app.domain.execution.payloads.my_new_task import MyNewTaskPayload

def handle_my_new_task(raw: dict) -> None:
    payload = MyNewTaskPayload(**raw)
    # ... do the work
```

**Step 5 — Register the handler in the worker entry point (`workers/<queue>_worker.py`):**

```python
from my_app.services.infra.jobs.handlers.my_new_task import handle_my_new_task

HANDLER_MAP = {
    ...
    TaskType.MY_NEW_TASK: handle_my_new_task,
}
```

That is the complete contract for adding a new task type. No changes to `worker_base.py`, `run_worker()`, or any infra code.

**If the task is triggered by a command** (instant), call `create_instant_task()` from `task_factory` inside the command's transaction.

**If the task is triggered from another domain flow** (e.g. an event worker that spawns a follow-up task), call `create_execution_task()` directly with the appropriate `origin_source`.

**If the task is triggered on a schedule**, add a scheduler type to `domain/schedulers/enums.py` and add the mapping in the scheduler runner (see [37_scheduled_jobs.md](37_scheduled_jobs.md)).

---

## Failed task handling

`FAIL` state tasks must not be deleted automatically. They are business signals:

- Log at `ERROR` level with `task_id`, `task_type`, `last_error`, `try_count`.
- Retain in the database indefinitely.
- Review on a defined cadence (weekly at minimum).

**Manual replay:** Reset `state = OPEN`, `try_count = 0`, `next_retry_at = NULL` via a CLI command — never directly via SQL:

```bash
python scripts/backfill/retry_failed_task.py --task-id 123
python scripts/backfill/retry_failed_tasks.py --task-type notification --since 2026-01-01
```
