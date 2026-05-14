from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, write_file as _write, touch_file as _touch


def _phase6(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 6 - Background Execution -----------------------------------")

    # ── Shared domain base enums ──────────────────────────────────────────────
    _write(root / a / "domain" / "base" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "base" / "enums.py", """\
import enum


class EventStateEnum(enum.Enum):
    \"\"\"Shared state enum for all domain event/operation tables (42_event.md).\"\"\"
    REQUESTED   = "requested"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    FAILED      = "failed"
""", force=force)

    # ── Execution domain enums ────────────────────────────────────────────────
    _write(root / a / "domain" / "execution" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "execution" / "enums.py", """\
import enum


class ExecutionTaskStateEnum(enum.Enum):
    OPEN             = "open"
    PENDING          = "pending"
    IN_PROGRESS      = "in_progress"
    RETRYING         = "retrying"
    RETRY_SCHEDULED  = "retry_scheduled"
    COMPLETED        = "completed"
    FAIL             = "fail"
    CANCEL           = "cancel"


class TaskType(enum.Enum):
    # Instant tasks — triggered directly by commands
    NOTIFICATION    = "notification"
    UPLOAD_IMAGE    = "upload_image"
    DELIVER_WEBHOOK = "deliver_webhook"

    # CREATE / SEND_PUSH notification tasks (used by notification system)
    CREATE_NOTIFICATIONS    = "create_notifications"
    SEND_PUSH_NOTIFICATION  = "send_push_notification"

    # Delayed scheduler tasks
    DELAYED_NOTIFY_TO_CUSTOMER  = "delayed_notify_to_customer"
    DELAYED_SEND_REPORT         = "delayed_send_report"
    DELAYED_REMINDER            = "delayed_reminder"
    DELAYED_BATCH_NOTIFICATION  = "delayed_batch_notification"

    # Recurring scheduler tasks
    RECURRING_SEND_REPORT = "recurring_send_report"
    RECURRING_REMINDER    = "recurring_reminder"
    RECURRING_PIN_TASK    = "recurring_pin_task"

    # Presence view-record tasks (enqueued by socket connect/disconnect handlers)
    RECORD_VIEW_START = "record_view_start"
    RECORD_VIEW_END   = "record_view_end"


class EventTaskOriginSourceEnum(enum.Enum):
    DELAYED_SCHEDULER   = "delayed_scheduler"
    RECURRING_SCHEDULER = "recurring_scheduler"
    INSTANT             = "instant"
""", force=force)

    # ── Payload dataclasses (domain/execution/payloads/) ──────────────────────
    _write(root / a / "domain" / "execution" / "payloads" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "execution" / "payloads" / "notification.py", """\
from dataclasses import dataclass


@dataclass(frozen=True)
class NotificationPayload:
    \"\"\"Payload for CREATE_NOTIFICATIONS and NOTIFICATION tasks.\"\"\"
    notification_type: str
    user_ids:          list[str]
    title:             str
    body:              str
    entity_type:       str | None = None
    entity_client_id:  str | None = None
    exclude_viewing:   list[dict] | None = None
""", force=force)
    _write(root / a / "domain" / "execution" / "payloads" / "reminder.py", """\
from dataclasses import dataclass


@dataclass(frozen=True)
class ReminderPayload:
    \"\"\"Payload for DELAYED_REMINDER and RECURRING_REMINDER tasks.\"\"\"
    workspace_id:     str
    user_id:          str
    entity_client_id: str
    message:          str = "You have a reminder."
""", force=force)
    _write(root / a / "domain" / "execution" / "payloads" / "upload.py", """\
from dataclasses import dataclass


@dataclass(frozen=True)
class UploadPayload:
    \"\"\"Payload for UPLOAD_IMAGE tasks.\"\"\"
    pending_upload_id: str
    workspace_id:      str
    created_by_id:     str
""", force=force)

    # ── Event mixin (models/base/event.py) — with __init_subclass__ guard ─────
    _write(root / a / "models" / "base" / "event.py", f"""\
import enum
from datetime import datetime, timezone
from typing import ClassVar

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from {a}.domain.base.enums import EventStateEnum


class Event:
    \"\"\"Lifecycle mixin for concrete domain operation event tables (42_event.md).
    Always combine with IdentityMixin and Base:
      class MyEvent(IdentityMixin, Event, Base): ...
    Set EVENT_TYPE_ENUM and EVENT_ERROR_ENUM on every concrete subclass.
    \"\"\"

    EVENT_TYPE_ENUM:  ClassVar[type[enum.Enum]]
    EVENT_ERROR_ENUM: ClassVar[type[enum.Enum]]

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        # Only enforce on SQLAlchemy model classes (have __tablename__)
        if hasattr(cls, "__tablename__"):
            for attr in ("EVENT_TYPE_ENUM", "EVENT_ERROR_ENUM"):
                if not hasattr(cls, attr):
                    raise AttributeError(
                        f"Concrete event model {{cls.__name__}} must define {{attr}}."
                    )

    @declared_attr
    def created_by_id(cls) -> Mapped[str]:
        return mapped_column(
            String(64), ForeignKey("users.client_id", deferrable=True),
            nullable=False, index=True,
        )

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
            SAEnum(cls.EVENT_TYPE_ENUM, name=f"{{cls.__tablename__}}_type_enum", create_type=True),
            nullable=False,
        )

    @declared_attr
    def last_error(cls) -> Mapped[enum.Enum | None]:
        return mapped_column(
            SAEnum(cls.EVENT_ERROR_ENUM, name=f"{{cls.__tablename__}}_error_enum", create_type=True),
            nullable=True,
        )

    attempts:     Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int]        = mapped_column(Integer, nullable=False, default=3)
    description:  Mapped[str | None] = mapped_column(String(512), nullable=True)
""", force=force)

    # ── ExecutionTask + ExecutionPayload models ───────────────────────────────
    _touch(root / a / "models" / "tables" / "execution" / "__init__.py", force=force)

    _write(root / a / "models" / "tables" / "execution" / "execution_task.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.execution.enums import ExecutionTaskStateEnum, TaskType
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ExecutionTask(IdentityMixin, Base):
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

    try_count:  Mapped[int]        = mapped_column(Integer, nullable=False, default=0)
    max_try:    Mapped[int]        = mapped_column(Integer, nullable=False, default=3)
    last_error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    worker_id:  Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at:   Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    payload: Mapped["ExecutionPayload"] = relationship(
        "ExecutionPayload", back_populates="execution_task", uselist=False
    )
""", force=force)

    _write(root / a / "models" / "tables" / "execution" / "execution_payload.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.domain.execution.enums import EventTaskOriginSourceEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class ExecutionPayload(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "epl"
    __tablename__ = "execution_payloads"

    origin_source: Mapped[EventTaskOriginSourceEnum] = mapped_column(
        SAEnum(EventTaskOriginSourceEnum, name="event_task_origin_source_enum", create_type=True),
        nullable=False,
    )
    origin_id:        Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_client_id:  Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload:          Mapped[dict]       = mapped_column(JSON, nullable=False)

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
""", force=force)

    # ── Task factory ─────────────────────────────────────────────────────────
    _write(root / a / "services" / "infra" / "execution" / "__init__.py", "", force=force)

    _write(root / a / "services" / "infra" / "execution" / "task_factory.py", f"""\
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from {a}.domain.execution.enums import EventTaskOriginSourceEnum, ExecutionTaskStateEnum, TaskType
from {a}.models.tables.execution.execution_payload import ExecutionPayload
from {a}.models.tables.execution.execution_task import ExecutionTask


async def create_execution_task(
    session: AsyncSession,
    task_type: TaskType,
    payload: dict,
    origin_source: EventTaskOriginSourceEnum,
    origin_id: str | None = None,
    scheduled_at: datetime | None = None,
    event_client_id: str | None = None,
    max_try: int = 3,
) -> ExecutionTask:
    \"\"\"Single entry point for creating an ExecutionTask + ExecutionPayload pair.
    Always call inside an open transaction so task creation is atomic with the
    domain write that triggered it.
    \"\"\"
    now = datetime.now(timezone.utc)
    task = ExecutionTask(
        task_type=task_type,
        state=ExecutionTaskStateEnum.OPEN,
        max_try=max_try,
        created_at=now,
        scheduled_at=scheduled_at,
    )
    session.add(task)
    await session.flush()  # assign client_id

    session.add(ExecutionPayload(
        origin_source=origin_source,
        origin_id=origin_id,
        event_client_id=event_client_id,
        payload=payload,
        execution_task_id=task.client_id,
        created_at=now,
    ))
    return task


async def create_instant_task(
    session: AsyncSession,
    task_type: TaskType,
    payload: dict,
    event_client_id: str | None = None,
    max_try: int = 3,
) -> ExecutionTask:
    \"\"\"Convenience wrapper for commands that trigger instant (non-scheduled) tasks.\"\"\"
    return await create_execution_task(
        session=session,
        task_type=task_type,
        payload=payload,
        origin_source=EventTaskOriginSourceEnum.INSTANT,
        event_client_id=event_client_id,
        max_try=max_try,
    )
""", force=force)

    # ── Task router (async, LISTEN/NOTIFY hybrid) ────────────────────────────
    _write(root / a / "services" / "infra" / "execution" / "task_router.py", f"""\
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import asyncpg
from sqlalchemy import select

from {a}.config import settings
from {a}.domain.execution.enums import ExecutionTaskStateEnum, TaskType
from {a}.models.database import get_db_session
from {a}.models.tables.execution.execution_task import ExecutionTask
from {a}.services.infra.redis import get_redis_client
from {a}.services.infra.sleep.activity_tracker import ActivityTracker

logger = logging.getLogger(__name__)

QUEUE_MAP: dict[TaskType, str] = {{
    TaskType.NOTIFICATION:               "queue:notifications",
    TaskType.CREATE_NOTIFICATIONS:       "queue:notifications",
    TaskType.SEND_PUSH_NOTIFICATION:     "queue:notifications",
    TaskType.UPLOAD_IMAGE:               "queue:uploads",
    TaskType.DELIVER_WEBHOOK:            "queue:webhooks",
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: "queue:notifications",
    TaskType.DELAYED_SEND_REPORT:        "queue:reports",
    TaskType.DELAYED_REMINDER:           "queue:notifications",
    TaskType.DELAYED_BATCH_NOTIFICATION: "queue:notifications",
    TaskType.RECURRING_SEND_REPORT:      "queue:reports",
    TaskType.RECURRING_REMINDER:         "queue:notifications",
    TaskType.RECURRING_PIN_TASK:         "queue:tasks",
    TaskType.RECORD_VIEW_START:          "queue:presence",
    TaskType.RECORD_VIEW_END:            "queue:presence",
}}

FALLBACK_POLL_SECONDS    = 30   # safety net for LISTEN/NOTIFY drop — not routing latency
BATCH_SIZE               = 50
STALE_IN_PROGRESS_MINUTES = 90  # must exceed max(HANDLER_TIMEOUT_SECONDS) / 60
STUCK_PENDING_MINUTES    = 5

_notify_event: asyncio.Event = asyncio.Event()


async def _listen_for_task_events() -> None:
    \"\"\"Dedicated asyncpg LISTEN connection — reconnects automatically on drop.\"\"\"
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


async def _sleep_monitor() -> None:
    while True:
        await asyncio.sleep(60)
        if not settings.sleep_mode_enabled:
            continue
        if ActivityTracker.idle_seconds() >= settings.idle_sleep_threshold_seconds:
            if not ActivityTracker.is_sleeping():
                ActivityTracker.enter_sleep()


async def run_task_router() -> None:
    logger.info("Task router started.")
    redis = get_redis_client(settings.redis_url)
    asyncio.create_task(_listen_for_task_events())
    asyncio.create_task(_sleep_monitor())

    while True:
        if ActivityTracker.is_sleeping():
            await asyncio.sleep(30)
            continue

        try:
            await asyncio.wait_for(_notify_event.wait(), timeout=FALLBACK_POLL_SECONDS)
        except asyncio.TimeoutError:
            pass
        _notify_event.clear()

        try:
            await _route_open_tasks(redis)
            await _requeue_retry_scheduled_tasks()
            await _cleanup_stale_tasks()
            await _recover_stuck_pending_tasks()
        except Exception:
            logger.exception("task_router: poll error")


async def _route_open_tasks(redis) -> None:
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask)
            .where(ExecutionTask.state == ExecutionTaskStateEnum.OPEN)
            .limit(BATCH_SIZE)
        )
        tasks = result.scalars().all()

        for task in tasks:
            queue_name = QUEUE_MAP.get(task.task_type)
            if not queue_name:
                logger.error(
                    "no queue mapped | task_type=%s task_id=%s",
                    task.task_type, task.client_id,
                )
                continue
            redis.rpush(queue_name, task.client_id)
            task.state = ExecutionTaskStateEnum.PENDING

        if tasks:
            await session.commit()
            depths = {{name: redis.llen(name) for name in set(QUEUE_MAP.values())}}
            logger.info("task_router | routed=%d queue_depths=%s", len(tasks), depths)


async def _requeue_retry_scheduled_tasks() -> None:
    now = datetime.now(timezone.utc)
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(
                ExecutionTask.state == ExecutionTaskStateEnum.RETRY_SCHEDULED,
                ExecutionTask.next_retry_at <= now,
            ).limit(BATCH_SIZE)
        )
        tasks = result.scalars().all()
        for task in tasks:
            task.state = ExecutionTaskStateEnum.OPEN
            task.next_retry_at = None

        if tasks:
            await session.commit()
            logger.info("task_router | requeued_retries=%d", len(tasks))


async def _cleanup_stale_tasks() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=STALE_IN_PROGRESS_MINUTES)
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(
                ExecutionTask.state == ExecutionTaskStateEnum.IN_PROGRESS,
                ExecutionTask.locked_at < cutoff,
            ).limit(BATCH_SIZE)
        )
        tasks = result.scalars().all()
        for task in tasks:
            task.state     = ExecutionTaskStateEnum.OPEN
            task.worker_id = None
            task.locked_at = None
            logger.warning(
                "stale_task_recovered | task_id=%s task_type=%s",
                task.client_id, task.task_type.value,
            )
        if tasks:
            await session.commit()


async def _recover_stuck_pending_tasks() -> None:
    \"\"\"Reset PENDING tasks older than STUCK_PENDING_MINUTES whose Redis entry was lost.\"\"\"
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
""", force=force)

    # ── Task DB session (context-manager for background handlers) ────────────
    _write(root / a / "services" / "infra" / "execution" / "db.py", f"""\
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import _session_factory


@asynccontextmanager
async def task_db_session() -> AsyncIterator[AsyncSession]:
    \"\"\"Async context manager for background task handlers.\"\"\"
    if _session_factory is None:
        raise RuntimeError("DB not initialised. Call init_db() before running workers.")
    async with _session_factory() as session:
        yield session
""", force=force)

    # ── Worker base (async, three-session pattern + SIGTERM) ─────────────────
    _write(root / a / "services" / "infra" / "execution" / "worker_base.py", f"""\
import asyncio
import logging
import random
import signal
import socket
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from {a}.config import settings
from {a}.domain.execution.enums import ExecutionTaskStateEnum, TaskType
from {a}.models.database import get_db_session
from {a}.models.tables.execution.execution_task import ExecutionTask
from {a}.services.infra.redis import get_redis_client

logger = logging.getLogger(__name__)

BACKOFF_SECONDS = [30, 120, 300]
BACKOFF_JITTER  = 0.15  # ±15%

HANDLER_TIMEOUT_SECONDS: dict[str, int] = {{
    "default":      300,   # 5 minutes
    "upload_image": 3600,  # 1 hour
    "send_report":  600,   # 10 minutes
}}

TaskHandlerFn = Callable[[dict, str], Awaitable[None]]

_shutdown_event: asyncio.Event = asyncio.Event()


def _register_shutdown_handler() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown_event.set)


async def run_worker(
    queue_name: str,
    handler_map: dict[TaskType, TaskHandlerFn],
) -> None:
    _register_shutdown_handler()
    loop = asyncio.get_event_loop()
    redis = get_redis_client(settings.redis_url)
    worker_id = f"{{socket.gethostname()}}:{{queue_name}}:{{int(time.time())}}"
    logger.info("worker_start | queue=%s worker_id=%s", queue_name, worker_id)

    current_task_id: str | None = None
    try:
        while not _shutdown_event.is_set():
            # Run blpop in thread pool so the event loop stays responsive to SIGTERM
            raw = await loop.run_in_executor(None, lambda: redis.blpop(queue_name, timeout=2))
            if not raw:
                continue
            current_task_id = raw[1] if isinstance(raw[1], str) else raw[1].decode()
            await _process_task(current_task_id, worker_id, handler_map)
            current_task_id = None
    finally:
        if current_task_id:
            await _rescue_in_flight_task(current_task_id)
        logger.info("worker_shutdown | queue=%s worker_id=%s", queue_name, worker_id)


async def _execute_with_timeout(
    handler: TaskHandlerFn,
    raw_payload: dict,
    task_client_id: str,
    task_type_value: str,
) -> None:
    timeout = HANDLER_TIMEOUT_SECONDS.get(task_type_value, HANDLER_TIMEOUT_SECONDS["default"])
    try:
        await asyncio.wait_for(handler(raw_payload, task_client_id), timeout=timeout)
    except asyncio.TimeoutError:
        raise RuntimeError(f"Handler timed out after {{timeout}}s")


async def _process_task(
    task_client_id: str,
    worker_id: str,
    handler_map: dict[TaskType, TaskHandlerFn],
) -> None:
    # Session 1 — claim (closes immediately after)
    task_type, raw_payload = await _claim_task(task_client_id, worker_id)
    if task_type is None:
        return

    handler = handler_map.get(task_type)
    if not handler:
        await _mark_no_handler(task_client_id, task_type)
        return

    # Session 2 — handler runs via task_db_session(); pool slot is free here
    start = time.monotonic()
    try:
        await _execute_with_timeout(handler, raw_payload, task_client_id, task_type.value)
        elapsed_ms = (time.monotonic() - start) * 1000
        # Session 3 — finalize (closes immediately after)
        await _finalize_task(task_client_id, worker_id, task_type, elapsed_ms)
    except Exception as exc:
        elapsed_ms = (time.monotonic() - start) * 1000
        await _fail_task(task_client_id, worker_id, task_type, exc, elapsed_ms)


async def _claim_task(
    task_client_id: str,
    worker_id: str,
) -> tuple[TaskType | None, dict]:
    now = datetime.now(timezone.utc)
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask)
            .where(
                ExecutionTask.client_id == task_client_id,
                ExecutionTask.state == ExecutionTaskStateEnum.PENDING,
            )
            .with_for_update(skip_locked=True)
        )
        task = result.scalar_one_or_none()
        if task is None:
            logger.info("task_id=%s already claimed — skipping", task_client_id)
            return None, {{}}

        task.state      = ExecutionTaskStateEnum.IN_PROGRESS
        task.worker_id  = worker_id
        task.locked_at  = now
        task.started_at = now

        await session.refresh(task, attribute_names=["payload"])
        raw_payload = task.payload.payload if task.payload else {{}}
        task_type = task.task_type
        await session.commit()
        return task_type, raw_payload


async def _mark_no_handler(task_client_id: str, task_type: TaskType) -> None:
    logger.error("no handler | task_type=%s task_id=%s", task_type, task_client_id)
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_client_id)
        )
        task = result.scalar_one_or_none()
        if task:
            task.state      = ExecutionTaskStateEnum.FAIL
            task.last_error = "No handler registered for task_type."
            await session.commit()


async def _finalize_task(
    task_client_id: str,
    worker_id: str,
    task_type: TaskType,
    elapsed_ms: float,
) -> None:
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_client_id)
        )
        task = result.scalar_one_or_none()
        if task:
            task.state        = ExecutionTaskStateEnum.COMPLETED
            task.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(
                "task_completed | task_id=%s task_type=%s worker=%s elapsed_ms=%.1f",
                task_client_id, task_type.value, worker_id, elapsed_ms,
            )


async def _fail_task(
    task_client_id: str,
    worker_id: str,
    task_type: TaskType,
    exc: Exception,
    elapsed_ms: float,
) -> None:
    logger.error(
        "task_failed | task_id=%s task_type=%s worker=%s elapsed_ms=%.1f error=%s",
        task_client_id, task_type.value, worker_id, elapsed_ms, str(exc)[:200],
    )
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_client_id)
        )
        task = result.scalar_one_or_none()
        if task:
            await _schedule_retry_or_fail(session, task, exc)


async def _rescue_in_flight_task(task_client_id: str) -> None:
    \"\"\"Called in finally block on SIGTERM — rescues in-flight task to RETRY_SCHEDULED.\"\"\"
    async for session in get_db_session():
        result = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_client_id)
        )
        task = result.scalar_one_or_none()
        if task and task.state == ExecutionTaskStateEnum.IN_PROGRESS:
            task.state         = ExecutionTaskStateEnum.RETRY_SCHEDULED
            task.next_retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)
            logger.warning("worker_sigterm_rescue | task_id=%s", task_client_id)
            await session.commit()


async def _schedule_retry_or_fail(session, task: ExecutionTask, exc: Exception) -> None:
    task.try_count  += 1
    task.last_error  = str(exc)[:1024]

    if task.try_count < task.max_try:
        base   = BACKOFF_SECONDS[min(task.try_count - 1, len(BACKOFF_SECONDS) - 1)]
        jitter = base * BACKOFF_JITTER
        delay  = base + random.uniform(-jitter, jitter)
        task.state         = ExecutionTaskStateEnum.RETRY_SCHEDULED
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
    await session.commit()
""", force=force)

    # ── Task handler stubs ────────────────────────────────────────────────────
    _write(root / a / "services" / "infra" / "jobs" / "__init__.py", "", force=force)
    _write(root / a / "services" / "infra" / "jobs" / "handlers" / "__init__.py", "", force=force)

    _write(root / a / "services" / "infra" / "jobs" / "handlers" / "notification.py", f"""\
import logging

from {a}.domain.execution.payloads.notification import NotificationPayload

logger = logging.getLogger(__name__)


async def handle_notification(raw: dict, task_id: str) -> None:
    \"\"\"Deserialise payload and deliver notification. Implement per-app.\"\"\"
    payload = NotificationPayload(**raw)
    logger.info(
        "notification | type=%s recipients=%d",
        payload.notification_type,
        len(payload.user_ids),
    )
""", force=force)

    _write(root / a / "services" / "infra" / "jobs" / "handlers" / "reminder.py", f"""\
import logging

from {a}.domain.execution.payloads.reminder import ReminderPayload

logger = logging.getLogger(__name__)


async def handle_reminder(raw: dict, task_id: str) -> None:
    \"\"\"Deserialise payload and send reminder. Implement per-app.\"\"\"
    payload = ReminderPayload(**raw)
    logger.info(
        "reminder | user_id=%s entity=%s",
        payload.user_id,
        payload.entity_client_id,
    )
""", force=force)

    # ── Worker entry points ───────────────────────────────────────────────────
    _write(root / a / "workers" / "notification_worker.py", f"""\
import asyncio

from {a}.domain.execution.enums import TaskType
from {a}.services.infra.execution.worker_base import run_worker
from {a}.services.infra.jobs.handlers.notification import handle_notification
from {a}.services.infra.jobs.handlers.reminder import handle_reminder

HANDLER_MAP = {{
    TaskType.NOTIFICATION:               handle_notification,
    TaskType.CREATE_NOTIFICATIONS:       handle_notification,
    TaskType.DELAYED_NOTIFY_TO_CUSTOMER: handle_notification,
    TaskType.DELAYED_REMINDER:           handle_reminder,
    TaskType.DELAYED_BATCH_NOTIFICATION: handle_notification,
    TaskType.RECURRING_REMINDER:         handle_reminder,
}}

if __name__ == "__main__":
    asyncio.run(run_worker("queue:notifications", HANDLER_MAP))
""", force=force)

    _write(root / a / "workers" / "presence_worker.py", f"""\
import asyncio

from {a}.domain.execution.enums import TaskType
from {a}.services.infra.execution.worker_base import run_worker
from {a}.services.tasks.presence.record_view_end import handle_record_view_end
from {a}.services.tasks.presence.record_view_start import handle_record_view_start

HANDLER_MAP = {{
    TaskType.RECORD_VIEW_START: handle_record_view_start,
    TaskType.RECORD_VIEW_END:   handle_record_view_end,
}}

if __name__ == "__main__":
    asyncio.run(run_worker("queue:presence", HANDLER_MAP))
""", force=force)

    _write(root / a / "workers" / "task_router_process.py", f"""\
import asyncio

from {a}.models.database import init_db
from {a}.services.infra.execution.task_router import run_task_router


async def main() -> None:
    await init_db()
    await run_task_router()


if __name__ == "__main__":
    asyncio.run(main())
""", force=force)

    # ── Postgres LISTEN/NOTIFY trigger (apply after execution_tasks migration) ─
    _write(root / "scripts" / "apply_db_triggers.py", f"""\
\"\"\"Apply Postgres triggers required for LISTEN/NOTIFY task wakeup.

Run once after `alembic upgrade head`:
    python scripts/apply_db_triggers.py

The trigger fires pg_notify('task_open', task_id) on every INSERT and
state UPDATE to 'open' on execution_tasks, waking the task router immediately
rather than waiting for the fallback poll interval.
\"\"\"
import asyncio

import asyncpg

from {a}.config import settings

_TRIGGER_SQL = \"\"\"
CREATE OR REPLACE FUNCTION notify_task_open()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('task_open', NEW.client_id::text);
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_task_open ON execution_tasks;

CREATE TRIGGER trg_task_open
AFTER INSERT OR UPDATE OF state ON execution_tasks
FOR EACH ROW WHEN (NEW.state = 'open')
EXECUTE FUNCTION notify_task_open();
\"\"\"


async def _apply() -> None:
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(_TRIGGER_SQL)
        print("[apply_db_triggers] Trigger trg_task_open applied.")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(_apply())
""", force=force)

    # ── Legacy task stubs (presence/signals — referenced by phase 7/8) ───────
    _write(root / a / "services" / "tasks" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "record_view_start.py", """\
async def handle_record_view_start(payload: dict, task_id: str) -> None:
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "record_view_end.py", """\
async def handle_record_view_end(payload: dict, task_id: str) -> None:
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "signals" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "signals" / "push_user_signal.py", f"""\
from {a}.sockets.manager import manager


async def handle_push_user_signal(payload: dict) -> None:
    user_id = payload.get("user_id")
    signal  = payload.get("signal")
    if user_id and signal:
        await manager.send_to_user(user_id, "user:signal", {{"signal": signal}})
""", force=force)

    # ── Audit log model ────────────────────────────────────────────────────────
    _touch(root / a / "models" / "tables" / "audit" / "__init__.py", force=force)

    _write(root / a / "models" / "tables" / "audit" / "audit_log.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class AuditLog(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "aud"
    __tablename__ = "audit_logs"

    # What happened
    event: Mapped[str] = mapped_column(String(128), nullable=False, index=True)

    # Who did it
    actor_user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=True, index=True
    )
    actor_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Workspace scope
    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True
    )

    # Affected resource
    resource_type:      Mapped[str | None] = mapped_column(String(64),  nullable=True)
    resource_client_id: Mapped[str | None] = mapped_column(String(64),  nullable=True)

    # Structured context
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Request metadata
    ip_address:  Mapped[str | None] = mapped_column(String(64),  nullable=True)
    user_agent:  Mapped[str | None] = mapped_column(String(512), nullable=True)
    request_id:  Mapped[str | None] = mapped_column(String(64),  nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True,
        default=lambda: datetime.now(timezone.utc),
    )
""", force=force)

    # ── Audit write helper ────────────────────────────────────────────────────
    _touch(root / a / "services" / "infra" / "audit" / "__init__.py", force=force)

    _write(root / a / "services" / "infra" / "audit" / "audited_events.py", f"""\
from __future__ import annotations

import os

# Default audited events — high-risk auth, membership, and destructive actions.
_BASE_AUDITED_EVENTS: frozenset[str] = frozenset({{
    # Auth
    "auth:signed-in",
    "auth:signed-out",
    "auth:token-refreshed",
    "auth:password-changed",
    # Workspace membership
    "workspace:member-invited",
    "workspace:member-removed",
    "workspace:role-changed",
    # Cases
    "case:state-changed",
    "case:deleted",
    "case:participant-removed",
    # Messages
    "message:deleted",
}})

# Domain modules extend this by calling register_audited_events() at startup.
_EXTENSIONS: set[str] = set()


def register_audited_events(events: set[str] | list[str]) -> None:
    \"\"\"Register additional audited events from a domain module.
    Call during application startup before the first request.
    \"\"\"
    _EXTENSIONS.update(events)


def get_audited_events() -> frozenset[str]:
    \"\"\"Return the merged set of audited event names.
    Combines base defaults + registered extensions + AUDITED_EVENTS env override.
    \"\"\"
    combined: set[str] = set(_BASE_AUDITED_EVENTS) | _EXTENSIONS
    env_override = os.environ.get("AUDITED_EVENTS", "")
    if env_override.strip():
        combined |= {{e.strip() for e in env_override.split(",") if e.strip()}}
    return frozenset(combined)
""", force=force)

    _write(root / a / "services" / "infra" / "audit" / "write_audit.py", f"""\
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.tables.audit.audit_log import AuditLog

if TYPE_CHECKING:
    from fastapi import Request


async def write_audit(
    session: AsyncSession,
    event: str,
    workspace_id: str,
    actor_user_id: str | None = None,
    actor_label:   str | None = None,
    resource_type: str | None = None,
    resource_client_id: str | None = None,
    detail: dict | None = None,
    request: "Request | None" = None,
) -> None:
    \"\"\"Write one audit log entry inside the caller's open transaction.\"\"\"
    ip_address  = _get_ip(request) if request else None
    user_agent  = request.headers.get("User-Agent", "")[:512] if request else None
    request_id  = getattr(getattr(request, "state", None), "request_id", None) if request else None

    entry = AuditLog(
        event=event,
        actor_user_id=actor_user_id,
        actor_label=actor_label,
        workspace_id=workspace_id,
        resource_type=resource_type,
        resource_client_id=resource_client_id,
        detail=detail or {{}},
        ip_address=ip_address,
        user_agent=user_agent,
        request_id=request_id,
        created_at=datetime.now(timezone.utc),
    )
    session.add(entry)


async def write_audit_from_event(
    session: AsyncSession,
    event_name: str,
    workspace_id: str,
    resource_client_id: str | None = None,
    detail: dict | None = None,
    occurred_at: datetime | None = None,
) -> None:
    \"\"\"Lightweight audit write for event-bus audit_handler (no Request available).\"\"\"
    entry = AuditLog(
        event=event_name,
        workspace_id=workspace_id,
        resource_client_id=resource_client_id,
        detail=detail or {{}},
        created_at=occurred_at or datetime.now(timezone.utc),
    )
    session.add(entry)


def _get_ip(request: "Request") -> str | None:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
""", force=force)

    # ── Audit query ───────────────────────────────────────────────────────────
    _touch(root / a / "services" / "queries" / "audit" / "__init__.py", force=force)

    _write(root / a / "services" / "queries" / "audit" / "list_audit_events.py", f"""\
from datetime import datetime

from sqlalchemy import select

from {a}.models.tables.audit.audit_log import AuditLog
from {a}.services.context import ServiceContext


async def list_audit_events(ctx: ServiceContext) -> dict:
    \"\"\"List audit events scoped to the caller's workspace.
    Query params (all optional):
      event, actor_user_id, resource_client_id, since (ISO), until (ISO), limit (int).
    \"\"\"
    params = ctx.incoming_data
    limit  = min(int(params.get("limit", 50)), 200)

    stmt = select(AuditLog).where(AuditLog.workspace_id == ctx.workspace_id)

    if params.get("event"):
        stmt = stmt.where(AuditLog.event == params["event"])
    if params.get("actor_user_id"):
        stmt = stmt.where(AuditLog.actor_user_id == params["actor_user_id"])
    if params.get("resource_client_id"):
        stmt = stmt.where(AuditLog.resource_client_id == params["resource_client_id"])
    if params.get("since"):
        stmt = stmt.where(AuditLog.created_at >= datetime.fromisoformat(params["since"]))
    if params.get("until"):
        stmt = stmt.where(AuditLog.created_at <= datetime.fromisoformat(params["until"]))

    stmt   = stmt.order_by(AuditLog.created_at.desc()).limit(limit + 1)
    result = await ctx.session.execute(stmt)
    rows   = result.scalars().all()

    has_more = len(rows) > limit
    return {{
        "events":   [_serialize(e) for e in rows[:limit]],
        "has_more": has_more,
    }}


def _serialize(entry: AuditLog) -> dict:
    return {{
        "client_id":         entry.client_id,
        "event":             entry.event,
        "actor":             entry.actor_label or f"user:{{entry.actor_user_id}}",
        "resource_type":     entry.resource_type,
        "resource_id":       entry.resource_client_id,
        "detail":            entry.detail,
        "ip_address":        entry.ip_address,
        "occurred_at":       entry.created_at.isoformat(),
    }}
""", force=force)

    # ── Audit router ──────────────────────────────────────────────────────────
    _write(root / a / "routers" / "api_v1" / "audit.py", f"""\
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import require_roles
from {a}.routers.utils.roles import ADMIN
from {a}.services.context import ServiceContext
from {a}.services.queries.audit.list_audit_events import list_audit_events
from {a}.services.run_service import run_service

router = APIRouter()


@router.get("")
async def list_audit_events_route(
    claims:             dict         = Depends(require_roles([ADMIN])),
    session:            AsyncSession = Depends(get_db),
    event:              str | None   = Query(None),
    actor_user_id:      str | None   = Query(None),
    resource_client_id: str | None   = Query(None),
    since:              str | None   = Query(None),
    until:              str | None   = Query(None),
    limit:              int          = Query(50, le=200),
):
    ctx = ServiceContext(
        incoming_data={{
            "event":              event,
            "actor_user_id":      actor_user_id,
            "resource_client_id": resource_client_id,
            "since":              since,
            "until":              until,
            "limit":              limit,
        }},
        identity=claims,
        session=session,
    )
    outcome = await run_service(list_audit_events, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data)
""", force=force)

    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import auth, health\n",
        f"from {a}.routers.api_v1 import audit, auth, health\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n',
        '    app.include_router(audit.router, prefix="/api/v1/audit", tags=["audit"])\n'
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n',
    )

    # ── Register new models in models/__init__.py ─────────────────────────────
    append_once(
        root / a / "models" / "__init__.py",
        (
            f"from {a}.models.tables.execution import execution_task  # noqa: F401\n"
            f"from {a}.models.tables.execution import execution_payload  # noqa: F401\n"
            f"from {a}.models.tables.audit import audit_log  # noqa: F401\n"
        ),
    )
