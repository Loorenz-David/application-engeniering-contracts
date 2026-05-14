from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase12_worker_runtime(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 13 - Worker Runtime + Schedulers --------------------------")

    _touch(root / a / "workers" / "__init__.py", force=force)
    _touch(root / a / "queues" / "__init__.py", force=force)
    _touch(root / a / "tasks" / "__init__.py", force=force)

    _write(root / a / "queues" / "registry.py", """\
from __future__ import annotations

QUEUE_NAMES = ["default", "critical", "replay", "dead-letter"]
""", force=force)

    _write(root / a / "queues" / "runtime.py", f"""\
from __future__ import annotations

import redis
from rq import Queue

from {a}.config import settings
from {a}.queues.registry import QUEUE_NAMES


def queue_for(name: str) -> Queue:
    if name not in QUEUE_NAMES:
        raise RuntimeError(f"Unknown queue '{{name}}'. Known queues: {{', '.join(QUEUE_NAMES)}}")
    conn = redis.from_url(settings.redis_url)
    return Queue(name, connection=conn)
""", force=force)

    _write(root / a / "workers" / "logging.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event
from {a}.core.logging.context import bind_execution_context


def bind_worker_context(worker_name: str) -> tuple[str, str]:
    execution_id, worker_id = bind_execution_context(worker_id=worker_name)
    log_event("worker.context.bound", execution_id=execution_id, worker_id=worker_id)
    return execution_id, worker_id
""", force=force)

    _write(root / a / "workers" / "retry.py", """\
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryMetadata:
    max_attempts: int = 5
    backoff_seconds: int = 10


DEFAULT_RETRY = RetryMetadata()
""", force=force)

    _write(root / a / "workers" / "dead_letter.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event


def send_to_dead_letter(task_type: str, payload: dict, reason: str) -> None:
    log_event(
        "worker.dead_letter",
        task_type=task_type,
        reason=reason,
        payload_size=len(str(payload)),
    )
""", force=force)

    _write(root / a / "workers" / "health.py", f"""\
from __future__ import annotations

import redis

from {a}.config import settings


def worker_healthcheck() -> dict[str, str]:
    client = redis.from_url(settings.redis_url, decode_responses=True)
    pong = client.ping()
    return {{"redis": "ok" if pong else "error"}}
""", force=force)

    _write(root / a / "workers" / "runtime.py", f"""\
from __future__ import annotations

from rq import Worker

from {a}.config import settings
from {a}.core.logging.config import configure_logging, log_event
from {a}.queues.registry import QUEUE_NAMES
from {a}.services.infra.redis import get_redis_client
from {a}.workers.logging import bind_worker_context


def run_worker(worker_name: str = "worker") -> None:
    configure_logging()
    bind_worker_context(worker_name)
    connection = get_redis_client(settings.redis_url)
    worker = Worker(QUEUE_NAMES, connection=connection, name=worker_name)
    log_event("worker.start", worker_id=worker_name)
    worker.work(with_scheduler=True)
""", force=force)

    _write(root / a / "tasks" / "registry.py", """\
from __future__ import annotations

# Explicit task registration only. No runtime auto-discovery.
REGISTERED_TASKS: dict[str, str] = {}
""", force=force)

    _write(root / "scripts" / "worker.py", f"""\
from {a}.workers.runtime import run_worker


if __name__ == "__main__":
    run_worker("worker-main")
""", force=force)

    _write(root / "scripts" / "worker_healthcheck.py", f"""\
from {a}.workers.health import worker_healthcheck


if __name__ == "__main__":
    health = worker_healthcheck()
    if health.get("redis") != "ok":
        raise SystemExit(1)
""", force=force)

    replace_once(
        root / "docker-compose.yml",
        "    depends_on:\n"
        "      postgres:\n"
        "        condition: service_healthy\n"
        "      redis:\n"
        "        condition: service_healthy\n"
        "    profiles:\n"
        "      - app\n",
        "    depends_on:\n"
        "      postgres:\n"
        "        condition: service_healthy\n"
        "      redis:\n"
        "        condition: service_healthy\n"
        "    healthcheck:\n"
        "      test: [\"CMD\", \"python\", \"scripts/worker_healthcheck.py\"]\n"
        "      interval: 10s\n"
        "      timeout: 5s\n"
        "      retries: 12\n"
        "    profiles:\n"
        "      - app\n",
    )

    append_once(
        root / "Makefile",
        "\n# Worker runtime\n"
        "worker:\n"
        "\tpython worker.py\n\n"
        "worker-dev:\n"
        "\tpython scripts/worker.py\n\n"
        "worker-logs:\n"
        "\tdocker compose logs -f worker\n",
    )

    # ── Schedulers (37_scheduled_jobs.md) ─────────────────────────────────────
    _write(root / a / "domain" / "schedulers" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "schedulers" / "enums.py", """\
import enum


class DelayedSchedulerTypeEnum(enum.Enum):
    NOTIFY_TO_CUSTOMER  = "notify_to_customer"
    SEND_REPORT         = "send_report"
    REMINDER            = "reminder"
    BATCH_NOTIFICATION  = "batch_notification"


class RecurringSchedulerTypeEnum(enum.Enum):
    SEND_REPORT = "send_report"
    REMINDER    = "reminder"
    PIN_TASK    = "pin_task"


class RecurringSchedulerIntervalValueEnum(enum.Enum):
    SECONDS = "seconds"
    MINUTES = "minutes"
    DAYS    = "days"
    MONTHS  = "months"


class SchedulerStateEnum(enum.Enum):
    ACTIVE   = "active"
    FIRED    = "fired"     # delayed only — fired once, now terminal
    PAUSED   = "paused"    # recurring only — temporarily suspended
    CANCELED = "canceled"
    ERROR    = "error"


class SchedulerOriginSourceEnum(enum.Enum):
    COMMAND = "command"  # created directly by an HTTP request command
    WORKER  = "worker"   # created by a background worker handling a task
""", force=force)

    _touch(root / a / "models" / "tables" / "schedulers" / "__init__.py", force=force)

    _write(root / a / "models" / "tables" / "schedulers" / "delayed_scheduler.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from {a}.domain.schedulers.enums import (
    DelayedSchedulerTypeEnum,
    SchedulerOriginSourceEnum,
    SchedulerStateEnum,
)
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class DelayedScheduler(IdentityMixin, Base):
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

    origin_id:        Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_client_id:  Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scheduled_for:    Mapped[datetime]   = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    payload_snapshot: Mapped[dict]       = mapped_column(JSON, nullable=False)
    last_error:       Mapped[str | None] = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fired_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
""", force=force)

    _write(root / a / "models" / "tables" / "schedulers" / "recurring_scheduler.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from {a}.domain.schedulers.enums import (
    RecurringSchedulerIntervalValueEnum,
    RecurringSchedulerTypeEnum,
    SchedulerOriginSourceEnum,
    SchedulerStateEnum,
)
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class RecurringScheduler(IdentityMixin, Base):
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

    origin_id:       Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    interval:       Mapped[int]                              = mapped_column(Integer, nullable=False)
    interval_value: Mapped[RecurringSchedulerIntervalValueEnum] = mapped_column(
        SAEnum(
            RecurringSchedulerIntervalValueEnum,
            name="recurring_scheduler_interval_value_enum",
            create_type=True,
        ),
        nullable=False,
    )

    last_interval:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_snapshot: Mapped[dict]            = mapped_column(JSON, nullable=False)
    last_error:       Mapped[str | None]      = mapped_column(String(1024), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False,
    )
""", force=force)

    # ── Scheduler factory ─────────────────────────────────────────────────────
    _touch(root / a / "services" / "infra" / "schedulers" / "__init__.py", force=force)

    _write(root / a / "services" / "infra" / "schedulers" / "scheduler_factory.py", f"""\
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from {a}.domain.schedulers.enums import (
    DelayedSchedulerTypeEnum,
    RecurringSchedulerIntervalValueEnum,
    RecurringSchedulerTypeEnum,
    SchedulerOriginSourceEnum,
    SchedulerStateEnum,
)
from {a}.models.tables.schedulers.delayed_scheduler import DelayedScheduler
from {a}.models.tables.schedulers.recurring_scheduler import RecurringScheduler


async def create_delayed_scheduler(
    session: AsyncSession,
    scheduler_type: DelayedSchedulerTypeEnum,
    scheduled_for: datetime,
    payload: dict,
    origin_source: SchedulerOriginSourceEnum = SchedulerOriginSourceEnum.COMMAND,
    origin_id: str | None = None,
    event_client_id: str | None = None,
) -> DelayedScheduler:
    \"\"\"Single entry point for creating delayed scheduler rows.\"\"\"
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
    session.add(scheduler)
    return scheduler


async def create_recurring_scheduler(
    session: AsyncSession,
    scheduler_type: RecurringSchedulerTypeEnum,
    interval: int,
    interval_value: RecurringSchedulerIntervalValueEnum,
    payload: dict,
    origin_source: SchedulerOriginSourceEnum = SchedulerOriginSourceEnum.COMMAND,
    origin_id: str | None = None,
    event_client_id: str | None = None,
) -> RecurringScheduler:
    \"\"\"Single entry point for creating recurring scheduler rows.\"\"\"
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
    session.add(scheduler)
    return scheduler
""", force=force)

    # ── Delayed scheduler runner (async, alarm-clock sleep) ───────────────────
    _write(root / a / "services" / "infra" / "schedulers" / "delayed_scheduler_runner.py", f"""\
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func

from {a}.domain.execution.enums import EventTaskOriginSourceEnum, TaskType
from {a}.domain.schedulers.enums import DelayedSchedulerTypeEnum, SchedulerStateEnum
from {a}.models.database import get_db_session
from {a}.models.tables.schedulers.delayed_scheduler import DelayedScheduler
from {a}.services.infra.execution.task_factory import create_execution_task
from {a}.services.infra.sleep.activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS      = 10
SCHEDULER_SLEEP_CAP_SECONDS = 300   # max sleep even when no jobs are due
ERROR_RETRY_MINUTES         = 15

DELAYED_TYPE_TO_TASK_TYPE: dict[DelayedSchedulerTypeEnum, TaskType] = {{
    DelayedSchedulerTypeEnum.NOTIFY_TO_CUSTOMER: TaskType.DELAYED_NOTIFY_TO_CUSTOMER,
    DelayedSchedulerTypeEnum.SEND_REPORT:        TaskType.DELAYED_SEND_REPORT,
    DelayedSchedulerTypeEnum.REMINDER:           TaskType.DELAYED_REMINDER,
    DelayedSchedulerTypeEnum.BATCH_NOTIFICATION: TaskType.DELAYED_BATCH_NOTIFICATION,
}}


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
            ActivityTracker.touch()  # due time arrived — wake the system before firing

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
                logger.exception(
                    "delayed_scheduler | fire_failed | id=%s type=%s",
                    scheduler.client_id, scheduler.type,
                )
                scheduler.state      = SchedulerStateEnum.ERROR
                scheduler.last_error = str(exc)[:1024]
                scheduler.updated_at = now
                errors += 1

        if fired or errors:
            await session.commit()
            logger.info("delayed_scheduler_runner | fired=%d errors=%d", fired, errors)


async def _retry_errored_schedulers() -> None:
    \"\"\"Reset ERROR-state schedulers after a cooldown so transient failures self-recover.\"\"\"
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=ERROR_RETRY_MINUTES)
    async for session in get_db_session():
        result = await session.execute(
            select(DelayedScheduler).where(
                DelayedScheduler.state == SchedulerStateEnum.ERROR,
                DelayedScheduler.scheduled_for > now,   # target time not yet past
                DelayedScheduler.updated_at < cutoff,
            ).limit(20)
        )
        errored = result.scalars().all()
        for scheduler in errored:
            scheduler.state      = SchedulerStateEnum.ACTIVE
            scheduler.last_error = None
            scheduler.updated_at = now
            logger.warning("delayed_scheduler | error_retry | id=%s", scheduler.client_id)
        if errored:
            await session.commit()


async def _get_next_scheduled_for() -> datetime | None:
    \"\"\"Return the earliest future scheduled_for across all ACTIVE delayed schedulers.\"\"\"
    async for session in get_db_session():
        result = await session.execute(
            select(func.min(DelayedScheduler.scheduled_for)).where(
                DelayedScheduler.state == SchedulerStateEnum.ACTIVE,
                DelayedScheduler.scheduled_for > datetime.now(timezone.utc),
            )
        )
        return result.scalar_one_or_none()
""", force=force)

    # ── Recurring scheduler runner (async, alarm-clock sleep) ─────────────────
    _write(root / a / "services" / "infra" / "schedulers" / "recurring_scheduler_runner.py", f"""\
import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from {a}.domain.execution.enums import EventTaskOriginSourceEnum, TaskType
from {a}.domain.schedulers.enums import (
    RecurringSchedulerIntervalValueEnum,
    RecurringSchedulerTypeEnum,
    SchedulerStateEnum,
)
from {a}.models.database import get_db_session
from {a}.models.tables.schedulers.recurring_scheduler import RecurringScheduler
from {a}.services.infra.execution.task_factory import create_execution_task
from {a}.services.infra.sleep.activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS       = 10
SCHEDULER_SLEEP_CAP_SECONDS = 300   # max sleep between checks when sleeping
BATCH_SIZE                  = 200   # prevents unbounded memory load

RECURRING_TYPE_TO_TASK_TYPE: dict[RecurringSchedulerTypeEnum, TaskType] = {{
    RecurringSchedulerTypeEnum.SEND_REPORT: TaskType.RECURRING_SEND_REPORT,
    RecurringSchedulerTypeEnum.REMINDER:    TaskType.RECURRING_REMINDER,
    RecurringSchedulerTypeEnum.PIN_TASK:    TaskType.RECURRING_PIN_TASK,
}}

INTERVAL_UNIT_TO_SECONDS: dict[RecurringSchedulerIntervalValueEnum, int] = {{
    RecurringSchedulerIntervalValueEnum.SECONDS: 1,
    RecurringSchedulerIntervalValueEnum.MINUTES: 60,
    RecurringSchedulerIntervalValueEnum.DAYS:    86_400,
    RecurringSchedulerIntervalValueEnum.MONTHS:  2_592_000,
}}


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
            ActivityTracker.touch()  # due time arrived — wake the system before firing

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
            .limit(BATCH_SIZE)
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
                logger.exception(
                    "recurring_scheduler | fire_failed | id=%s type=%s",
                    scheduler.client_id, scheduler.type,
                )
                scheduler.last_error = str(exc)[:1024]
                errors += 1

        if fired or errors:   # commit both successes and error updates
            await session.commit()
            logger.info("recurring_scheduler_runner | fired=%d errors=%d", fired, errors)


async def _get_next_run_at() -> datetime | None:
    \"\"\"Compute earliest next fire time across all ACTIVE recurring schedulers.\"\"\"
    async for session in get_db_session():
        result = await session.execute(
            select(RecurringScheduler)
            .where(RecurringScheduler.state == SchedulerStateEnum.ACTIVE)
            .limit(BATCH_SIZE)
        )
        schedulers = result.scalars().all()
        if not schedulers:
            return None
        next_times = []
        for s in schedulers:
            unit_seconds     = INTERVAL_UNIT_TO_SECONDS[s.interval_value]
            interval_seconds = s.interval * unit_seconds
            reference        = s.last_interval or s.created_at
            next_times.append(reference + timedelta(seconds=interval_seconds))
        return min(next_times)


def _is_due(scheduler: RecurringScheduler, now: datetime) -> bool:
    unit_seconds     = INTERVAL_UNIT_TO_SECONDS[scheduler.interval_value]
    interval_seconds = scheduler.interval * unit_seconds
    reference        = scheduler.last_interval or scheduler.created_at
    return (now - reference).total_seconds() >= interval_seconds
""", force=force)

    # ── Worker entry points for schedulers ────────────────────────────────────
    _write(root / a / "workers" / "delayed_scheduler_runner.py", f"""\
import asyncio

from {a}.models.database import init_db
from {a}.services.infra.schedulers.delayed_scheduler_runner import run_delayed_scheduler_runner


async def main() -> None:
    await init_db()
    await run_delayed_scheduler_runner()


if __name__ == "__main__":
    asyncio.run(main())
""", force=force)

    _write(root / a / "workers" / "recurring_scheduler_runner.py", f"""\
import asyncio

from {a}.models.database import init_db
from {a}.services.infra.schedulers.recurring_scheduler_runner import run_recurring_scheduler_runner


async def main() -> None:
    await init_db()
    await run_recurring_scheduler_runner()


if __name__ == "__main__":
    asyncio.run(main())
""", force=force)

    # ── Register scheduler models ─────────────────────────────────────────────
    append_once(
        root / a / "models" / "__init__.py",
        (
            f"from {a}.models.tables.schedulers import delayed_scheduler  # noqa: F401\n"
            f"from {a}.models.tables.schedulers import recurring_scheduler  # noqa: F401\n"
        ),
    )

    append_once(
        root / "Makefile",
        "\n# Scheduler runners\n"
        "delayed-scheduler:\n"
        f"\tpython {a}/workers/delayed_scheduler_runner.py\n\n"
        "recurring-scheduler:\n"
        f"\tpython {a}/workers/recurring_scheduler_runner.py\n\n"
        "task-router:\n"
        f"\tpython {a}/workers/task_router_process.py\n\n"
        "# Run before every production deploy\n"
        "pre-deploy:\n"
        "\tAPP_ENV=production alembic upgrade head\n"
        "\tAPP_ENV=production PYTHONPATH=. python scripts/apply_db_triggers.py\n",
    )

    _write(root / "Procfile", f"""\
web: python run.py
worker: python worker.py
task-router: python {a}/workers/task_router_process.py
delayed-scheduler: python {a}/workers/delayed_scheduler_runner.py
recurring-scheduler: python {a}/workers/recurring_scheduler_runner.py
""", force=force)
