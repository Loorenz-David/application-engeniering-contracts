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
│    Task Router      │  scans OPEN tasks → publishes task_id to Redis queue
└──────────┬──────────┘
           │  { "task_id": 123 }
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
    NOTIFICATION = "notification"
    UPLOAD_IMAGE = "upload_image"

    # Delayed scheduler tasks — created by DelayedScheduler when due
    DELAYED_NOTIFY_TO_CUSTOMER = "delayed_notify_to_customer"
    DELAYED_SEND_REPORT = "delayed_send_report"
    DELAYED_REMINDER = "delayed_reminder"
    DELAYED_BATCH_NOTIFICATION = "delayed_batch_notification"

    # Recurring scheduler tasks — created by RecurringScheduler on each interval
    RECURRING_SEND_REPORT = "recurring_send_report"
    RECURRING_REMINDER = "recurring_reminder"
    RECURRING_PIN_TASK = "recurring_pin_task"


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
from my_app.domain.execution.enums import ExecutionTaskStateEnum, TaskType


class ExecutionTask(db.Model):
    __tablename__ = "execution_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

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
from my_app.domain.execution.enums import EventTaskOriginSourceEnum


class ExecutionPayload(db.Model):
    __tablename__ = "execution_payloads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    origin_source: Mapped[EventTaskOriginSourceEnum] = mapped_column(
        SAEnum(EventTaskOriginSourceEnum, name="event_task_origin_source_enum", create_type=True),
        nullable=False,
    )
    origin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    execution_task_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("execution_tasks.id"), nullable=False, unique=True
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
- `origin_id` is the integer PK of the scheduler row that created this task (`DelayedScheduler.id` or `RecurringScheduler.id`). It is `None` for instant tasks.
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
| `OPEN` → `PENDING` | Task Router | After publishing task_id to Redis queue |
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
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: "queue:notifications",
    TaskType.DELAYED_SEND_REPORT:        "queue:reports",
    TaskType.DELAYED_REMINDER:           "queue:notifications",
    TaskType.DELAYED_BATCH_NOTIFICATION: "queue:notifications",
    TaskType.RECURRING_SEND_REPORT:      "queue:reports",
    TaskType.RECURRING_REMINDER:         "queue:notifications",
    TaskType.RECURRING_PIN_TASK:         "queue:tasks",
}

POLL_INTERVAL_SECONDS = 2
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
            logger.error("No queue mapped for task_type=%s task_id=%s", task.task_type, task.id)
            continue

        redis.rpush(queue_name, str(task.id))
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
- The router publishes only `{"task_id": <int>}` — never the payload. Workers fetch from the DB.
- After publishing, the router sets `state = PENDING`. If the publish fails, `state` stays `OPEN` and the task is retried on the next scan.
- The router also drives the retry cycle: tasks with `state=RETRY_SCHEDULED` and `next_retry_at <= now` are reset to `OPEN`.

---

## Workers

Workers subscribe to one or more queues. They do not trust Redis delivery as execution ownership — ownership is established via an atomic DB claim.

### Worker flow

```
1. CONSUME task_id from Redis queue (blocking pop)
2. CLAIM: UPDATE execution_task
          SET state='in_progress', worker_id=<id>, locked_at=now, started_at=now
          WHERE id=<task_id> AND state='pending'
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

BACKOFF_SECONDS = [30, 120, 300]  # retry intervals by attempt index


def run_worker(queue_name: str, handler_map: dict[TaskType, Callable[[dict, int], None]]) -> None:
    app = create_app("production")
    redis = get_redis_client(app.config["REDIS_URI"])
    worker_id = f"{socket.gethostname()}:{queue_name}:{int(time.time())}"

    logger.info("Worker started | queue=%s worker_id=%s", queue_name, worker_id)

    with app.app_context():
        while True:
            raw = redis.blpop(queue_name, timeout=5)
            if not raw:
                continue

            task_id = int(raw[1])
            _process_task(task_id, worker_id, handler_map)


def _process_task(task_id: int, worker_id: str, handler_map: dict[TaskType, Callable[[dict, int], None]]) -> None:
    now = datetime.now(timezone.utc)

    # Atomic claim
    rows_updated = (
        db.session.query(ExecutionTask)
        .filter(ExecutionTask.id == task_id, ExecutionTask.state == ExecutionTaskStateEnum.PENDING)
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
        logger.info("task_id=%s already claimed — skipping", task_id)
        return

    task = db.session.get(ExecutionTask, task_id)
    handler = handler_map.get(task.task_type)

    if not handler:
        logger.error("No handler for task_type=%s task_id=%s", task.task_type, task_id)
        _mark_failed(task, "No handler registered for task_type.")
        return

    try:
        handler(task.payload.payload, task.id)
        task.state = ExecutionTaskStateEnum.COMPLETED
        task.completed_at = datetime.now(timezone.utc)
        db.session.commit()
        logger.info("task_id=%s completed | type=%s", task_id, task.task_type)

    except Exception as exc:
        _schedule_retry_or_fail(task, exc)


def _schedule_retry_or_fail(task: ExecutionTask, exc: Exception) -> None:
    task.try_count += 1
    task.last_error = str(exc)[:1024]

    if task.try_count < task.max_try:
        delay = BACKOFF_SECONDS[min(task.try_count - 1, len(BACKOFF_SECONDS) - 1)]
        task.state = ExecutionTaskStateEnum.RETRY_SCHEDULED
        task.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay)
        logger.warning(
            "task_id=%s retry_scheduled | attempt=%d next_retry_at=%s",
            task.id, task.try_count, task.next_retry_at,
        )
    else:
        task.state = ExecutionTaskStateEnum.FAIL
        logger.error(
            "task_id=%s permanently failed | attempt=%d error=%s",
            task.id, task.try_count, task.last_error,
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

HANDLER_MAP = {
    TaskType.NOTIFICATION:               handle_notification,
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: handle_notification,
    TaskType.DELAYED_REMINDER:           handle_reminder,
    TaskType.DELAYED_BATCH_NOTIFICATION: handle_notification,
    TaskType.RECURRING_REMINDER:         handle_reminder,
}

if __name__ == "__main__":
    run_worker("queue:notifications", HANDLER_MAP)
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
    recipient_id: int
    workspace_id: int
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


def handle_notification(raw: dict, task_id: int) -> None:
    payload = NotificationPayload(**raw)
    # payload is now typed — IDE-complete, validated at entry
    # task_id is available if this handler needs to create a scheduler with origin tracing
    # ... send notification via payload.channel, payload.recipient_id, etc.
    logger.info("Notification sent | recipient_id=%s channel=%s", payload.recipient_id, payload.channel)
```

Deserialising at handler entry means a missing or misnamed key raises `TypeError` immediately, before any side effect is attempted. The worker catches it, records the error, and schedules a retry.

**Handler rules:**
- Handlers receive `(raw: dict, task_id: int)` — no ORM instances, no `ServiceContext`.
- First line of every handler: deserialise into the typed payload dataclass.
- `task_id` is the current `ExecutionTask.id`. Pass it as `origin_id` to `create_delayed_scheduler()` or `create_recurring_scheduler()` when the handler needs to schedule follow-up work.
- Handlers must be idempotent. A handler called twice with the same payload must produce the same outcome.
- Handlers must not modify the payload.
- Handlers raise on unrecoverable failure — the worker's retry logic handles it.
- One handler = one side effect. Do not combine multiple integrations in one handler.

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
    origin_id: int | None = None,
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
        execution_task_id=task.id,
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

`create_instant_task()` is a thin convenience wrapper for commands. Scheduler runners call `create_execution_task()` directly, passing their `origin_source` and `origin_id`.

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
def handle_notification(raw: dict, task_id: int) -> None:
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
    entity_id: int
    workspace_id: int
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
