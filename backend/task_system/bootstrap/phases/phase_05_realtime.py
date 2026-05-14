from pathlib import Path

import typer

from bootstrap.writer import replace_once, touch_file as _touch, write_file as _write


def _phase5(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 5 - Real-Time Infrastructure -------------------------------")

    # ── Redis client ─────────────────────────────────────────────────────────
    _write(root / a / "services" / "infra" / "redis" / "__init__.py", f"""\
from {a}.services.infra.redis.client import assert_redis_available, get_redis_client
from {a}.services.infra.redis.keys import make_key

__all__ = ["assert_redis_available", "get_redis_client", "make_key"]
""", force=force)
    _write(root / a / "services" / "infra" / "redis" / "client.py", f"""\
import redis


def get_redis_client(redis_uri: str | None):
    if not redis_uri:
        raise RuntimeError("Redis URI is not configured.")
    return redis.from_url(redis_uri, decode_responses=True)


def assert_redis_available(redis_uri: str | None) -> None:
    get_redis_client(redis_uri).ping()
""", force=force)
    _write(root / a / "services" / "infra" / "redis" / "keys.py", f"""\
from {a}.config import settings


def make_key(namespace: str, *parts: object) -> str:
    clean = [str(p).strip(":") for p in parts if p is not None and str(p) != ""]
    return ":".join([settings.redis_key_prefix, namespace, *clean])
""", force=force)

    # ── Event bus — contract-compliant (11_infra_events.md) ──────────────────
    _write(root / a / "services" / "infra" / "events" / "__init__.py", f"""\
from {a}.services.infra.events.event_bus import dispatch, register

__all__ = ["dispatch", "register"]
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "domain_event.py", """\
from dataclasses import dataclass, field


@dataclass(kw_only=True)
class Event:
    \"\"\"Base event. All domain events inherit from this.\"\"\"
    event_name: str
    client_id:  str
    extra:      dict = field(default_factory=dict)


@dataclass(kw_only=True)
class WorkspaceEvent(Event):
    \"\"\"Broadcast to all users connected to a workspace room.\"\"\"
    workspace_id: str


@dataclass(kw_only=True)
class UserEvent(Event):
    \"\"\"Push to a specific user's room only.\"\"\"
    user_id: str


@dataclass(kw_only=True)
class ConversationRoomEvent(Event):
    \"\"\"Broadcast to all users currently viewing a specific conversation.\"\"\"
    conversation_id: str
    workspace_id:    str
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "build_event.py", f"""\
from {a}.services.infra.events.domain_event import (
    ConversationRoomEvent,
    UserEvent,
    WorkspaceEvent,
)


def build_workspace_event(
    entity,
    event_name: str,
    *,
    workspace_id: str | None = None,
    extra: dict | None = None,
) -> WorkspaceEvent:
    return WorkspaceEvent(
        event_name=event_name,
        client_id=entity.client_id,
        workspace_id=workspace_id or getattr(entity, "workspace_id", None),
        extra=extra or {{}},
    )


def build_user_event(
    user_id:    str,
    event_name: str,
    client_id:  str,
    extra: dict | None = None,
) -> UserEvent:
    return UserEvent(
        event_name=event_name,
        client_id=client_id,
        user_id=user_id,
        extra=extra or {{}},
    )


def build_conversation_event(
    entity,
    event_name:      str,
    *,
    conversation_id: str,
    workspace_id:    str,
    extra: dict | None = None,
) -> ConversationRoomEvent:
    return ConversationRoomEvent(
        event_name=event_name,
        client_id=entity.client_id,
        conversation_id=conversation_id,
        workspace_id=workspace_id,
        extra=extra or {{}},
    )
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "event_bus.py", """\
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from .domain_event import Event

logger = logging.getLogger(__name__)

_handlers: list[Callable[[Event], Awaitable[None]]] = []


def register(handler: Callable[[Event], Awaitable[None]]) -> None:
    \"\"\"Register an async handler. Call during application startup only.\"\"\"
    _handlers.append(handler)


async def dispatch(events: list[Event]) -> None:
    \"\"\"Call every registered handler for each event after a transaction commits.
    A failing handler is logged and skipped so one bad handler cannot block others.
    \"\"\"
    for event in events:
        for handler in _handlers:
            try:
                await handler(event)
            except Exception:
                logger.exception(
                    "event handler failed | event=%s handler=%s client_id=%s",
                    event.event_name,
                    handler.__name__,
                    event.client_id,
                )
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "realtime_push.py", f"""\
from {a}.sockets.manager import manager


async def push_workspace_refresh(workspace_id: str, event_name: str, payload: dict) -> None:
    await manager.broadcast_to_room(manager.workspace_room(workspace_id), event_name, payload)


async def push_workspace_batch(workspace_id: str, event_name: str, ids: list) -> None:
    await manager.broadcast_to_room(
        manager.workspace_room(workspace_id), event_name, {{"ids": ids}}
    )


async def push_to_conversation(conversation_id: str, event_name: str, payload: dict) -> None:
    await manager.broadcast_to_room(
        manager.conversation_room(conversation_id), event_name, payload
    )


async def push_to_user(user_id: str, event_name: str, payload: dict) -> None:
    await manager.send_to_user(user_id, event_name, payload)
""", force=force)

    # ── Event handlers ────────────────────────────────────────────────────────
    _touch(root / a / "services" / "infra" / "events" / "handlers" / "__init__.py", force=force)

    _write(root / a / "services" / "infra" / "events" / "handlers" / "socket_handler.py", f"""\
from {a}.services.infra.events.domain_event import (
    ConversationRoomEvent,
    UserEvent,
    WorkspaceEvent,
)
from {a}.services.infra.events.realtime_push import (
    push_to_conversation,
    push_to_user,
    push_workspace_batch,
    push_workspace_refresh,
)


async def handle(event) -> None:
    \"\"\"Route domain events to the correct socket room.
    ConversationRoomEvent is checked first (most specific).
    \"\"\"
    if isinstance(event, ConversationRoomEvent):
        await push_to_conversation(
            event.conversation_id,
            event.event_name,
            {{"client_id": event.client_id, **event.extra}},
        )
    elif isinstance(event, WorkspaceEvent):
        if "ids" in event.extra:
            await push_workspace_batch(event.workspace_id, event.event_name, event.extra["ids"])
        else:
            await push_workspace_refresh(
                event.workspace_id,
                event.event_name,
                {{"client_id": event.client_id, **event.extra}},
            )
    elif isinstance(event, UserEvent):
        await push_to_user(
            event.user_id,
            event.event_name,
            {{"client_id": event.client_id, **event.extra}},
        )
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "handlers" / "audit_handler.py", f"""\
import logging
from datetime import datetime, timezone

from {a}.services.infra.audit.audited_events import get_audited_events
from {a}.services.infra.events.domain_event import Event

logger = logging.getLogger(__name__)


async def handle(event: Event) -> None:
    if event.event_name not in get_audited_events():
        return

    workspace_id = getattr(event, "workspace_id", event.extra.get("workspace_id"))
    if not workspace_id:
        logger.warning("audit_handler: no workspace_id on event %s — skipped", event.event_name)
        return

    try:
        from {a}.models.database import get_db_session
        from {a}.services.infra.audit.write_audit import write_audit_from_event

        async for session in get_db_session():
            await write_audit_from_event(
                session=session,
                event_name=event.event_name,
                workspace_id=workspace_id,
                resource_client_id=event.client_id or None,
                detail=event.extra,
                occurred_at=datetime.now(timezone.utc),
            )
            await session.commit()
    except Exception:
        logger.exception(
            "audit_handler: failed to write audit entry | event=%s client_id=%s",
            event.event_name,
            event.client_id,
        )
""", force=force)

    _write(root / a / "services" / "infra" / "events" / "handlers" / "webhook_handler.py", f"""\
import logging

from {a}.services.infra.events.domain_event import WorkspaceEvent

logger = logging.getLogger(__name__)

# Populate with webhook-eligible event names in local extensions.
# e.g. _WEBHOOK_EVENTS = {{"invoice:updated", "case:state-changed"}}
_WEBHOOK_EVENTS: set[str] = set()


async def handle(event) -> None:
    \"\"\"Enqueue a durable webhook delivery task — never calls external APIs inline.\"\"\"
    if not _WEBHOOK_EVENTS:
        return
    if not isinstance(event, WorkspaceEvent):
        return
    if event.event_name not in _WEBHOOK_EVENTS:
        return

    try:
        from {a}.domain.execution.enums import EventTaskOriginSourceEnum, TaskType
        from {a}.models.database import get_db_session
        from {a}.services.infra.execution.task_factory import create_execution_task

        async for session in get_db_session():
            await create_execution_task(
                session=session,
                task_type=TaskType.DELIVER_WEBHOOK,
                payload={{
                    "event_name":   event.event_name,
                    "client_id":    event.client_id,
                    "workspace_id": event.workspace_id,
                    "extra":        event.extra,
                }},
                origin_source=EventTaskOriginSourceEnum.INSTANT,
            )
            await session.commit()
    except Exception:
        logger.exception(
            "webhook_handler: failed to enqueue | event=%s workspace=%s",
            event.event_name,
            event.workspace_id,
        )
""", force=force)

    # ── Sockets ───────────────────────────────────────────────────────────────
    _write(root / a / "sockets" / "__init__.py", f"""\
import socketio

from {a}.config import settings

sio = socketio.AsyncServer(
    async_mode="asgi",
    cors_allowed_origins=settings.frontend_origins,
)
socket_app = None
""", force=True)
    _write(root / a / "sockets" / "connection_meta.py", """\
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ConnectionMeta:
    user_id: str
    workspace_id: str
    username: str
    connected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    entity_views: set[tuple[str, str]] = field(default_factory=set)
""", force=force)
    _write(root / a / "sockets" / "manager.py", f"""\
from {a}.sockets import sio
from {a}.sockets.connection_meta import ConnectionMeta


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, ConnectionMeta] = {{}}

    async def connect(self, sid: str, meta: ConnectionMeta) -> None:
        self._connections[sid] = meta
        await sio.enter_room(sid, self.user_room(meta.user_id))
        await sio.enter_room(sid, self.workspace_room(meta.workspace_id))

    async def disconnect(self, sid: str) -> ConnectionMeta | None:
        meta = self._connections.pop(sid, None)
        if meta:
            await sio.leave_room(sid, self.user_room(meta.user_id))
            await sio.leave_room(sid, self.workspace_room(meta.workspace_id))
        return meta

    async def join_conversation(self, sid: str, conversation_client_id: str) -> None:
        await sio.enter_room(sid, self.conversation_room(conversation_client_id))

    async def leave_conversation(self, sid: str, conversation_client_id: str) -> None:
        await sio.leave_room(sid, self.conversation_room(conversation_client_id))

    async def send_to_user(self, user_id: str, event: str, payload: dict) -> None:
        await sio.emit(event, payload, room=self.user_room(user_id))

    async def broadcast_to_room(self, room: str, event: str, payload: dict) -> None:
        await sio.emit(event, payload, room=room)

    def get(self, sid: str) -> ConnectionMeta | None:
        return self._connections.get(sid)

    @staticmethod
    def user_room(user_id: str) -> str:
        return f"user:{{user_id}}"

    @staticmethod
    def workspace_room(workspace_id: str) -> str:
        return f"workspace:{{workspace_id}}"

    @staticmethod
    def conversation_room(conversation_client_id: str) -> str:
        return f"conversation:{{conversation_client_id}}"


manager = ConnectionManager()
""", force=force)
    _write(root / a / "sockets" / "handlers.py", f"""\
import jwt

from {a}.config import settings
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
    await manager.disconnect(sid)


async def _handle_view_entity(sid: str, data: dict):
    meta = manager.get(sid)
    if meta:
        entity_type = str(data.get("entity_type", ""))
        entity_client_id = str(data.get("entity_client_id", ""))
        if entity_type and entity_client_id:
            meta.entity_views.add((entity_type, entity_client_id))


async def _handle_leave_entity(sid: str, data: dict):
    meta = manager.get(sid)
    if meta:
        meta.entity_views.discard((str(data.get("entity_type", "")), str(data.get("entity_client_id", ""))))


def _query_token(environ: dict) -> str | None:
    query = environ.get("QUERY_STRING", "")
    for part in query.split("&"):
        if part.startswith("token="):
            return part.removeprefix("token=")
    return None
""", force=force)
    _write(root / a / "sockets" / "register.py", f"""\
from {a}.sockets import sio
from {a}.sockets.handlers import (
    _handle_connect,
    _handle_disconnect,
    _handle_leave_entity,
    _handle_view_entity,
)


def register_socket_handlers() -> None:
    sio.on("connect", handler=_handle_connect)
    sio.on("disconnect", handler=_handle_disconnect)
    sio.on("view_entity", handler=_handle_view_entity)
    sio.on("leave_entity", handler=_handle_leave_entity)
""", force=force)
    _write(root / a / "asgi.py", f"""\
from {a} import create_app
from {a} import sockets as sockets_module

create_app()
app = sockets_module.socket_app
""", force=force)

    # ── Patch app factory — socket wiring + event handler registration ────────
    replace_once(
        root / a / "__init__.py",
        "def create_app() -> FastAPI:\n    app = FastAPI(lifespan=lifespan)\n",
        "def create_app() -> FastAPI:\n    app = FastAPI(lifespan=lifespan)\n    from "
        f"{a}.sockets.register import register_socket_handlers\n    register_socket_handlers()\n",
    )
    replace_once(
        root / "run.py",
        f'        "{a}:create_app",\n        factory=True,',
        f'        "{a}.asgi:app",',
    )
    replace_once(
        root / a / "__init__.py",
        "    _register_routers(app)\n    _validate_config()\n    return app\n",
        f"    _register_routers(app)\n    _validate_config()\n\n    import socketio\n    from {a}.sockets import sio\n    import {a}.sockets as sockets_module\n    sockets_module.socket_app = socketio.ASGIApp(sio, other_asgi_app=app)\n    return app\n",
    )
    # Register event handlers during lifespan startup
    replace_once(
        root / a / "__init__.py",
        "    yield\n    await close_db()\n",
        "    _register_event_handlers()\n    yield\n    await close_db()\n",
    )
    replace_once(
        root / a / "__init__.py",
        "def _register_routers(app: FastAPI) -> None:\n",
        f"def _register_event_handlers() -> None:\n    from {a}.services.infra.events import register\n    from {a}.services.infra.events.handlers.socket_handler import handle as socket_handle\n    from {a}.services.infra.events.handlers.audit_handler import handle as audit_handle\n    from {a}.services.infra.events.handlers.webhook_handler import handle as webhook_handle\n    register(socket_handle)\n    register(audit_handle)\n    register(webhook_handle)\n\n\ndef _register_routers(app: FastAPI) -> None:\n",
    )
