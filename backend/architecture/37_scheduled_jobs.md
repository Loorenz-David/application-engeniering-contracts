# 37 — Schedulers Contract

## What schedulers do

Schedulers determine **when** work should happen and **what** task type should be created. They do not execute business logic.

When a scheduler fires, it creates an `execution_task` with `state=OPEN` and an `execution_payload` snapshot. The task router picks it up and workers execute it. See [16_background_jobs.md](16_background_jobs.md) for the full execution system.

---

## Two scheduler types

| Type | Trigger | Table |
|---|---|---|
| **Delayed** | Fires once at a specific datetime (`scheduled_for`) | `delayed_schedulers` |
| **Recurring** | Fires repeatedly on a fixed interval | `recurring_schedulers` |

Both types share the same downstream: they produce an `execution_task`.

---

## Enums

```python
# domain/schedulers/enums.py
import enum


class DelayedSchedulerTypeEnum(enum.Enum):
    NOTIFY_TO_CUSTOMER = "notify_to_customer"
    SEND_REPORT = "send_report"
    REMINDER = "reminder"
    BATCH_NOTIFICATION = "batch_notification"


class RecurringSchedulerTypeEnum(enum.Enum):
    SEND_REPORT = "send_report"
    REMINDER = "reminder"
    PIN_TASK = "pin_task"


class RecurringSchedulerIntervalValueEnum(enum.Enum):
    SECONDS = "seconds"
    MINUTES = "minutes"
    DAYS = "days"
    MONTHS = "months"


class SchedulerStateEnum(enum.Enum):
    ACTIVE = "active"
    FIRED = "fired"      # delayed only — fired once, now terminal
    PAUSED = "paused"    # recurring only — temporarily suspended
    CANCELED = "canceled"
    ERROR = "error"


class SchedulerOriginSourceEnum(enum.Enum):
    COMMAND = "command"   # created directly by an HTTP request command
    WORKER  = "worker"    # created by a background worker handling a task
```

---

## Models

### DelayedScheduler

A delayed scheduler fires once at `scheduled_for`. After firing it transitions to `FIRED` and produces no further tasks.

```python
# models/tables/schedulers/delayed_scheduler.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from my_app.models import db
from my_app.domain.schedulers.enums import DelayedSchedulerTypeEnum, SchedulerStateEnum, SchedulerOriginSourceEnum


class DelayedScheduler(db.Model):
    __tablename__ = "delayed_schedulers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    type: Mapped[DelayedSchedulerTypeEnum] = mapped_column(
        SAEnum(DelayedSchedulerTypeEnum, name="delayed_scheduler_type_enum", create_type=True),
        nullable=False,
    )
    state: Mapped[SchedulerStateEnum] = mapped_column(
        SAEnum(SchedulerStateEnum, name="scheduler_state_enum", create_type=True),
        nullable=False,
        default=SchedulerStateEnum.ACTIVE,
        index=True,
    )

    origin_source: Mapped[SchedulerOriginSourceEnum] = mapped_column(
        SAEnum(SchedulerOriginSourceEnum, name="scheduler_origin_source_enum", create_type=True),
        nullable=False,
        default=SchedulerOriginSourceEnum.COMMAND,
    )
    origin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    payload_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### RecurringScheduler

A recurring scheduler fires on every interval, starting from `last_interval` (or `created_at` on first run). It stays `ACTIVE` between firings and is only terminal on `CANCELED`.

```python
# models/tables/schedulers/recurring_scheduler.py
from datetime import datetime, timezone
from sqlalchemy import String, Integer, DateTime, JSON
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from my_app.models import db
from my_app.domain.schedulers.enums import RecurringSchedulerTypeEnum, RecurringSchedulerIntervalValueEnum, SchedulerStateEnum, SchedulerOriginSourceEnum


class RecurringScheduler(db.Model):
    __tablename__ = "recurring_schedulers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)

    type: Mapped[RecurringSchedulerTypeEnum] = mapped_column(
        SAEnum(RecurringSchedulerTypeEnum, name="recurring_scheduler_type_enum", create_type=True),
        nullable=False,
    )
    state: Mapped[SchedulerStateEnum] = mapped_column(
        SAEnum(SchedulerStateEnum, name="scheduler_state_enum", create_type=True),
        nullable=False,
        default=SchedulerStateEnum.ACTIVE,
        index=True,
    )

    origin_source: Mapped[SchedulerOriginSourceEnum] = mapped_column(
        SAEnum(SchedulerOriginSourceEnum, name="scheduler_origin_source_enum", create_type=True),
        nullable=False,
        default=SchedulerOriginSourceEnum.COMMAND,
    )
    origin_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    interval: Mapped[int] = mapped_column(Integer, nullable=False)
    interval_value: Mapped[RecurringSchedulerIntervalValueEnum] = mapped_column(
        SAEnum(
            RecurringSchedulerIntervalValueEnum,
            name="recurring_scheduler_interval_value_enum",
            create_type=True,
        ),
        nullable=False,
    )

    last_interval: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    payload_snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
```

**`payload_snapshot` rules:**
- Captured when the scheduler row is created.
- Copied verbatim into each `ExecutionPayload` every time the scheduler fires.
- Workers must not rely on live data beyond what is in the snapshot. If a worker needs fresh data, it queries by an ID stored in the snapshot — it does not re-derive the full context.

---

## Scheduler runners

Both runners are background processes that poll the database and create `execution_task` rows.

### Delayed scheduler runner

```python
# services/infra/schedulers/delayed_scheduler_runner.py
import logging
import time
from datetime import datetime, timezone

from my_app import create_app
from my_app.models import db
from my_app.models.tables.schedulers.delayed_scheduler import DelayedScheduler
from my_app.models.tables.schedulers.enums import SchedulerStateEnum, DelayedSchedulerTypeEnum
from my_app.domain.execution.enums import TaskType, EventTaskOriginSourceEnum
from my_app.services.infra.execution.task_factory import create_execution_task

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10

DELAYED_TYPE_TO_TASK_TYPE: dict[DelayedSchedulerTypeEnum, TaskType] = {
    DelayedSchedulerTypeEnum.NOTIFY_TO_CUSTOMER:  TaskType.DELAYED_NOTIFY_TO_CUSTOMER,
    DelayedSchedulerTypeEnum.SEND_REPORT:         TaskType.DELAYED_SEND_REPORT,
    DelayedSchedulerTypeEnum.REMINDER:            TaskType.DELAYED_REMINDER,
    DelayedSchedulerTypeEnum.BATCH_NOTIFICATION:  TaskType.DELAYED_BATCH_NOTIFICATION,
}


def run_delayed_scheduler_runner() -> None:
    app = create_app("production")
    logger.info("Delayed scheduler runner started.")

    with app.app_context():
        while True:
            _fire_due_schedulers()
            time.sleep(POLL_INTERVAL_SECONDS)


def _fire_due_schedulers() -> None:
    now = datetime.now(timezone.utc)

    due = (
        db.session.query(DelayedScheduler)
        .filter(
            DelayedScheduler.state == SchedulerStateEnum.ACTIVE,
            DelayedScheduler.scheduled_for <= now,
        )
        .limit(50)
        .all()
    )

    for scheduler in due:
        try:
            create_execution_task(
                task_type=DELAYED_TYPE_TO_TASK_TYPE[scheduler.type],
                payload=scheduler.payload_snapshot,
                origin_source=EventTaskOriginSourceEnum.DELAYED_SCHEDULER,
                origin_id=scheduler.id,
                scheduled_at=scheduler.scheduled_for,
                event_client_id=scheduler.event_client_id,
            )
            scheduler.state = SchedulerStateEnum.FIRED
            scheduler.fired_at = now
        except Exception as exc:
            logger.exception(
                "Failed to fire delayed_scheduler_id=%s type=%s",
                scheduler.id, scheduler.type,
            )
            scheduler.state = SchedulerStateEnum.ERROR
            scheduler.last_error = str(exc)[:1024]

    if due:
        db.session.commit()
        logger.info("delayed_scheduler_runner | fired=%d", len(due))
```

### Recurring scheduler runner

```python
# services/infra/schedulers/recurring_scheduler_runner.py
import logging
import time
from datetime import datetime, timezone

from my_app import create_app
from my_app.models import db
from my_app.models.tables.schedulers.recurring_scheduler import RecurringScheduler
from my_app.models.tables.schedulers.enums import (
    SchedulerStateEnum,
    RecurringSchedulerTypeEnum,
    RecurringSchedulerIntervalValueEnum,
)
from my_app.domain.execution.enums import TaskType, EventTaskOriginSourceEnum
from my_app.services.infra.execution.task_factory import create_execution_task

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 10

RECURRING_TYPE_TO_TASK_TYPE: dict[RecurringSchedulerTypeEnum, TaskType] = {
    RecurringSchedulerTypeEnum.SEND_REPORT: TaskType.RECURRING_SEND_REPORT,
    RecurringSchedulerTypeEnum.REMINDER:    TaskType.RECURRING_REMINDER,
    RecurringSchedulerTypeEnum.PIN_TASK:    TaskType.RECURRING_PIN_TASK,
}

INTERVAL_UNIT_TO_SECONDS: dict[RecurringSchedulerIntervalValueEnum, int] = {
    RecurringSchedulerIntervalValueEnum.SECONDS: 1,
    RecurringSchedulerIntervalValueEnum.MINUTES: 60,
    RecurringSchedulerIntervalValueEnum.DAYS:    86_400,
    RecurringSchedulerIntervalValueEnum.MONTHS:  2_592_000,  # 30-day approximation
}


def run_recurring_scheduler_runner() -> None:
    app = create_app("production")
    logger.info("Recurring scheduler runner started.")

    with app.app_context():
        while True:
            _fire_due_recurring_schedulers()
            time.sleep(POLL_INTERVAL_SECONDS)


def _fire_due_recurring_schedulers() -> None:
    now = datetime.now(timezone.utc)

    due = (
        db.session.query(RecurringScheduler)
        .filter(RecurringScheduler.state == SchedulerStateEnum.ACTIVE)
        .all()
    )

    fired = 0
    for scheduler in due:
        if not _is_due(scheduler, now):
            continue

        try:
            create_execution_task(
                task_type=RECURRING_TYPE_TO_TASK_TYPE[scheduler.type],
                payload=scheduler.payload_snapshot,
                origin_source=EventTaskOriginSourceEnum.RECURRING_SCHEDULER,
                origin_id=scheduler.id,
                scheduled_at=now,
                event_client_id=scheduler.event_client_id,
            )
            scheduler.last_interval = now
            fired += 1
        except Exception as exc:
            logger.exception(
                "Failed to fire recurring_scheduler_id=%s type=%s",
                scheduler.id, scheduler.type,
            )
            scheduler.last_error = str(exc)[:1024]

    if fired > 0:
        db.session.commit()
        logger.info("recurring_scheduler_runner | fired=%d", fired)


def _is_due(scheduler: RecurringScheduler, now: datetime) -> bool:
    unit_seconds = INTERVAL_UNIT_TO_SECONDS[scheduler.interval_value]
    interval_seconds = scheduler.interval * unit_seconds
    reference = scheduler.last_interval or scheduler.created_at
    return (now - reference).total_seconds() >= interval_seconds
```

---

## Scheduler factory

A single module is the only place that creates scheduler rows. Commands and workers both go through it — nothing constructs scheduler models directly.

```python
# services/infra/schedulers/scheduler_factory.py
from dataclasses import asdict
from datetime import datetime, timezone

from my_app.models import db
from my_app.models.tables.schedulers.delayed_scheduler import DelayedScheduler
from my_app.models.tables.schedulers.recurring_scheduler import RecurringScheduler
from my_app.domain.schedulers.enums import (
    DelayedSchedulerTypeEnum,
    RecurringSchedulerTypeEnum,
    RecurringSchedulerIntervalValueEnum,
    SchedulerStateEnum,
    SchedulerOriginSourceEnum,
)


def create_delayed_scheduler(
    scheduler_type: DelayedSchedulerTypeEnum,
    scheduled_for: datetime,
    payload: dict,
    origin_source: SchedulerOriginSourceEnum = SchedulerOriginSourceEnum.COMMAND,
    origin_id: int | None = None,
    event_client_id: str | None = None,
) -> DelayedScheduler:
    scheduler = DelayedScheduler(
        type=scheduler_type,
        state=SchedulerStateEnum.ACTIVE,
        scheduled_for=scheduled_for,
        payload_snapshot=payload,
        origin_source=origin_source,
        origin_id=origin_id,
        event_client_id=event_client_id,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(scheduler)
    return scheduler


def create_recurring_scheduler(
    scheduler_type: RecurringSchedulerTypeEnum,
    interval: int,
    interval_value: RecurringSchedulerIntervalValueEnum,
    payload: dict,
    origin_source: SchedulerOriginSourceEnum = SchedulerOriginSourceEnum.COMMAND,
    origin_id: int | None = None,
    event_client_id: str | None = None,
) -> RecurringScheduler:
    scheduler = RecurringScheduler(
        type=scheduler_type,
        state=SchedulerStateEnum.ACTIVE,
        interval=interval,
        interval_value=interval_value,
        payload_snapshot=payload,
        origin_source=origin_source,
        origin_id=origin_id,
        event_client_id=event_client_id,
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(scheduler)
    return scheduler
```

`origin_source` defaults to `COMMAND`. Workers pass `origin_source=SchedulerOriginSourceEnum.WORKER` and `origin_id=task_id` so the scheduler row carries a full trace back to the execution task that created it.

---

## Creating schedulers

Schedulers are created by commands or workers. A command determines the business need; the scheduler records when and what.

### From a command

```python
# services/commands/record/schedule_record_reminder.py
from dataclasses import asdict
from datetime import datetime

from my_app.models import db
from my_app.domain.schedulers.enums import DelayedSchedulerTypeEnum
from my_app.domain.execution.payloads.reminder import ReminderPayload
from my_app.services.infra.schedulers.scheduler_factory import create_delayed_scheduler
from my_app.services.context import ServiceContext


def schedule_record_reminder(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data
    remind_at = datetime.fromisoformat(data["remind_at"])

    with db.session.begin():
        event = RecordEvent(
            state=EventStateEnum.REQUESTED,
            type=RecordEventTypeEnum.REMINDER,
            created_by_id=ctx.user_id,
            record_id=record.id,
        )
        db.session.add(event)
        db.session.flush()

        scheduler = create_delayed_scheduler(
            scheduler_type=DelayedSchedulerTypeEnum.REMINDER,
            scheduled_for=remind_at,
            payload=asdict(ReminderPayload(
                workspace_id=ctx.workspace_id,
                user_id=ctx.user_id,
                record_client_id=data["record_client_id"],
                message=data.get("message", "You have a reminder."),
            )),
            event_client_id=event.client_id,
        )

    return {"scheduler_client_id": scheduler.client_id, "scheduled_for": remind_at.isoformat()}
```

### From a worker

Workers receive `task_id` as their second argument. Pass it as `origin_id` so the scheduler row traces back to the task that created it.

```python
# services/infra/jobs/handlers/process_order.py
from dataclasses import asdict
from my_app.domain.schedulers.enums import DelayedSchedulerTypeEnum, SchedulerOriginSourceEnum
from my_app.domain.execution.payloads.process_order import ProcessOrderPayload
from my_app.domain.execution.payloads.reminder import ReminderPayload
from my_app.services.infra.schedulers.scheduler_factory import create_delayed_scheduler
from my_app.models import db
import datetime


def handle_process_order(raw: dict, task_id: int) -> None:
    payload = ProcessOrderPayload(**raw)
    # ... process the order ...

    remind_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1)
    create_delayed_scheduler(
        scheduler_type=DelayedSchedulerTypeEnum.REMINDER,
        scheduled_for=remind_at,
        payload=asdict(ReminderPayload(
            workspace_id=payload.workspace_id,
            user_id=payload.user_id,
            record_client_id=payload.order_client_id,
            message="Your order is ready for pickup.",
        )),
        origin_source=SchedulerOriginSourceEnum.WORKER,
        origin_id=task_id,
    )
    db.session.commit()
```

The full trace chain is now:

```
ExecutionTask (reminder)
  → ExecutionPayload.origin_id  → DelayedScheduler
  → DelayedScheduler.origin_id  → ExecutionTask (process_order)
  → ExecutionPayload.origin_id  → None (INSTANT — created by command)
```

---

## Rules

- **Schedulers define WHEN and WHAT — workers define HOW.** No business logic lives in a scheduler row or runner.
- **Always use `scheduler_factory` to create scheduler rows.** Never construct `DelayedScheduler` or `RecurringScheduler` directly.
- **Always build `payload_snapshot` with `asdict()` from the typed payload dataclass.** Never construct the dict by hand.
- **`payload_snapshot` is immutable.** Captured at creation time. Workers use it as-is.
- **Delayed schedulers fire exactly once.** After transitioning to `FIRED`, they are never re-processed.
- **Recurring schedulers use `last_interval` to track drift.** The runner checks `(now - last_interval) >= interval`. This prevents double-firing on runner restarts.
- **Scheduler runners are idempotent.** Running two runner processes simultaneously is safe because the atomic task creation and `last_interval` update are committed together — a concurrent runner that reads the same scheduler finds `last_interval` already updated and skips it.
- **Do not cancel a scheduler by deleting its row.** Set `state = CANCELED`. Deleted rows cannot be audited.
- **Workers creating schedulers must pass `origin_source=WORKER, origin_id=task_id`.** This is the contract for full traceability.

---

## File structure

```
my_app/
├── domain/
│   └── schedulers/
│       └── enums.py                       # all scheduler enums including SchedulerOriginSourceEnum
└── services/
    └── infra/
        └── schedulers/
            ├── scheduler_factory.py       # create_delayed_scheduler(), create_recurring_scheduler()
            ├── delayed_scheduler_runner.py
            └── recurring_scheduler_runner.py

models/
└── tables/
    └── schedulers/
        ├── delayed_scheduler.py
        └── recurring_scheduler.py

workers/                                   # process entry points
    ├── task_router.py
    ├── delayed_scheduler_runner.py
    └── recurring_scheduler_runner.py
```

---

## Scheduler catalog

Every scheduler type must be documented:

| Type | Scheduler | Task produced | Interval / trigger |
|---|---|---|---|
| Weekly report | `RecurringScheduler` / `SEND_REPORT` | `RECURRING_SEND_REPORT` | 7 days |
| Reminder | `RecurringScheduler` / `REMINDER` | `RECURRING_REMINDER` | configurable |
| Pin task | `RecurringScheduler` / `PIN_TASK` | `RECURRING_PIN_TASK` | configurable |
| Customer notification | `DelayedScheduler` / `NOTIFY_TO_CUSTOMER` | `DELAYED_NOTIFY_TO_CUSTOMER` | once at `scheduled_for` |
| Report | `DelayedScheduler` / `SEND_REPORT` | `DELAYED_SEND_REPORT` | once at `scheduled_for` |
| Reminder | `DelayedScheduler` / `REMINDER` | `DELAYED_REMINDER` | once at `scheduled_for` |
| Batch notification | `DelayedScheduler` / `BATCH_NOTIFICATION` | `DELAYED_BATCH_NOTIFICATION` | once at `scheduled_for` |

Update this table whenever a new scheduler type is added.
