# 13 — WebSocket & Real-Time Contract

## Architecture overview

Real-time is handled by native FastAPI WebSocket endpoints. There is no Socket.IO dependency.

```
Client
  │  ws://host/ws?token=<access_token>
  ▼
FastAPI WebSocket endpoint
  │  decode JWT → user_id, workspace_id
  │  join user room + workspace room
  ▼
ConnectionManager (per-process, in-memory)
  │  push_to_user / push_to_workspace
  ▼
Connected WebSocket clients

--- cross-process messaging ---

Worker process
  │  redis.publish("channel:sockets", payload)
  ▼
Redis pub/sub
  ▼
FastAPI app (each worker subscribes)
  │  receives message → routes to ConnectionManager
  ▼
Connected WebSocket clients (on this worker)
```

Each uvicorn worker maintains its own `ConnectionManager`. Workers that do not hold the target connection simply skip the message. Redis pub/sub ensures delivery across all workers.

---

## ConnectionMeta

Each WebSocket connection is associated with a `ConnectionMeta` record that holds identity and room membership for that specific socket:

```python
# sockets/manager.py
from dataclasses import dataclass, field


@dataclass
class ConnectionMeta:
    user_id:          str
    username:         str
    workspace_id:     str
    conversation_ids: set[str]              = field(default_factory=set)
    entity_views:     set[tuple[str, str]]  = field(default_factory=set)
```

`conversation_ids` tracks active conversation room memberships (for real-time message delivery). `entity_views` tracks every entity the user is currently viewing as `(entity_type, entity_client_id)` tuples — used to clean up Redis presence on disconnect regardless of entity type.

---

## ConnectionManager

```python
# sockets/manager.py
import asyncio
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)


class ConnectionManager:

    def __init__(self) -> None:
        self._connections:            dict[WebSocket, ConnectionMeta] = {}
        self._user_connections:       dict[str, list[WebSocket]]      = {}
        self._workspace_connections:  dict[str, list[WebSocket]]      = {}
        self._conversation_connections: dict[str, list[WebSocket]]    = {}

    async def connect(
        self,
        ws:           WebSocket,
        user_id:      str,
        username:     str,
        workspace_id: str,
    ) -> None:
        await ws.accept()
        meta = ConnectionMeta(user_id=user_id, username=username, workspace_id=workspace_id)
        self._connections[ws] = meta
        self._user_connections.setdefault(user_id, []).append(ws)
        self._workspace_connections.setdefault(workspace_id, []).append(ws)
        logger.debug("ws:connect user=%s workspace=%s", user_id, workspace_id)

    def disconnect(self, ws: WebSocket) -> None:
        meta = self._connections.pop(ws, None)
        if meta is None:
            return
        self._user_connections.get(meta.user_id, []).remove(ws)
        self._workspace_connections.get(meta.workspace_id, []).remove(ws)
        for cid in list(meta.conversation_ids):
            self._leave_conversation_room(ws, cid)
        logger.debug("ws:disconnect user=%s workspace=%s", meta.user_id, meta.workspace_id)

    def join_conversation(self, ws: WebSocket, conversation_id: str) -> ConnectionMeta | None:
        meta = self._connections.get(ws)
        if meta is None:
            return None
        meta.conversation_ids.add(conversation_id)
        self._conversation_connections.setdefault(conversation_id, []).append(ws)
        return meta

    def leave_conversation(self, ws: WebSocket, conversation_id: str) -> ConnectionMeta | None:
        meta = self._connections.get(ws)
        if meta is None:
            return None
        meta.conversation_ids.discard(conversation_id)
        self._leave_conversation_room(ws, conversation_id)
        return meta

    def _leave_conversation_room(self, ws: WebSocket, conversation_id: str) -> None:
        room = self._conversation_connections.get(conversation_id, [])
        if ws in room:
            room.remove(ws)

    def viewers(self, conversation_id: str) -> list[ConnectionMeta]:
        """Return meta for every connection currently in a conversation room."""
        return [
            self._connections[ws]
            for ws in self._conversation_connections.get(conversation_id, [])
            if ws in self._connections
        ]

    async def push_to_user(self, user_id: str, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        for ws in list(self._user_connections.get(user_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                pass   # client disconnected mid-send — cleaned up on next receive

    async def push_to_workspace(self, workspace_id: str, event: str, data: dict) -> None:
        payload = {"event": event, "data": data}
        for ws in list(self._workspace_connections.get(workspace_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                pass

    async def push_to_conversation(
        self, conversation_id: str, event: str, data: dict
    ) -> None:
        payload = {"event": event, "data": data}
        for ws in list(self._conversation_connections.get(conversation_id, [])):
            try:
                await ws.send_json(payload)
            except Exception:
                pass


manager = ConnectionManager()
```

`manager` is a module-level singleton — one per process. Import it anywhere inside the application process that needs to push to connected clients.

---

## WebSocket endpoint

```python
# sockets/handlers.py
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from jose import jwt, JWTError
from my_app.config import settings
from my_app.sockets.manager import manager
from my_app.services.infra.presence import presence

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token:     str = Query(...),
):
    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except JWTError:
        await websocket.close(code=4001, reason="Invalid token.")
        return

    user_id      = claims.get("user_id")
    username     = claims.get("username")
    workspace_id = claims.get("workspace_id")

    if not user_id or not workspace_id:
        await websocket.close(code=4001, reason="Missing identity claims.")
        return

    await manager.connect(websocket, user_id, username or "", workspace_id)

    try:
        while True:
            raw = await websocket.receive_text()
            await _handle_client_message(websocket, raw)
    except WebSocketDisconnect:
        _cleanup_presence(websocket)
        manager.disconnect(websocket)


def _cleanup_presence(ws: WebSocket) -> None:
    meta = manager._connections.get(ws)
    if meta is None:
        return
    for entity_type, entity_client_id in meta.entity_views:
        presence.mark_left(entity_type, entity_client_id, meta.user_id)


async def _handle_client_message(ws: WebSocket, raw: str) -> None:
    try:
        msg = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return

    action = msg.get("action")
    if not action:
        return

    if action == "view_entity":
        await _handle_view_entity(ws, msg)
    elif action == "leave_entity":
        await _handle_leave_entity(ws, msg)
    elif action == "typing":
        await _handle_typing(ws, msg)


async def _handle_view_entity(ws: WebSocket, msg: dict) -> None:
    entity_type      = msg.get("entity_type")
    entity_client_id = msg.get("entity_client_id")
    if not entity_type or not entity_client_id:
        return

    meta = manager._connections.get(ws)
    if meta is None:
        return

    meta.entity_views.add((entity_type, entity_client_id))
    presence.mark_viewing(entity_type, entity_client_id, meta.user_id)

    # Conversation rooms also need real-time delivery and presence broadcasts
    if entity_type == "conversation":
        manager.join_conversation(ws, entity_client_id)
        await manager.push_to_conversation(
            entity_client_id,
            "conversation:user-viewing",
            {"user_id": meta.user_id, "username": meta.username},
        )
        viewers = [
            {"user_id": m.user_id, "username": m.username}
            for m in manager.viewers(entity_client_id)
        ]
        await ws.send_json({"event": "conversation:viewer-list", "data": {"viewers": viewers}})


async def _handle_leave_entity(ws: WebSocket, msg: dict) -> None:
    entity_type      = msg.get("entity_type")
    entity_client_id = msg.get("entity_client_id")
    if not entity_type or not entity_client_id:
        return

    meta = manager._connections.get(ws)
    if meta is None:
        return

    meta.entity_views.discard((entity_type, entity_client_id))
    presence.mark_left(entity_type, entity_client_id, meta.user_id)

    if entity_type == "conversation":
        manager.leave_conversation(ws, entity_client_id)
        await manager.push_to_conversation(
            entity_client_id,
            "conversation:user-stopped-viewing",
            {"user_id": meta.user_id, "username": meta.username},
        )


async def _handle_typing(ws: WebSocket, msg: dict) -> None:
    conversation_id = msg.get("conversation_id")
    if not conversation_id:
        return
    meta = manager._connections.get(ws)
    if meta is None:
        return
    is_typing = bool(msg.get("is_typing"))
    event     = "conversation:user-typing" if is_typing else "conversation:user-stopped-typing"
    await manager.push_to_conversation(
        conversation_id,
        event,
        {"user_id": meta.user_id, "username": meta.username},
    )
```

**Rules:**
- The access token is passed as a query parameter `?token=<jwt>`. This is the only case where a token is accepted via query string (HTTP headers are not available during the WebSocket upgrade handshake).
- The token is validated on every new connection. Blocklist check is not run at connect time — connections are short-lived enough that this is acceptable. The client receives `session_invalidated` via the user room when the token is blocklisted on logout.
- Presence events (viewing, typing) are driven by client messages and handled directly in the WebSocket handler — they are **not** routed through the event bus, because they are triggered by connection state, not DB commands.
- `view_entity` writes to both Redis presence (all entity types) and optionally the in-process conversation room (conversations only). This split is intentional: Redis presence is cross-process and used for notification exclusion; the in-process room is used for real-time event delivery.
- `_cleanup_presence` is called before `manager.disconnect()` on `WebSocketDisconnect` so that Redis keys are cleared even when the client disconnects without sending `leave_entity`. The Redis TTL is the final fallback if the process crashes before this runs.
- `manager._connections` is accessed directly within `sockets/` module functions — intentional internal access within the same layer.

---

## Connection limits

`ConnectionManager` enforces per-user and per-process connection limits. Without limits, a buggy client or browser session leak can exhaust server memory.

Add limit enforcement inside `connect()`:

```python
# sockets/manager.py
MAX_CONNECTIONS_PER_PROCESS = 5000   # total WebSocket connections this worker holds
MAX_CONNECTIONS_PER_USER    = 10     # tabs / devices per authenticated user


class ConnectionManager:

    async def connect(self, ws: WebSocket, user_id: str, username: str, workspace_id: str) -> None:
        if len(self._connections) >= MAX_CONNECTIONS_PER_PROCESS:
            await ws.close(code=4008, reason="Server connection limit reached.")
            return

        user_sockets = self._user_connections.get(user_id, [])
        if len(user_sockets) >= MAX_CONNECTIONS_PER_USER:
            # Evict the oldest connection for this user before accepting the new one.
            oldest = user_sockets[0]
            await oldest.close(code=4009, reason="Connection replaced by newer session.")
            self.disconnect(oldest)

        await ws.accept()
        # ... rest of connect logic
```

**Rules:**
- `MAX_CONNECTIONS_PER_PROCESS` is read from `settings.ws_max_connections_per_process` so it can be tuned via env var without a code deploy.
- Do not reject the new connection when the per-user limit is hit — evict the oldest instead. Rejecting causes the client to loop reconnecting. Evicting lets the newest session win, which is almost always what the user wants.
- Log evictions at `INFO` level: `ws:evict user=%s connections_before=%d`.

---

## Idle disconnect / keepalive

Long-lived WebSocket connections that go idle (browser tab in background, network hiccup) consume memory indefinitely. Implement server-side ping and close idle connections that do not respond.

The server sends a `ping` event every 30 seconds. The client must respond with a `pong` message within 10 seconds. If no `pong` is received, the connection is closed.

```python
# sockets/handlers.py — inside the websocket_endpoint receive loop

PING_INTERVAL_SECONDS = 30
PONG_TIMEOUT_SECONDS  = 10


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: str = Query(...)):
    # ... auth and connect ...

    last_pong = asyncio.get_event_loop().time()

    async def keepalive():
        nonlocal last_pong
        while True:
            await asyncio.sleep(PING_INTERVAL_SECONDS)
            await websocket.send_json({"event": "ping"})
            await asyncio.sleep(PONG_TIMEOUT_SECONDS)
            if asyncio.get_event_loop().time() - last_pong > PING_INTERVAL_SECONDS + PONG_TIMEOUT_SECONDS:
                await websocket.close(code=4010, reason="Keepalive timeout.")
                return

    keepalive_task = asyncio.create_task(keepalive())
    try:
        while True:
            raw = await websocket.receive_text()
            msg = json.loads(raw)
            if msg.get("action") == "pong":
                last_pong = asyncio.get_event_loop().time()
            else:
                await _handle_client_message(websocket, raw)
    except WebSocketDisconnect:
        _cleanup_presence(websocket)
        manager.disconnect(websocket)
    finally:
        keepalive_task.cancel()
```

**Client-side contract:** The frontend must handle `ping` events and respond with `{"action": "pong"}` within `PONG_TIMEOUT_SECONDS`. Failure to respond closes the connection with code `4010`.

**Rules:**
- `PING_INTERVAL_SECONDS` and `PONG_TIMEOUT_SECONDS` come from settings — never hardcoded.
- The keepalive task is always cancelled in the `finally` block to prevent task leaks.
- Idle connections closed by the server must be reconnected by the client automatically. The client's reconnect logic should use exponential backoff to avoid a stampede of reconnects after a server restart.

---

## Redis pub/sub listener (cross-process push)

The lifespan starts a background task that subscribes to the `channel:sockets` Redis channel and routes incoming messages to the local `ConnectionManager`:

```python
# sockets/pubsub_listener.py
import asyncio
import json
import redis.asyncio as aioredis
from my_app.config import settings
from my_app.sockets.manager import manager
import logging

logger = logging.getLogger(__name__)


async def listen_for_socket_events() -> None:
    r      = aioredis.from_url(settings.redis_uri)
    pubsub = r.pubsub()
    await pubsub.subscribe("channel:sockets")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            payload = json.loads(message["data"])
            await _dispatch(payload)
        except Exception:
            logger.exception("socket pubsub dispatch error")


async def _dispatch(payload: dict) -> None:
    event_type = payload.get("event_type")

    if event_type == "user_signal":
        await manager.push_to_user(
            payload["user_id"],
            "user_signal",
            {"signal": payload["signal"]},
        )

    elif event_type == "workspace_refresh":
        await manager.push_to_workspace(
            payload["workspace_id"],
            payload["event_name"],
            payload["data"],
        )

    elif event_type == "workspace_batch":
        await manager.push_to_workspace(
            payload["workspace_id"],
            payload["event_name"],
            {"ids": payload["ids"]},
        )

    elif event_type == "workspace_signal":
        await manager.push_to_workspace(
            payload["workspace_id"],
            payload["event_name"],
            {},
        )

    elif event_type == "conversation_event":
        await manager.push_to_conversation(
            payload["conversation_id"],
            payload["event_name"],
            payload["data"],
        )
```

Started in the app lifespan:

```python
# my_app/__init__.py — inside lifespan
import asyncio


def _start_socket_pubsub(app: FastAPI) -> None:
    from my_app.sockets.pubsub_listener import listen_for_socket_events
    task = asyncio.create_task(listen_for_socket_events())
    app.state.socket_pubsub_task = task   # keep reference to cancel on shutdown
```

Cancelled in shutdown:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    _start_socket_pubsub(app)
    yield
    if task := getattr(app.state, "socket_pubsub_task", None):
        task.cancel()
    await close_db()
```

---

## Pushing events from workers

Workers (separate processes) publish to Redis. They do not hold WebSocket connections and do not import `manager` directly:

```python
# services/infra/jobs/handlers/user_signal.py
import json
import redis
from my_app.config import settings


def handle_user_signal(raw: dict, task_id: str) -> None:
    r = redis.from_url(settings.redis_uri)
    r.publish("channel:sockets", json.dumps({
        "event_type": "user_signal",
        "user_id":    raw["user_id"],
        "signal":     raw["signal"],
    }))
```

---

## Pushing events from the application (infra layer)

Application-side real-time pushes originate in the infra event layer, not in commands directly:

```python
# services/infra/events/realtime_push.py
import json
import redis
from my_app.config import settings


def push_workspace_refresh(workspace_id: str, event_name: str, data: dict) -> None:
    """One entity changed — push its serialized representation."""
    _publish({"event_type": "workspace_refresh", "workspace_id": workspace_id,
              "event_name": event_name, "data": data})


def push_workspace_batch(workspace_id: str, event_name: str, client_ids: list[str]) -> None:
    """Bulk operation — push the list of changed client_ids."""
    _publish({"event_type": "workspace_batch", "workspace_id": workspace_id,
              "event_name": event_name, "ids": client_ids})


def push_workspace_signal(workspace_id: str, event_name: str) -> None:
    """Broad signal — frontend invalidates the full entity cache."""
    _publish({"event_type": "workspace_signal", "workspace_id": workspace_id,
              "event_name": event_name})


def push_to_conversation(conversation_id: str, event_name: str, data: dict) -> None:
    """Push to all users currently viewing a specific conversation."""
    _publish({"event_type": "conversation_event", "conversation_id": conversation_id,
              "event_name": event_name, "data": data})


def _publish(payload: dict) -> None:
    r = redis.from_url(settings.redis_uri)
    r.publish("channel:sockets", json.dumps(payload))
```

**Rules:**
- Commands must not import `manager` or `push_*` functions directly. Real-time pushes go through event handlers in the infra layer.
- All pushes use named rooms (user or workspace). Never broadcast to all connected clients.
- Event names follow `<domain>:<action>` convention: `"record:updated"`, `"case:state-changed"`. The colon is intentional and must match the frontend's `ServerToClientEvents` type exactly.
- Payload must be a plain dict — never an ORM instance.
- Use `client_id` values in all payloads. Never alternate integer IDs.

---

## Single vs batch vs signal — when to use each

| Scenario | Function | Event name pattern | Payload |
|---|---|---|---|
| One entity changed (create, update, delete) | `push_workspace_refresh` | `record:updated` | `{"client_id": ..., ...}` |
| Bulk command changed 2–200 entities | `push_workspace_batch` | `record:batch-updated` | `{"ids": [client_id, ...]}` |
| Job touched 200+ entities | `push_workspace_signal` | `record:invalidate-all` | `{}` |
| Message created/edited in a conversation | `push_to_conversation` | `conversation:message-created` | `{"client_id": ..., ...}` |
| User-private event (logout, notification) | `push_to_user` via pub/sub | `user_signal` | `{"signal": "..."}` |

Never push 50 individual `record:updated` events for a bulk operation. Emit one `record:batch-updated` with all 50 `client_id`s.

---

## Room convention

| Room | Naming | Members | Joined by |
|---|---|---|---|
| User room | keyed by `user_id` | All connections for one user | Automatically on connect |
| Workspace room | keyed by `workspace_id` | All connections in one workspace | Automatically on connect |
| Conversation room | keyed by `conversation_id` | Users currently viewing a conversation | Client sends `join_conversation` message |

The user and workspace rooms are joined automatically on connect — no client message needed. Conversation rooms are opt-in via `view_entity` with `entity_type: "conversation"`. A user can be in multiple conversation rooms simultaneously (multiple open tabs).

**Redis presence** is a separate, cross-process layer that runs alongside room membership. Every `view_entity` writes `presence:{entity_type}:{entity_client_id}` → Redis SET of user_ids. Every `leave_entity` and disconnect removes the entry. This is the data source used by `CREATE_NOTIFICATIONS` to exclude users who are already viewing the entity. Room membership is in-process only (used for real-time push); Redis presence is cross-process (used for notification filtering).

## Client message protocol

Messages from client → server are JSON objects. The server silently ignores malformed or unknown messages.

| `action` | Required fields | Effect |
|---|---|---|
| `view_entity` | `entity_type`, `entity_client_id` | Writes Redis presence key; for `conversation`: also joins room, broadcasts `conversation:user-viewing`, sends `conversation:viewer-list` back to caller |
| `leave_entity` | `entity_type`, `entity_client_id` | Clears Redis presence key; for `conversation`: also leaves room, broadcasts `conversation:user-stopped-viewing` |
| `typing` | `conversation_id`, `is_typing` (bool) | Broadcasts `conversation:user-typing` or `conversation:user-stopped-typing` to conversation room |

`view_entity` is entity-agnostic — send it whenever the user opens any entity view (case, conversation, etc.). The server writes Redis presence for all entity types and only adds conversation-room membership for `conversation`. Presence events carry `{"user_id": "...", "username": "..."}` so the frontend can display names without a REST call.

---

## What socket handlers must NOT do

- Perform database writes directly (use a command via `run_service`)
- Return sensitive data without verifying the caller's workspace/identity
- Store connection state in module-level globals other than `manager`
- Process bidirectional messages unless the feature explicitly requires it
