from pathlib import Path

import typer

from bootstrap.writer import write_file as _write


def _phase6(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 6 - Background Execution -----------------------------------")

    _write(root / a / "domain" / "execution" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "execution" / "enums.py", """\
from enum import StrEnum


class TaskType(StrEnum):
    RECORD_VIEW_START = "RECORD_VIEW_START"
    RECORD_VIEW_END = "RECORD_VIEW_END"
    CREATE_NOTIFICATIONS = "CREATE_NOTIFICATIONS"
    SEND_PUSH_NOTIFICATION = "SEND_PUSH_NOTIFICATION"
    PUSH_USER_SIGNAL = "PUSH_USER_SIGNAL"


class EventStateEnum(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
""", force=force)
    _write(root / a / "models" / "base" / "event.py", f"""\
from datetime import datetime

from sqlalchemy import DateTime, JSON, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from {a}.domain.execution.enums import EventStateEnum


class Event:
    state: Mapped[EventStateEnum] = mapped_column(
        SAEnum(EventStateEnum, name="event_state_enum", create_type=True),
        nullable=False,
        default=EventStateEnum.PENDING,
        index=True,
    )
    event_data: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    worker_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    worker_error: Mapped[str | None] = mapped_column(String(2048), nullable=True)
""", force=force)

    _write(root / a / "services" / "infra" / "execution" / "__init__.py", "", force=force)
    _write(root / a / "services" / "infra" / "execution" / "db.py", f"""\
from contextlib import asynccontextmanager

from {a}.models.database import get_db_session


@asynccontextmanager
async def task_db_session():
    async for session in get_db_session():
        yield session
""", force=force)
    _write(root / a / "services" / "infra" / "execution" / "registry.py", f"""\
from collections.abc import Awaitable, Callable

from {a}.domain.execution.enums import TaskType
from {a}.services.tasks.notifications.create_notifications import handle_create_notifications
from {a}.services.tasks.notifications.send_push_notification import handle_send_push_notification
from {a}.services.tasks.presence.record_view_end import handle_record_view_end
from {a}.services.tasks.presence.record_view_start import handle_record_view_start
from {a}.services.tasks.signals.push_user_signal import handle_push_user_signal

TaskHandler = Callable[[dict], Awaitable[None]]

TASK_REGISTRY: dict[TaskType, TaskHandler] = {{
    TaskType.RECORD_VIEW_START: handle_record_view_start,
    TaskType.RECORD_VIEW_END: handle_record_view_end,
    TaskType.CREATE_NOTIFICATIONS: handle_create_notifications,
    TaskType.SEND_PUSH_NOTIFICATION: handle_send_push_notification,
    TaskType.PUSH_USER_SIGNAL: handle_push_user_signal,
}}


async def dispatch_task(task_type: str, payload: dict) -> None:
    task = TaskType(task_type)
    handler = TASK_REGISTRY[task]
    await handler(payload)
""", force=force)
    _write(root / a / "services" / "infra" / "execution" / "task_factory.py", f"""\
from rq import Queue

from {a}.config import settings
from {a}.domain.execution.enums import TaskType
from {a}.services.infra.redis import get_redis_client


def create_instant_task(task_type: TaskType, payload: dict):
    queue = Queue(connection=get_redis_client(settings.redis_url))
    return queue.enqueue(
        "{a}.services.infra.execution.worker_job.run_task",
        task_type.value,
        payload,
    )
""", force=force)
    _write(root / a / "services" / "infra" / "execution" / "worker_job.py", f"""\
import asyncio

from {a}.models.database import close_db, init_db
from {a}.services.infra.execution.registry import dispatch_task


def run_task(task_type: str, payload: dict) -> None:
    asyncio.run(_run(task_type, payload))


async def _run(task_type: str, payload: dict) -> None:
    await init_db()
    try:
        await dispatch_task(task_type, payload)
    finally:
        await close_db()
""", force=force)

    _write(root / a / "services" / "tasks" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "record_view_start.py", """\
async def handle_record_view_start(payload: dict) -> None:
    # Implemented in Phase 7.
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "presence" / "record_view_end.py", """\
async def handle_record_view_end(payload: dict) -> None:
    # Implemented in Phase 7.
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "notifications" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "notifications" / "create_notifications.py", """\
async def handle_create_notifications(payload: dict) -> None:
    # Implemented in Phase 8.
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "notifications" / "send_push_notification.py", """\
async def handle_send_push_notification(payload: dict) -> None:
    # Implemented in Phase 8.
    return None
""", force=force)
    _write(root / a / "services" / "tasks" / "signals" / "__init__.py", "", force=force)
    _write(root / a / "services" / "tasks" / "signals" / "push_user_signal.py", f"""\
from {a}.sockets.manager import manager


async def handle_push_user_signal(payload: dict) -> None:
    user_id = payload.get("user_id")
    signal = payload.get("signal")
    if user_id and signal:
        await manager.send_to_user(user_id, "user:signal", {{"signal": signal}})
""", force=force)
    _write(root / "worker.py", f"""\
from rq import Worker

from {a}.config import settings
from {a}.services.infra.redis import get_redis_client


if __name__ == "__main__":
    worker = Worker(["default"], connection=get_redis_client(settings.redis_url))
    worker.work()
""", force=force)
