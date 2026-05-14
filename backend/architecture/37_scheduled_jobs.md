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
from my_app.models.base.identity import IdentityMixin
from my_app.domain.schedulers.enums import DelayedSchedulerTypeEnum, SchedulerStateEnum, SchedulerOriginSourceEnum


class DelayedScheduler(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "dsch"
    __tablename__ = "delayed_schedulers"

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
    origin_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
from my_app.models.base.identity import IdentityMixin
from my_app.domain.schedulers.enums import RecurringSchedulerTypeEnum, RecurringSchedulerIntervalValueEnum, SchedulerStateEnum, SchedulerOriginSourceEnum


class RecurringScheduler(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "rsch"
    __tablename__ = "recurring_schedulers"

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
    origin_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
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
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from my_app.config import settings
from my_app.domain.execution.enums import EventTaskOriginSourceEnum, TaskType
from my_app.domain.schedulers.enums import DelayedSchedulerTypeEnum, SchedulerStateEnum
from my_app.models.database import get_db_session
from my_app.models.tables.schedulers.delayed_scheduler import DelayedScheduler
from my_app.services.infra.execution.task_factory import create_execution_task
from my_app.services.infra.sleep.activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS      = 10
SCHEDULER_SLEEP_CAP_SECONDS = 300   # max sleep between checks even when no jobs are due
ERROR_RETRY_MINUTES         = 15    # how long before an ERROR-state scheduler is retried

DELAYED_TYPE_TO_TASK_TYPE: dict[DelayedSchedulerTypeEnum, TaskType] = {
    DelayedSchedulerTypeEnum.NOTIFY_TO_CUSTOMER: TaskType.DELAYED_NOTIFY_TO_CUSTOMER,
    DelayedSchedulerTypeEnum.SEND_REPORT:        TaskType.DELAYED_SEND_REPORT,
    DelayedSchedulerTypeEnum.REMINDER:           TaskType.DELAYED_REMINDER,
    DelayedSchedulerTypeEnum.BATCH_NOTIFICATION: TaskType.DELAYED_BATCH_NOTIFICATION,
}


async def run_delayed_scheduler_runner() -> None:
    logger.info("Delayed scheduler runner started.")
    next_due_at: datetime | None = None

    while True:
        if ActivityTracker.is_sleeping():
            if next_due_at is not None:
                sleep_for = max(0.0, (next_due_at - datetime.now(timezone.utc)).total_seconds())
                sleep_for = min(sleep_for, SCHEDULER_SLEEP_CAP_SECONDS)
            else:
                sleep_for = SCHEDULER_SLEEP_CAP_SECONDS
            await asyncio.sleep(sleep_for)
            if next_due_at is None or datetime.now(timezone.utc) < next_due_at:
                continue
            ActivityTracker.touch()

        try:
            await _fire_due_schedulers()
            await _retry_errored_schedulers()
        except Exception:
            logger.exception("delayed_scheduler_runner: poll error")

        next_due_at = await _get_next_scheduled_for()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _fire_due_schedulers() -> None:
    now = datetime.now(timezone.utc)
    async for session in get_db_session():
        result = await session.execute(
            select(DelayedScheduler).where(
                DelayedScheduler.state == SchedulerStateEnum.ACTIVE,
                DelayedScheduler.scheduled_for <= now,
            ).limit(50)
        )
        due = result.scalars().all()
        fired = errors = 0
        for scheduler in due:
            try:
                await create_execution_task(
                    session=session,
                    task_type=DELAYED_TYPE_TO_TASK_TYPE[scheduler.type],
                    payload=scheduler.payload_snapshot,
                    origin_source=EventTaskOriginSourceEnum.DELAYED_SCHEDULER,
                    origin_id=scheduler.client_id,
                    scheduled_at=scheduler.scheduled_for,
                    event_client_id=scheduler.event_client_id,
                )
                scheduler.state    = SchedulerStateEnum.FIRED
                scheduler.fired_at = now
                ActivityTracker.touch()
                fired += 1
            except Exception as exc:
                logger.exception("delayed_scheduler | fire_failed | id=%s", scheduler.client_id)
                scheduler.state      = SchedulerStateEnum.ERROR
                scheduler.last_error = str(exc)[:1024]
                errors += 1
        if fired or errors:
            await session.commit()
            logger.info("delayed_scheduler_runner | fired=%d errors=%d", fired, errors)


async def _retry_errored_schedulers() -> None:
    """Reset ERROR-state schedulers after a cooldown so transient failures self-recover."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=ERROR_RETRY_MINUTES)
    async for session in get_db_session():
        result = await session.execute(
            select(DelayedScheduler).where(
                DelayedScheduler.state == SchedulerStateEnum.ERROR,
                DelayedScheduler.scheduled_for > datetime.now(timezone.utc),  # not yet past
                DelayedScheduler.updated_at < cutoff,
            ).limit(20)
        )
        errored = result.scalars().all()
        for scheduler in errored:
            scheduler.state      = SchedulerStateEnum.ACTIVE
            scheduler.last_error = None
            logger.warning("delayed_scheduler | error_retry | id=%s", scheduler.client_id)
        if errored:
            await session.commit()


async def _get_next_scheduled_for() -> datetime | None:
    async for session in get_db_session():
        result = await session.execute(
            select(func.min(DelayedScheduler.scheduled_for)).where(
                DelayedScheduler.state == SchedulerStateEnum.ACTIVE,
                DelayedScheduler.scheduled_for > datetime.now(timezone.utc),
            )
        )
        return result.scalar_one_or_none()
```

### Recurring scheduler runner

```python
# services/infra/schedulers/recurring_scheduler_runner.py
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from my_app.config import settings
from my_app.domain.execution.enums import EventTaskOriginSourceEnum, TaskType
from my_app.domain.schedulers.enums import (
    RecurringSchedulerIntervalValueEnum,
    RecurringSchedulerTypeEnum,
    SchedulerStateEnum,
)
from my_app.models.database import get_db_session
from my_app.models.tables.schedulers.recurring_scheduler import RecurringScheduler
from my_app.services.infra.execution.task_factory import create_execution_task
from my_app.services.infra.sleep.activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS       = 10
SCHEDULER_SLEEP_CAP_SECONDS = 300
BATCH_SIZE                  = 200   # prevents unbounded load on large scheduler tables

RECURRING_TYPE_TO_TASK_TYPE: dict[RecurringSchedulerTypeEnum, TaskType] = {
    RecurringSchedulerTypeEnum.SEND_REPORT: TaskType.RECURRING_SEND_REPORT,
    RecurringSchedulerTypeEnum.REMINDER:    TaskType.RECURRING_REMINDER,
    RecurringSchedulerTypeEnum.PIN_TASK:    TaskType.RECURRING_PIN_TASK,
}

INTERVAL_UNIT_TO_SECONDS: dict[RecurringSchedulerIntervalValueEnum, int] = {
    RecurringSchedulerIntervalValueEnum.SECONDS: 1,
    RecurringSchedulerIntervalValueEnum.MINUTES: 60,
    RecurringSchedulerIntervalValueEnum.DAYS:    86_400,
    RecurringSchedulerIntervalValueEnum.MONTHS:  2_592_000,
}


async def run_recurring_scheduler_runner() -> None:
    logger.info("Recurring scheduler runner started.")
    next_due_at: datetime | None = None

    while True:
        if ActivityTracker.is_sleeping():
            if next_due_at is not None:
                sleep_for = max(0.0, (next_due_at - datetime.now(timezone.utc)).total_seconds())
                sleep_for = min(sleep_for, SCHEDULER_SLEEP_CAP_SECONDS)
            else:
                sleep_for = SCHEDULER_SLEEP_CAP_SECONDS
            await asyncio.sleep(sleep_for)
            if next_due_at is None or datetime.now(timezone.utc) < next_due_at:
                continue
            ActivityTracker.touch()

        try:
            await _fire_due_recurring_schedulers()
        except Exception:
            logger.exception("recurring_scheduler_runner: poll error")

        next_due_at = await _get_next_run_at()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _fire_due_recurring_schedulers() -> None:
    now = datetime.now(timezone.utc)
    async for session in get_db_session():
        result = await session.execute(
            select(RecurringScheduler)
            .where(RecurringScheduler.state == SchedulerStateEnum.ACTIVE)
            .limit(BATCH_SIZE)   # prevents unbounded memory load
        )
        candidates = result.scalars().all()

        fired = errors = 0
        for scheduler in candidates:
            if not _is_due(scheduler, now):
                continue
            try:
                await create_execution_task(
                    session=session,
                    task_type=RECURRING_TYPE_TO_TASK_TYPE[scheduler.type],
                    payload=scheduler.payload_snapshot,
                    origin_source=EventTaskOriginSourceEnum.RECURRING_SCHEDULER,
                    origin_id=scheduler.client_id,
                    scheduled_at=now,
                    event_client_id=scheduler.event_client_id,
                )
                scheduler.last_interval = now
                ActivityTracker.touch()
                fired += 1
            except Exception as exc:
                logger.exception("recurring_scheduler | fire_failed | id=%s", scheduler.client_id)
                scheduler.last_error = str(exc)[:1024]
                errors += 1

        if fired or errors:   # commit both successes and error updates
            await session.commit()
            logger.info("recurring_scheduler_runner | fired=%d errors=%d", fired, errors)


async def _get_next_run_at() -> datetime | None:
    async for session in get_db_session():
        result = await session.execute(
            select(func.min(RecurringScheduler.next_run_at)).where(
                RecurringScheduler.state == SchedulerStateEnum.ACTIVE
            )
        )
        return result.scalar_one_or_none()


def _is_due(scheduler: RecurringScheduler, now: datetime) -> bool:
    unit_seconds     = INTERVAL_UNIT_TO_SECONDS[scheduler.interval_value]
    interval_seconds = scheduler.interval * unit_seconds
    reference        = scheduler.last_interval or scheduler.created_at
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
            record_id=record.client_id,
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


def handle_process_order(raw: dict, task_id: str) -> None:
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
- **Commit on both fired and errored.** The recurring runner commits `if fired or errors` — never only on success. `last_error` must be persisted even when no tasks were successfully fired so failures are visible in the DB.
- **Recurring runner must use `LIMIT(BATCH_SIZE)`.** Never load all `ACTIVE` schedulers without a bound. With thousands of active schedulers an unbounded query causes memory spikes on every poll cycle.
- **ERROR-state delayed schedulers must self-recover.** `_retry_errored_schedulers()` resets `ERROR` → `ACTIVE` after `ERROR_RETRY_MINUTES` (15 min default) for schedulers whose `scheduled_for` is still in the future. Without this, a transient DB error permanently kills the job.
- **Scheduler runners participate in sleep mode using the cached alarm-clock pattern.** Cache `next_due_at` from the last active cycle. During sleep, sleep to that time rather than a flat interval. Call `ActivityTracker.touch()` before submitting any task so the full system wakes before the task enters the queue. See [22_performance.md](22_performance.md) for the full pattern.

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
