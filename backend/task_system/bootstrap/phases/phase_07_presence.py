from pathlib import Path

import typer

from bootstrap.writer import write_file as _write


def _phase7(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 7 - Presence System ----------------------------------------")

    _write(root / a / "domain" / "presence" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "presence" / "enums.py", """\
from enum import StrEnum


class EntityType(StrEnum):
    CASE_LIST = "case_list"
    CASE = "case"
    CONVERSATION_LIST = "conversation_list"
    CONVERSATION = "conversation"
""", force=force)
    _write(root / a / "services" / "infra" / "presence" / "__init__.py", f"""\
from {a}.services.infra.presence.presence import get_viewers, mark_left, mark_viewing

__all__ = ["get_viewers", "mark_left", "mark_viewing"]
""", force=force)
    _write(root / a / "services" / "infra" / "presence" / "presence.py", f"""\
from {a}.config import settings
from {a}.services.infra.redis import get_redis_client, make_key

PRESENCE_TTL_SECONDS = 90


def _key(entity_type: str, entity_client_id: str) -> str:
    return make_key("presence", entity_type, entity_client_id)


def mark_viewing(entity_type: str, entity_client_id: str, user_id: str) -> None:
    r = get_redis_client(settings.redis_url)
    key = _key(entity_type, entity_client_id)
    r.sadd(key, user_id)
    r.expire(key, PRESENCE_TTL_SECONDS)


def mark_left(entity_type: str, entity_client_id: str, user_id: str) -> None:
    r = get_redis_client(settings.redis_url)
    r.srem(_key(entity_type, entity_client_id), user_id)


def get_viewers(entity_type: str, entity_client_id: str) -> set[str]:
    r = get_redis_client(settings.redis_url)
    return set(r.smembers(_key(entity_type, entity_client_id)))
""", force=force)

    _write(root / a / "services" / "tasks" / "presence" / "record_view_start.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import select

from {a}.models.tables.users.user import User
from {a}.models.tables.users.user_app_view_record import UserAppViewRecord
from {a}.services.infra.execution.db import task_db_session


async def handle_record_view_start(payload: dict) -> None:
    user_client_id = payload.get("user_id")
    entity_type = payload.get("entity_type")
    entity_client_id = payload.get("entity_client_id")
    if not user_client_id or not entity_type:
        return
    async with task_db_session() as session:
        user = (await session.execute(select(User).where(User.client_id == user_client_id))).scalar_one_or_none()
        if user is None:
            return
        record = UserAppViewRecord(
            user_id=user.client_id,
            entity_type=entity_type,
            entity_client_id=entity_client_id,
            started_at=datetime.now(timezone.utc),
        )
        session.add(record)
        await session.flush()
        user.last_app_view_record_id = record.client_id
        await session.commit()
""", force=True)
    _write(root / a / "services" / "tasks" / "presence" / "record_view_end.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import desc, select

from {a}.models.tables.users.user import User
from {a}.models.tables.users.user_app_view_record import UserAppViewRecord
from {a}.services.infra.execution.db import task_db_session


async def handle_record_view_end(payload: dict) -> None:
    user_client_id = payload.get("user_id")
    entity_type = payload.get("entity_type")
    entity_client_id = payload.get("entity_client_id")
    if not user_client_id or not entity_type:
        return
    async with task_db_session() as session:
        user = (await session.execute(select(User).where(User.client_id == user_client_id))).scalar_one_or_none()
        if user is None:
            return
        result = await session.execute(
            select(UserAppViewRecord)
            .where(
                UserAppViewRecord.user_id == user.client_id,
                UserAppViewRecord.entity_type == entity_type,
                UserAppViewRecord.entity_client_id == entity_client_id,
                UserAppViewRecord.ended_at.is_(None),
            )
            .order_by(desc(UserAppViewRecord.started_at))
            .limit(1)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return
        record.ended_at = datetime.now(timezone.utc)
        await session.commit()
""", force=True)

    _write(root / a / "sockets" / "handlers.py", f"""\
import jwt

from {a}.config import settings
from {a}.domain.execution.enums import TaskType
from {a}.domain.presence.enums import EntityType
from {a}.services.infra.execution.task_factory import create_instant_task
from {a}.services.infra.presence import mark_left, mark_viewing
from {a}.sockets.connection_meta import ConnectionMeta
from {a}.sockets.manager import manager


async def _handle_connect(sid: str, environ: dict, auth: dict | None = None):
    token = (auth or {{}}).get("token") or _query_token(environ)
    if not token:
        return False
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return False
    await manager.connect(
        sid,
        ConnectionMeta(
            user_id=claims.get("user_id", ""),
            workspace_id=claims.get("workspace_id", ""),
            username=claims.get("username", ""),
        ),
    )
    return True


async def _handle_disconnect(sid: str):
    meta = await manager.disconnect(sid)
    if meta:
        _cleanup_presence(meta)


async def _handle_view_entity(sid: str, data: dict):
    meta = manager.get(sid)
    if not meta:
        return
    try:
        entity_type = EntityType(str(data.get("entity_type", "")))
    except ValueError:
        return
    entity_client_id = str(data.get("entity_client_id", ""))
    if not entity_client_id:
        return
    mark_viewing(entity_type.value, entity_client_id, meta.user_id)
    meta.entity_views.add((entity_type.value, entity_client_id))
    create_instant_task(TaskType.RECORD_VIEW_START, {{"user_id": meta.user_id, "entity_type": entity_type.value, "entity_client_id": entity_client_id}})
    if entity_type == EntityType.CONVERSATION:
        await manager.join_conversation(sid, entity_client_id)


async def _handle_leave_entity(sid: str, data: dict):
    meta = manager.get(sid)
    if not meta:
        return
    try:
        entity_type = EntityType(str(data.get("entity_type", "")))
    except ValueError:
        return
    entity_client_id = str(data.get("entity_client_id", ""))
    if not entity_client_id:
        return
    mark_left(entity_type.value, entity_client_id, meta.user_id)
    meta.entity_views.discard((entity_type.value, entity_client_id))
    create_instant_task(TaskType.RECORD_VIEW_END, {{"user_id": meta.user_id, "entity_type": entity_type.value, "entity_client_id": entity_client_id}})
    if entity_type == EntityType.CONVERSATION:
        await manager.leave_conversation(sid, entity_client_id)


def _cleanup_presence(meta: ConnectionMeta) -> None:
    for entity_type, entity_client_id in list(meta.entity_views):
        mark_left(entity_type, entity_client_id, meta.user_id)
        create_instant_task(TaskType.RECORD_VIEW_END, {{"user_id": meta.user_id, "entity_type": entity_type, "entity_client_id": entity_client_id}})


def _query_token(environ: dict) -> str | None:
    query = environ.get("QUERY_STRING", "")
    for part in query.split("&"):
        if part.startswith("token="):
            return part.removeprefix("token=")
    return None
""", force=True)
