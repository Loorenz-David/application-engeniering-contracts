# 11 — Infrastructure: Event Bus & Outbox Contract

## What the event system does

Commands raise domain events after committing to the database. These events drive side effects: sending notifications, pushing real-time updates, triggering analytics snapshots, or calling external integrations.

The event system decouples the command from its downstream effects. A command does not know which handlers will respond to its event.

---

## Architecture

```
Command
  │
  ├─ builds events (builders/)
  ├─ commits to DB (with db.session.begin())
  └─ emits events to event bus (emitters/)
                │
                ▼
          EventBus (Redis-backed outbox)
                │
          Dispatcher (background worker)
                │
        Handlers (one per side effect)
          ├─ record_notification.py
          ├─ record_analytics.py
          ├─ record_webhook.py
          └─ realtime_refresh.py
```

---

## Event structure

An event is a plain dict:

```python
{
    "event_type": "record:created",
    "schema_version": 1,
    "workspace_id": 7,
    "payload": {
        "record_id": 123,
        "client_id": "abc-def",
        # ... enough data for handlers to act without querying
    },
    "meta": {
        "triggered_by_user_id": 42,
        "timestamp": "2025-01-15T12:00:00Z",
    }
}
```

**Rules:**
- `event_type` follows `<domain>:<verb>` naming: `record:created`, `record:state-changed`, `resource:published`.
  The colon separator matches the frontend's `ServerToClientEvents` type — event types and socket event names must be identical so the realtime handler can pass the `event_type` directly to Socket.IO without translation.
- Events are self-contained. Handlers must not need to re-query the database to understand the event.
- Events are immutable after creation. Handlers must not modify event payloads.

---

## Event builders

Builders construct the event dict from ORM instances, after flush (IDs are available):

```python
# services/infra/events/builders/<domain>/record_events.py
from datetime import datetime, timezone


def build_record_created_event(record) -> dict:
    return {
        "event_type": "record:created",
        "schema_version": 1,
        "workspace_id": record.workspace_id,
        "payload": {
            "record_id": record.id,
            "client_id": record.client_id,
            "name": record.name,
        },
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
```

Builders are pure functions — no database calls, no I/O.

---

## Batch events

When a command processes multiple entities at once, it must emit **one batch event** carrying all affected `client_id`s — not N individual events. Emitting 50 individual `record:updated` events for a bulk update floods the Socket.IO room and causes 50 cache invalidations on the frontend.

### Batch event builder

```python
# services/infra/events/builders/<domain>/record_events.py
from datetime import datetime, timezone


def build_records_bulk_updated_event(
    records: list,
    workspace_id: int,
    triggered_by_user_id: int,
) -> dict:
    return {
        "event_type": "record:batch-updated",
        "schema_version": 1,
        "workspace_id": workspace_id,
        "payload": {
            "client_ids": [r.client_id for r in records],
            "count": len(records),
        },
        "meta": {
            "triggered_by_user_id": triggered_by_user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
```

Batch event payload rules:
- `client_ids` is a list of **public** `client_id` strings — never internal DB `id`s.
- `count` is informational; the frontend uses `ids` length, not `count`, for iteration.
- For very large batches (200+ entities) where enumerating IDs is wasteful, use a broad signal event (`record:invalidate-all`) with no ID list.

### Batch command pattern

A bulk command collects all affected instances, commits once, then emits one batch event:

```python
def bulk_update_records(ctx: ServiceContext, record_client_ids: list[str], updates: dict) -> dict:
    with db.session.begin():
        records = (
            db.session.query(Record)
            .filter(Record.client_id.in_(record_client_ids))
            .filter_by(workspace_id=ctx.workspace_id)
            .all()
        )
        for record in records:
            record.name = updates.get("name", record.name)
            # ... apply updates

        batch_event = build_records_bulk_updated_event(
            records=records,
            workspace_id=ctx.workspace_id,
            triggered_by_user_id=ctx.user_id,
        )

    emit_record_events(ctx, [batch_event])   # one event, one socket push
    return {"updated_count": len(records)}
```

### Broad signal — when IDs are too many to enumerate

For operations that touch hundreds or thousands of entities (nightly reconciliation, mass import):

```python
def build_records_invalidate_all_event(workspace_id: int, triggered_by_user_id: int) -> dict:
    return {
        "event_type": "record:invalidate-all",
        "schema_version": 1,
        "workspace_id": workspace_id,
        "payload": {},
        "meta": {
            "triggered_by_user_id": triggered_by_user_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
```

The frontend handler for `record:invalidate-all` calls `invalidateQueries` on the whole entity namespace with `refetchType: 'active'` — only components currently observing the entity re-fetch.

### Single vs batch vs broadcast — decision rule

| Command type | Entities changed | Emit |
|---|---|---|
| Single-entity create/update/delete | 1 | `record:created` / `record:updated` / `record:deleted` (single) |
| Bulk command | 2–200 | `record:batch-updated` with full `client_ids` list |
| Mass operation (import, reconcile) | 200+ | `record:invalidate-all` (no IDs) |

---

## Event emitters

Emitters write events to the outbox (DB or Redis) after the transaction commits:

```python
# services/infra/events/emitters/<domain>.py
from my_app.services.context import ServiceContext
from my_app.services.infra.events.event_bus import get_event_bus


def emit_record_events(ctx: ServiceContext, events: list[dict]) -> None:
    bus = get_event_bus()
    for event in events:
        bus.publish(event)
```

**Note — no suppression flags:** Suppressing events via a flag on the context is an anti-pattern. If an operation should not emit events, it is either:
1. An internal/system-only command explicitly designed to not emit (its body calls no emitter), or
2. A testing concern handled by monkeypatching the emitter in tests — not via a production flag.

The correct pattern:

```python
def create_record(ctx: ServiceContext) -> dict:
    pending_events: list[dict] = []

    with db.session.begin():
        # ...build record...
        pending_events.append(build_record_created_event(record_instance))

    # Commands always emit. Suppression is a test concern.
    emit_record_events(ctx, pending_events)
    return result
```

---

## Event handlers

Handlers respond to a specific event type and perform one side effect:

```python
# services/infra/events/handlers/<domain>/record_notification.py
import logging

logger = logging.getLogger(__name__)


def handle_record_created_send_notification(event: dict) -> None:
    payload = event["payload"]
    workspace_id = event["workspace_id"]
    # fetch contact, render template, send notification
    # if this fails, log and re-raise so the dispatcher can retry
    logger.info("Sending creation notification for record %s", payload["record_id"])
    ...
```

**Rules:**
- One handler = one side effect. Do not combine multiple integrations in one handler.
- Handlers are registered in a registry file:

```python
# services/infra/events/registry/<domain>.py
from ..handlers.<domain>.record_notification import handle_record_created_send_notification
from ..handlers.<domain>.record_analytics import handle_record_created_update_analytics
from ..handlers.<domain>.record_batch_realtime import handle_records_bulk_updated_realtime

RECORD_EVENT_HANDLERS = {
    "record:created": [
        handle_record_created_send_notification,
        handle_record_created_update_analytics,
    ],
    "record:batch-updated": [
        handle_records_bulk_updated_realtime,
    ],
}
```

- Handlers must be idempotent. The dispatcher may retry on failure. Use the `AppEventOutbox` table to track dispatch state and prevent duplicate side effects.

---

## Outbox pattern

For reliability, events are written to `AppEventOutbox` within the same transaction as the write:

```python
# models/tables/app_event_outbox.py
class AppEventOutbox(db.Model):
    __tablename__ = "app_event_outbox"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending | dispatched | failed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=...)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

The background dispatcher reads `pending` outbox rows, calls the registered handlers, and marks rows as `dispatched`. This guarantees at-least-once delivery even if the application crashes between the commit and the Redis publish.

---

## Real-time notifications

Socket.IO pushes are a special class of side effect. They are fire-and-forget and do not use the outbox:

```python
# services/infra/events/realtime_refresh.py
from my_app.socketio_instance import socketio


def push_realtime_refresh(room: str, event_name: str, data: dict) -> None:
    socketio.emit(event_name, data, room=room, namespace="/")
```

Real-time pushes are called from event handlers, not from commands directly.

---

## Event schema versioning

Event payloads evolve over time as domains add new fields. Handlers must not break when they receive an event with additional fields they do not recognize.

**Rule: event payloads are append-only.** You may add new keys to a payload. You must never remove or rename existing keys.

Add a `schema_version` field to every event:

```python
def build_record_created_event(record) -> dict:
    return {
        "event_type": "record:created",
        "schema_version": 1,          # increment when payload shape changes
        "workspace_id": record.workspace_id,
        "payload": {
            "record_id": record.id,
            "client_id": record.client_id,
            "name": record.name,
        },
        "meta": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    }
```

When you need to add a field that has no safe default for old handlers, increment `schema_version` and update all registered handlers before deploying.

Handler defensiveness pattern — always use `.get()` with a default for fields that may not exist in older schema versions:

```python
def handle_record_created_send_notification(event: dict) -> None:
    payload = event["payload"]
    # New field added in schema_version 2 — safe default for v1 events
    priority = payload.get("priority", "standard")
    ...
```

**Never remove a key from a payload.** If a field is no longer meaningful, keep it with its last known value or `None`. Removing it breaks handlers that still reference it.

---

## Schema evolution — breaking vs non-breaking changes

Before touching an event builder, classify the change:

| Change | Breaking? | Safe action |
|---|---|---|
| Add a new key with a value | No | Add to builder; handlers use `.get()` with a default |
| Add a new key that is sometimes `None` | No | Add to builder; handlers use `.get(key)` |
| Remove a key | **Yes** | Never remove — keep with last known value or `None` |
| Rename a key | **Yes** | Add the new key alongside the old; deprecate old (never remove) |
| Change a key's type (`int` → `str`) | **Yes** | Add a new key with the new type; keep the old key |
| Change a key's semantics | **Yes** | Treat as a rename — old and new key coexist |
| Add a new event type | No | Register handlers before deploying the builder |
| Rename an event type | **Yes** | Emit both names during transition; see event type retirement below |
| Change separator (`.` → `:`) | **Yes** | Treat as rename — emit both during transition |

---

## Safe schema evolution sequence

### Adding a new field (backwards-compatible)

```
1. Add the field to the event builder
2. Deploy — handlers that haven't been updated use `.get(field, default)`
3. Update handlers to use the new field
4. Done — no version bump required if the field has a safe default
```

If the field has no safe default (a handler cannot function correctly without it), increment `schema_version` and follow the breaking change sequence below.

### Breaking change sequence

A breaking change is one where old handlers will misinterpret or crash on events built by the new builder.

```
1. Increment schema_version in the builder (e.g., 1 → 2)
2. Update ALL registered handlers to handle both schema_version 1 and 2
3. Deploy the handler updates first — handlers now tolerate both versions
4. Deploy the builder update — new events are schema_version 2
5. After all pending schema_version 1 events in the outbox are dispatched:
   remove the schema_version 1 branch from handlers
```

Handler that supports both versions:

```python
def handle_record_created_send_notification(event: dict) -> None:
    payload = event["payload"]
    version = event.get("schema_version", 1)

    if version >= 2:
        priority = payload["priority"]           # required in v2
        recipient = payload["recipient_id"]      # new in v2
    else:
        priority = payload.get("priority", "standard")   # default for v1
        recipient = None

    ...
```

### Rolling deploy window

During a rolling deploy, two application versions run simultaneously:
- Old instances emit `schema_version 1` events
- New instances emit `schema_version 2` events
- The dispatcher picks up events from both versions

This is why handlers must support both versions before the builder is updated. Never deploy the builder and the handler in the same release if the change is breaking — the old handler will fail on events emitted by new instances before the old instances are replaced.

---

## Event type retirement

When an event type is no longer needed:

```
1. Remove the builder call from all commands (no new events are emitted)
2. Leave the handlers registered — they must drain any pending outbox rows
3. Wait until AppEventOutbox has no pending rows of the old event type:
   SELECT COUNT(*) FROM app_event_outbox WHERE event_type = 'record:old-event' AND status = 'pending';
4. Unregister the handlers from the registry
5. Delete the handler files
```

Never unregister a handler before the outbox queue for that event type is empty. Pending events with no handler are silently dropped — that is a dead letter.

---

## Event catalog maintenance

Every event type emitted by the application must have an entry in `docs/events/catalog.md`:

```markdown
## record:created

**Schema version:** 2
**Emitted by:** `create_record` command
**Handlers:** `record_notification`, `record_analytics`, `realtime_refresh`

### Payload (v2)
| Field | Type | Description |
|---|---|---|
| record_id | int | Internal DB ID |
| client_id | str | External-facing UUID |
| name | str | Record display name |
| priority | str | Added in v2. Values: standard, high, urgent |
| recipient_id | int \| null | Added in v2. Null if no recipient assigned |

### Payload (v1, deprecated)
Same as v2 without `priority` and `recipient_id`.
```

The event catalog must be updated in the same PR as any builder or handler change. An undocumented event is an unmaintainable event.

---

## Dead letter handling

When a handler fails after all retries, the outbox row must be marked `failed` — not silently dropped. A failed event is a business signal: a notification was not sent, a webhook was not forwarded, analytics were not updated.

Failed outbox rows must be:
1. Logged at `ERROR` level with the event type, payload, and exception.
2. Set to `status="failed"` in `AppEventOutbox`.
3. Retained in the database — never deleted automatically.
4. Reviewed on a defined cadence (weekly at minimum).

The dispatcher marks failure after the final retry:

```python
def dispatch_event(outbox_row: AppEventOutbox) -> None:
    handlers = EVENT_REGISTRY.get(outbox_row.event_type, [])
    for handler in handlers:
        try:
            handler(outbox_row.payload)
        except Exception:
            logger.exception(
                "Handler failed | event_type=%s outbox_id=%s handler=%s",
                outbox_row.event_type,
                outbox_row.id,
                handler.__name__,
            )
            outbox_row.status = "failed"
            db.session.commit()
            raise   # let the worker's retry policy decide whether to re-enqueue

    outbox_row.status = "dispatched"
    outbox_row.dispatched_at = datetime.now(timezone.utc)
    db.session.commit()
```

A background report job or an alert must notify the team when failed outbox rows accumulate. Use `SELECT COUNT(*) FROM app_event_outbox WHERE status = 'failed'` and alert if the count exceeds a threshold.

**Manual replay:** When the root cause of handler failures is fixed, failed rows can be replayed by resetting their status to `pending` and letting the dispatcher pick them up again. This must be done via a CLI command — not directly via SQL:

```bash
flask replay-failed-events --event-type record:created --since 2025-03-01
```
