# 13 — Socket.IO & Real-Time Contract

## Setup

A single `socketio` instance is created in `socketio_instance.py` and initialized by the app factory:

```python
# socketio_instance.py
from flask_socketio import SocketIO

socketio = SocketIO(async_mode="eventlet")
```

```python
# __init__.py — inside create_app()
def _initialize_socketio(app: Flask, frontend_origins: list[str]) -> None:
    redis_uri = app.config.get("REDIS_URI", "")
    kwargs = {"cors_allowed_origins": frontend_origins}

    if redis_uri:
        try:
            assert_redis_available(redis_uri, decode_responses=False)
            socketio.init_app(
                app,
                **kwargs,
                message_queue=redis_uri,
                channel="my-app-socketio",
            )
        except Exception as exc:
            app.logger.warning("Socket.IO Redis fallback: %s", exc)
            socketio.init_app(app, **kwargs)
    else:
        socketio.init_app(app, **kwargs)
```

In production, Socket.IO uses Redis as the message queue so that multiple application processes (gunicorn workers) can broadcast to all connected clients. In development, in-process mode is acceptable.

---

## Handler registration

All socket event handlers are registered in `sockets/register.py`:

```python
# sockets/register.py
from .notifications import register_notification_handlers
from .signaling import register_signaling_handlers


def register_socket_handlers() -> None:
    register_notification_handlers()
    register_signaling_handlers()
```

Called from `create_app()` after all blueprints are registered:

```python
from my_app.sockets.register import register_socket_handlers
register_socket_handlers()
```

---

## Room convention

Clients join rooms scoped to their workspace. All workspace-scoped events are pushed to `"workspace_{workspace_id}"`:

```python
from flask_socketio import join_room

@socketio.on("join")
def on_join(data):
    workspace_id = data.get("workspace_id")
    join_room(f"workspace_{workspace_id}")
```

User-specific events (private notifications, personal alerts) use `"user_{user_id}"`.

---

## Pushing events from the application

Real-time pushes originate in event handlers (not in commands):

```python
# services/infra/events/realtime_refresh.py
from my_app.socketio_instance import socketio


def push_workspace_refresh(workspace_id: int, event_name: str, data: dict) -> None:
    """Single-entity push — one entity changed, one client_id in the payload."""
    socketio.emit(event_name, data, room=f"workspace_{workspace_id}", namespace="/")


def push_workspace_batch_event(workspace_id: int, event_name: str, client_ids: list[str]) -> None:
    """Batch push — multiple entities changed in one operation.
    The frontend receives one event with the full list of client_ids and handles
    per-ID cache invalidation via batchInvalidation()."""
    socketio.emit(event_name, {"ids": client_ids}, room=f"workspace_{workspace_id}", namespace="/")


def push_workspace_broadcast_signal(workspace_id: int, event_name: str) -> None:
    """Broad signal — operation touched too many entities to enumerate.
    The frontend invalidates the whole entity cache for the workspace."""
    socketio.emit(event_name, {"workspace_id": workspace_id}, room=f"workspace_{workspace_id}", namespace="/")


def push_user_refresh(user_id: int, event_name: str, data: dict) -> None:
    socketio.emit(event_name, data, room=f"user_{user_id}", namespace="/")
```

**Rules:**
- Commands must not import `socketio` directly. Real-time pushes go through event handlers.
- All pushes use named rooms, never broadcast to all clients.
- Event names follow `<domain>:<action>` convention: `"record:updated"`, `"resource:published"`.
  This matches the frontend's `ServerToClientEvents` type exactly — the colon is intentional and must be consistent across backend and frontend.
- Payload must be a plain dict — never an ORM instance.
- Use `client_id` values (not internal DB `id`) in all payloads — matches what the frontend uses as query keys and route params.

---

## Single vs batch vs broadcast — when to use each

| Scenario | Push function | Event name pattern | Payload |
|---|---|---|---|
| One entity changed (create, update, delete) | `push_workspace_refresh` | `record:updated` | `{"id": client_id, ...}` |
| Bulk command changed 2–200 entities | `push_workspace_batch_event` | `record:batch-updated` | `{"ids": [client_id, ...]}` |
| Job touched 200+ entities (nightly reconcile, mass import) | `push_workspace_broadcast_signal` | `record:invalidate-all` | `{"workspace_id": ...}` |

Never push 50 individual `record:updated` events for a bulk operation. Emit one `record:batch-updated` event with all 50 `client_id`s. The frontend's `batchInvalidation()` handles per-ID cache decisions without triggering 50 network requests.

---

## Notification store

Notifications that a client may have missed (e.g., disconnected at push time) are stored in Redis with a TTL. Clients request pending notifications on reconnect via a REST endpoint, not via a socket event:

```
GET /api/v1/notifications/pending/
```

This prevents socket-level callback hell and keeps the notification retrieval path auditable.

---

## What socket handlers must NOT do

- Perform database writes directly (use a command via `run_service`)
- Return sensitive data without verifying the caller's workspace/identity
- Use the default namespace for private workspace events (use authenticated rooms)
- Store connection state in module-level globals
