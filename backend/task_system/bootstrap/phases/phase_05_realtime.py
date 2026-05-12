from pathlib import Path

import typer

from bootstrap.writer import replace_once, touch_file as _touch, write_file as _write


def _phase5(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 5 - Real-Time Infrastructure -------------------------------")

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

    _write(root / a / "services" / "infra" / "events" / "__init__.py", f"""\
from {a}.services.infra.events.bus import EventBus

__all__ = ["EventBus"]
""", force=force)
    _write(root / a / "services" / "infra" / "events" / "bus.py", '''\
import logging

logger = logging.getLogger(__name__)


class EventBus:
    """Contract-correct event bus seam.

    Domain commands should call this after commit. Applications that need durable
    delivery should back this with an outbox table and worker.
    """

    async def emit(self, event_type: str, payload: dict) -> None:
        logger.info("event_emitted | event_type=%s payload_keys=%s", event_type, sorted(payload.keys()))

    async def batch_emit(self, event_type: str, payloads: list[dict]) -> None:
        for payload in payloads:
            await self.emit(event_type, payload)
''', force=force)
    for folder in ["builders", "handlers"]:
        _touch(root / a / "services" / "infra" / "events" / folder / "__init__.py", force=force)
    _write(root / a / "services" / "infra" / "events" / "registry" / "__init__.py", """\
from collections.abc import Awaitable, Callable

EventHandler = Callable[[dict], Awaitable[None]]
EVENT_REGISTRY: dict[str, EventHandler] = {}
""", force=force)

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
