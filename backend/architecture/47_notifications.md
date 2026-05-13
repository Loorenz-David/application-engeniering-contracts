# 47 — Notification System Contract

## Purpose

Notifications tell users about events that are relevant to them but that they are not currently watching. They are distinct from socket events, which keep active views reactive. A notification reaches a user regardless of what they have open — or whether their browser is open at all.

Two delivery channels work in parallel:

| Channel | Delivery | Persistence | Works offline |
|---|---|---|---|
| In-app | `UserEvent` → user room → frontend fetches | DB row | No — signaled on reconnect via unread count |
| Browser push | VAPID → push service → service worker | DB row | Yes — shown as system notification |

Both channels are backed by the same `Notification` row. The DB row is the source of truth; the socket push and the browser push are optimistic delivery attempts on top of it.

---

## Architecture

```
Domain command (or task worker)
  │
  └─ create_instant_task(TaskType.CREATE_NOTIFICATIONS, payload)
                │
          Task worker: create_notifications_handler
                │
          For each target user:
                ├─ INSERT Notification row
                ├─ publish UserEvent("notification:new") → user room
                │     └─ online user receives signal, fetches /notifications
                └─ create_instant_task(TaskType.SEND_PUSH_NOTIFICATION, {user_id, ...})
                              │
                        Task worker: send_push_notification_handler
                              │
                        Load PushSubscription rows for user
                        For each subscription: pywebpush → push service → service worker
                        On 410 Gone: delete stale subscription
```

---

## Notification types — `domain/notifications/enums.py`

```python
import enum


class NotificationType(str, enum.Enum):
    # Case domain
    CASE_MESSAGE          = "case:message"
    CASE_STATE_CHANGED    = "case:state-changed"
    CASE_PARTICIPANT_ADDED = "case:participant-added"

    # Extensible — add values here when a new domain gains notification support.
    # Never pass raw strings as notification_type in application code.
```

`NotificationType` is a `str` enum — the value IS the string, so it is safe to store directly in a `String` column and compare against without a Postgres enum type. New notification types never require a migration.

---

## Models

### `Notification` — `models/tables/notifications/notification.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin


class Notification(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ntf"
    __tablename__    = "notifications"
    __table_args__   = (
        Index("ix_notifications_user_unread", "user_id", "read_at"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    notification_type: Mapped[str] = mapped_column(String(64),   nullable=False, index=True)
    title:             Mapped[str] = mapped_column(String(256),  nullable=False)
    body:              Mapped[str] = mapped_column(Text,         nullable=False)

    # Deep-link target — the entity the notification is about
    entity_type:      Mapped[str | None] = mapped_column(String(64),  nullable=True)
    entity_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    read_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime]        = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
```

`entity_type` + `entity_client_id` together form a deep-link target. The frontend maps `entity_type` to a route and navigates to `entity_client_id` when the notification is tapped. The compound index on `(user_id, read_at)` makes the common query — unread notifications for a user — fast without a full table scan.

---

### `PushSubscription` — `models/tables/notifications/push_subscription.py`

```python
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin


class PushSubscription(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "psub"
    __tablename__    = "push_subscriptions"
    __table_args__   = (
        UniqueConstraint("user_id", "endpoint", name="uq_push_subscription_user_endpoint"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    endpoint:     Mapped[str] = mapped_column(Text,        nullable=False)
    p256dh:       Mapped[str] = mapped_column(Text,        nullable=False)   # browser public key
    auth:         Mapped[str] = mapped_column(Text,        nullable=False)   # auth secret
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at:  Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
```

One row per browser/device. The `(user_id, endpoint)` unique constraint prevents duplicate registrations — the register command is idempotent: upsert on conflict. `p256dh` and `auth` come from the browser's `PushSubscription` object and are required by the VAPID protocol for payload encryption.

---

### `NotificationPin` — `models/tables/notifications/notification_pin.py`

Allows any user to subscribe to notifications for a specific entity regardless of whether they appear in that entity's participant list. A manager pinning a case receives all case notifications; unpinning removes them.

The table is entity-agnostic — `entity_type` stores an `EntityType` value (see [48_presence.md](48_presence.md)), so the same table serves cases, tasks, and any future domain without schema changes.

```python
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models.base.identity import IdentityMixin


class NotificationPin(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "npin"
    __tablename__    = "notification_pins"
    __table_args__   = (
        UniqueConstraint(
            "user_id", "entity_type", "entity_client_id",
            name="uq_notification_pin_user_entity",
        ),
    )

    user_id:          Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )
    entity_type:      Mapped[str] = mapped_column(String(64),  nullable=False, index=True)
    entity_client_id: Mapped[str] = mapped_column(String(128), nullable=False)

    pinned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
```

One row per `(user, entity_type, entity_client_id)` combination. The unique constraint makes the pin command idempotent — inserting a duplicate is a no-op.

---

## Presence module — `services/infra/presence/presence.py`

Presence tracks which users are currently viewing a given entity. It is the cross-process source of truth used by `CREATE_NOTIFICATIONS` to exclude users who are already watching the entity they would otherwise be notified about.

```python
# services/infra/presence/presence.py
from my_app.services.infra.redis import get_redis_client
from my_app.config import settings

_TTL = 7200   # 2-hour safety expiry — covers network drops without clean leave_entity


def mark_viewing(entity_type: str, entity_client_id: str, user_id: str) -> None:
    r = get_redis_client(settings.redis_uri)
    key = _key(entity_type, entity_client_id)
    r.sadd(key, user_id)
    r.expire(key, _TTL)


def mark_left(entity_type: str, entity_client_id: str, user_id: str) -> None:
    r = get_redis_client(settings.redis_uri)
    r.srem(_key(entity_type, entity_client_id), user_id)


def get_viewers(entity_type: str, entity_client_id: str) -> set[str]:
    r = get_redis_client(settings.redis_uri)
    return {v.decode() for v in r.smembers(_key(entity_type, entity_client_id))}


def _key(entity_type: str, entity_client_id: str) -> str:
    prefix = settings.redis_key_prefix
    return f"{prefix}:presence:{entity_type}:{entity_client_id}"
```

**Rules:**
- Keys are Redis SETs — one SET per entity, members are user_id strings.
- `mark_viewing` refreshes the TTL on every call — a user navigating within the same entity repeatedly stays present without expiring mid-session.
- `mark_left` is called from both `leave_entity` (clean close) and `_cleanup_presence` on WebSocket disconnect. The TTL is the fallback for hard disconnects when neither fires.
- `get_viewers` returns a `set[str]` of user_ids. The caller is responsible for comparing against target user IDs.
- The presence module is imported only by `sockets/handlers.py` (write path) and task workers (read path). Commands and routers must not import it directly.

---

## VAPID setup — `services/infra/push/vapid.py`

VAPID (Voluntary Application Server Identification) is the Web Push standard. The server holds a key pair. The public key is shared with the frontend so browsers can subscribe. The private key signs each push request so the push service can verify the sender.

```python
# services/infra/push/vapid.py
import json
from pywebpush import webpush, WebPushException
from my_app.config import settings
import logging

logger = logging.getLogger(__name__)


def send_web_push(
    endpoint: str,
    p256dh:   str,
    auth:     str,
    payload:  dict,
) -> None:
    """Send a single push notification to one browser subscription.

    Raises WebPushException on failure. Caller is responsible for handling
    410 Gone (subscription expired) by deleting the PushSubscription row.
    """
    webpush(
        subscription_info={
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        },
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={"sub": f"mailto:{settings.vapid_contact_email}"},
    )
```

**Settings required:**

```python
# config.py additions
vapid_private_key:   str   # base64url-encoded VAPID private key
vapid_public_key:    str   # base64url-encoded VAPID public key (served to frontend)
vapid_contact_email: str   # contact for push service operators
```

Generate keys once during setup:

```bash
python -c "
from py_vapid import Vapid
v = Vapid()
v.generate_keys()
print('private:', v.private_key)
print('public:', v.public_key)
"
```

Store the private key in the secrets manager. The public key is not sensitive — it is returned by the public API endpoint.

---

## Push payload schema

The push service delivers the payload to the browser's service worker. The service worker uses it to show a system notification and handle click navigation:

```json
{
    "title": "New message in Case #42",
    "body": "John: Can you check the delivery address?",
    "data": {
        "notification_client_id": "ntf_01ARZ...",
        "entity_type": "case_conversation",
        "entity_client_id": "ccv_01ARZ..."
    }
}
```

**Rules:**
- `title` and `body` must be human-readable — they appear verbatim in the OS notification tray.
- `data.entity_type` and `data.entity_client_id` are used by the service worker's `notificationclick` handler to navigate to the right route.
- `data.notification_client_id` lets the frontend mark the notification as read after click without a separate lookup.
- Never include sensitive content (message content, PII beyond display name) in the push payload — push payloads transit through the push service operator's infrastructure.

---

## Task handlers

### `CREATE_NOTIFICATIONS`

```python
# workers/tasks/notifications/create_notifications_handler.py
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.tables.notifications.notification import Notification
from my_app.services.infra.events.build_event import build_user_event
from my_app.services.infra.events import event_bus
from my_app.services.infra.execution.task_factory import create_instant_task
from my_app.services.infra.presence import presence
from my_app.domain.execution.enums import TaskType
from datetime import datetime, timezone


async def handle(raw: dict, task_id: str, session: AsyncSession) -> None:
    notification_type = raw["notification_type"]
    user_ids          = raw["user_ids"]          # list of user.client_id strings (public IDs)
    title             = raw["title"]
    body              = raw["body"]
    entity_type       = raw.get("entity_type")
    entity_client_id  = raw.get("entity_client_id")

    # Exclude users currently viewing any of the specified entity contexts
    viewing_ids: set[str] = set()
    for ctx in raw.get("exclude_viewing", []):
        viewing_ids |= presence.get_viewers(ctx["entity_type"], ctx["entity_client_id"])
    if viewing_ids:
        user_ids = [uid for uid in user_ids if uid not in viewing_ids]

    if not user_ids:
        return

    pending_events = []

    for user_id in user_ids:
        notification = Notification(
            user_id=user_id,
            notification_type=notification_type,
            title=title,
            body=body,
            entity_type=entity_type,
            entity_client_id=entity_client_id,
        )
        session.add(notification)

    await session.flush()   # assigns client_ids

    for notification in [n for n in session.new if isinstance(n, Notification)]:
        pending_events.append(build_user_event(
            user_id=str(notification.user_id),
            event_name="notification:new",
            client_id=notification.client_id,
        ))
        create_instant_task(TaskType.SEND_PUSH_NOTIFICATION, {
            "user_id":                notification.user_id,
            "notification_client_id": notification.client_id,
            "title":                  title,
            "body":                   body,
            "entity_type":            entity_type,
            "entity_client_id":       entity_client_id,
        })

    await session.commit()
    event_bus.dispatch(pending_events)
```

**How presence filtering works:**
1. The caller passes `user_ids` — all participants who should be considered for notification (sender already excluded by the caller).
2. The caller also passes `exclude_viewing` — a list of `{entity_type, entity_client_id}` dicts representing every view context where a user is already seeing the change and does not need a notification.
3. The task unions all viewer sets via Redis SMEMBERS lookups — one lookup per entry in `exclude_viewing`.
4. Any user present in the union is dropped. If the filtered list is empty, the task exits immediately.
5. Notification rows are created only for the remaining users.

**Why a list and not a single entity:** a single operation can be visible from multiple views. A new task is visible from the task detail page *and* the task list page. A new message is visible from the conversation view *and* the case detail page. The list lets the caller declare all the views where the change is already visible, and the task handles the union — the task handler itself has no knowledge of what views exist for any domain.

The `entity_type + entity_client_id` fields at the top of the payload are the **deep-link target** — where tapping the notification navigates. They are independent of `exclude_viewing` and can differ from it.

### `SEND_PUSH_NOTIFICATION`

```python
# workers/tasks/notifications/send_push_notification_handler.py
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.models.tables.notifications.push_subscription import PushSubscription
from my_app.services.infra.push.vapid import send_web_push
from pywebpush import WebPushException
import logging

logger = logging.getLogger(__name__)


async def handle(raw: dict, task_id: str, session: AsyncSession) -> None:
    user_id = raw["user_id"]

    result       = await session.execute(
        select(PushSubscription).where(PushSubscription.user_id == user_id)
    )
    subscriptions = result.scalars().all()

    if not subscriptions:
        return

    payload = {
        "title": raw["title"],
        "body":  raw["body"],
        "data": {
            "notification_client_id": raw["notification_client_id"],
            "entity_type":            raw.get("entity_type"),
            "entity_client_id":       raw.get("entity_client_id"),
        },
    }

    stale_client_ids = []

    for sub in subscriptions:
        try:
            send_web_push(sub.endpoint, sub.p256dh, sub.auth, payload)
        except WebPushException as e:
            if e.response and e.response.status_code == 410:
                stale_client_ids.append(sub.client_id)
            else:
                logger.warning(
                    "push failed | sub=%s status=%s",
                    sub.client_id,
                    e.response.status_code if e.response else "no response",
                )

    if stale_client_ids:
        await session.execute(
            delete(PushSubscription).where(PushSubscription.client_id.in_(stale_client_ids))
        )
        await session.commit()
```

HTTP 410 from the push service means the subscription is permanently expired — the user uninstalled the browser or cleared site data. These rows are deleted immediately so future tasks don't waste attempts on them.

---

## Commands

### `register_push_subscription` — `services/commands/notifications/register_push_subscription.py`

```python
# incoming_data keys:
#   endpoint     (str)
#   p256dh       (str)
#   auth         (str)
#   device_label (str | None)
#
# Upserts PushSubscription for (user_id, endpoint).
# Returns {"subscription": {"client_id": "psub_..."}}
```

Idempotent — if the same endpoint is registered again (e.g. page reload), it updates `last_used_at` and `device_label` rather than inserting a duplicate.

### `unregister_push_subscription` — `services/commands/notifications/unregister_push_subscription.py`

```python
# incoming_data keys:
#   endpoint (str)   — the browser-provided endpoint URL
#
# Hard-deletes the PushSubscription for (user_id, endpoint).
# No-op if already deleted — never raises NotFound.
# Returns {}
```

Called when the user explicitly disables notifications in the app, or when the service worker fires `pushsubscriptionchange`.

### `pin_notification` — `services/commands/notifications/pin_notification.py`

```python
# incoming_data keys:
#   entity_type      (str — EntityType value, e.g. "case")
#   entity_client_id (str — the entity to subscribe to)
#
# Creates a NotificationPin for (user_id, entity_type, entity_client_id).
# Idempotent — upserts on the unique constraint; a second pin is a no-op.
# Returns {"pin": {"client_id": "npin_..."}}
```

The user is opting in to receive all notifications for that entity, regardless of whether they appear in the entity's participant list. The `entity_type` must be a valid `EntityType` value — validate against the enum before inserting.

### `unpin_notification` — `services/commands/notifications/unpin_notification.py`

```python
# incoming_data keys:
#   entity_type      (str — EntityType value)
#   entity_client_id (str)
#
# Hard-deletes the NotificationPin for (user_id, entity_type, entity_client_id).
# No-op if the pin does not exist — never raises NotFound.
# Returns {}
```

### `mark_notifications_read` — `services/commands/notifications/mark_notifications_read.py`

```python
# incoming_data keys:
#   notification_client_ids (list[str])   — explicit list, or
#   mark_all_read           (bool)        — if true, marks all unread for user_id
#
# Sets read_at = now() on matching rows where read_at is NULL.
# Idempotent — already-read notifications are skipped, not re-stamped.
# Returns {"marked_read": int}   — count of rows updated
```

---

## Queries

### `list_notifications` — `services/queries/notifications/list_notifications.py`

```python
# incoming_data keys:
#   unread_only (bool, default False)
#   limit       (int,  default 30)
#   before_client_id (str | None) — keyset cursor paired with created_at
#
# Returns {"notifications": list[NotificationResult], "unread_count": int}
# unread_count is always the total unread count regardless of pagination —
# it is used to badge the notification bell.
```

### `get_unread_notification_count` — `services/queries/notifications/get_unread_notification_count.py`

```python
# No incoming_data keys required beyond user_id from ctx.
# Returns {"unread_count": int}
# Lightweight query for badge polling or post-login hydration.
```

---

## Result type — `domain/notifications/results.py`

```python
from dataclasses import dataclass


@dataclass
class NotificationResult:
    client_id:        str
    notification_type: str
    title:            str
    body:             str
    entity_type:      str | None
    entity_client_id: str | None
    read_at:          str | None
    created_at:       str
```

---

## API endpoints — `routers/api_v1/notifications.py`

```
GET    /notifications                    list_notifications (authenticated)
POST   /notifications/mark-read          mark_notifications_read
GET    /notifications/unread-count       get_unread_notification_count
POST   /notifications/push-subscription  register_push_subscription
DELETE /notifications/push-subscription  unregister_push_subscription
GET    /notifications/vapid-public-key   returns {"public_key": settings.vapid_public_key}
POST   /notifications/pins               pin_notification
DELETE /notifications/pins               unpin_notification
```

`/notifications/vapid-public-key` is public (no JWT required) — the frontend fetches it before the user logs in to set up the service worker.

---

## Notification target resolver

Each domain that triggers notifications owns a `notification_targets.py` module in its domain layer. This module is the single source of truth for "who gets notified about this entity." Commands call it instead of writing inline participant queries.

The resolver unions **independent sources** — each source is a private async function returning `set[str]` of `user.client_id` values. Adding a new source (e.g. workspace admins, mentioned users) means adding one new private function and including it in the `gather` call. Nothing else changes.

```python
# domain/cases/notification_targets.py
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from my_app.domain.presence.enums import EntityType
from my_app.models.tables.users.user import User
from my_app.models.tables.cases.case_participant import CaseParticipant
from my_app.models.tables.notifications.notification_pin import NotificationPin


async def resolve_case_notification_targets(
    session: AsyncSession,
    case: "Case",
    *,
    exclude_user_id: str | None = None,
) -> set[str]:
    """Return all user client_ids that should receive notifications for this case.

    Sources are queried concurrently. New sources are added here without
    touching any command.
    """
    sources = await asyncio.gather(
        _get_participants(session, case),
        _get_pinned_subscribers(session, case),
    )
    target_ids: set[str] = set().union(*sources)
    if exclude_user_id:
        target_ids.discard(exclude_user_id)
    return target_ids


async def _get_participants(session: AsyncSession, case: "Case") -> set[str]:
    rows = await session.execute(
        select(User.client_id)
        .join(CaseParticipant, CaseParticipant.user_id == User.client_id)
        .where(CaseParticipant.case_id == case.client_id)
    )
    return {row[0] for row in rows}


async def _get_pinned_subscribers(session: AsyncSession, case: "Case") -> set[str]:
    rows = await session.execute(
        select(User.client_id)
        .join(NotificationPin, NotificationPin.user_id == User.client_id)
        .where(
            NotificationPin.entity_type      == EntityType.CASE.value,
            NotificationPin.entity_client_id == case.client_id,
        )
    )
    return {row[0] for row in rows}
```

**Resolver rules:**
- Lives in `domain/<entity>/notification_targets.py` — pure domain layer, no command logic.
- Always returns `set[str]` of `user.client_id` values — consistent with what `CREATE_NOTIFICATIONS` expects.
- `exclude_user_id` is always the actor who triggered the operation — they never receive a notification about their own action.
- Sources run concurrently via `asyncio.gather` — add sources freely without increasing serial latency.
- Each source is a private function — independently testable, never called from outside this module.

---

## Triggering notifications from domain commands

Commands never call `CREATE_NOTIFICATIONS` directly. They resolve the target user IDs and queue the task after commit:

```python
# services/commands/cases/send_message.py (additions)
from my_app.services.infra.execution.task_factory import create_instant_task
from my_app.domain.execution.enums import TaskType
from my_app.domain.notifications.enums import NotificationType
from my_app.domain.presence.enums import EntityType
from my_app.domain.cases.notification_targets import resolve_case_notification_targets


async def send_message(ctx: ServiceContext) -> dict:
    ...
    event_bus.dispatch(pending_events)

    # Resolve targets via domain resolver — participants + pinned subscribers, sender excluded
    notify_ids = list(await resolve_case_notification_targets(
        ctx.session, case, exclude_user_id=ctx.user_id,
    ))

    if notify_ids:
        create_instant_task(TaskType.CREATE_NOTIFICATIONS, {
            "notification_type": NotificationType.CASE_MESSAGE,
            "user_ids":          notify_ids,
            "title":             "New message",
            "body":              request.plain_text[:120],
            "entity_type":       EntityType.CONVERSATION.value,   # deep-link target
            "entity_client_id":  conversation.client_id,
            "exclude_viewing": [
                # already seeing it: viewing the conversation directly
                {"entity_type": EntityType.CONVERSATION.value, "entity_client_id": conversation.client_id},
                # already seeing it: viewing the parent case (message visible in preview)
                {"entity_type": EntityType.CASE.value,         "entity_client_id": case.client_id},
            ],
        })

    return {"message": serialize_message(message)}
```

The `CREATE_NOTIFICATIONS` task is queued **after** the transaction commits and events are dispatched — never inside `begin()`.

---

## Frontend integration notes (service worker contract)

The server does not dictate service worker implementation, but the payload schema it sends is a contract. The service worker must handle:

```javascript
// service-worker.js
self.addEventListener("push", event => {
    const data = event.data.json()
    event.waitUntil(
        self.registration.showNotification(data.title, {
            body: data.body,
            data: data.data,
        })
    )
})

self.addEventListener("notificationclick", event => {
    event.notification.close()
    const { entity_type, entity_client_id } = event.notification.data
    // navigate to the right route based on entity_type
    event.waitUntil(clients.openWindow(`/app/${entity_type}/${entity_client_id}`))
})
```

The route mapping (`entity_type` → URL) is owned by the frontend. The backend only guarantees the two fields are present when `entity_type` is set.

---

## File structure

```
my_app/
├── domain/
│   ├── notifications/
│   │   ├── enums.py                        # NotificationType str enum
│   │   └── results.py                      # NotificationResult
│   └── <entity>/
│       └── notification_targets.py         # resolve_<entity>_notification_targets()
│                                           # one module per domain, union of sources
├── models/
│   └── tables/
│       └── notifications/
│           ├── notification.py
│           ├── push_subscription.py
│           └── notification_pin.py         # NotificationPin — generic, EntityType-keyed
├── services/
│   ├── commands/
│   │   └── notifications/
│   │       ├── register_push_subscription.py
│   │       ├── unregister_push_subscription.py
│   │       ├── mark_notifications_read.py
│   │       ├── pin_notification.py         # upsert NotificationPin
│   │       └── unpin_notification.py       # delete NotificationPin
│   ├── queries/
│   │   └── notifications/
│   │       ├── list_notifications.py
│   │       └── get_unread_notification_count.py
│   └── infra/
│       ├── presence/
│       │   └── presence.py                 # mark_viewing(), mark_left(), get_viewers()
│       └── push/
│           └── vapid.py                    # send_web_push()
├── routers/
│   └── api_v1/
│       └── notifications.py
└── workers/
    └── tasks/
        └── notifications/
            ├── create_notifications_handler.py
            └── send_push_notification_handler.py
```

---

## Rules

- **Commands never write inline target queries.** Target resolution lives in `domain/<entity>/notification_targets.py`. The command calls the resolver, passes the returned `set[str]` to `CREATE_NOTIFICATIONS`, and is done. The task never decides who should be notified.
- **Each domain owns one `notification_targets.py` module.** Adding a new notification source (pinned subscribers, workspace admins, mentioned users) means adding one private function to that module and including it in `asyncio.gather`. No command changes required.
- **`pin_notification` validates `entity_type` against `EntityType` before inserting.** A pin with an unknown entity type is rejected with a validation error — never silently stored as a free string.
- **`unpin_notification` is always a no-op when the pin does not exist.** Never raises `NotFound`.
- **`CREATE_NOTIFICATIONS` is the only entry point for creating notification rows.** Never insert `Notification` rows directly in a command or router.
- **Socket push and browser push are both best-effort on top of the DB row.** The `Notification` row is written first; delivery is secondary. A user who missed both the socket push and the browser push will see their notifications on next load via `list_notifications`.
- **Browser push payload must not contain sensitive content.** Payloads transit third-party push service infrastructure (FCM, APNs). Include enough to show a meaningful notification and navigate; never include message content beyond a truncated preview.
- **Delete stale push subscriptions immediately on 410.** Never retry a 410 — the subscription is gone permanently.
- **`unregister_push_subscription` is always by endpoint URL, not by `client_id`.** The browser always returns the endpoint; it does not know the `client_id`.
- **`mark_notifications_read` is idempotent.** Calling it twice on the same notification is safe — the second call is a no-op.
- **Presence filtering uses `exclude_viewing`, not a single entity.** Pass a list of `{entity_type, entity_client_id}` dicts — one per view context where the change is already visible. The task unions all viewer sets and excludes the union. Omitting `exclude_viewing` skips filtering and notifies all `user_ids`.
- **`exclude_viewing` and the deep-link `entity_type/entity_client_id` are independent.** The top-level `entity_type + entity_client_id` is where the notification navigates on tap. `exclude_viewing` is who to skip. They can overlap or differ — a notification about a conversation message navigates to the conversation, but excludes viewers of the conversation, the parent case, or a task list page.
- **The caller declares all relevant view contexts.** The task handler has no domain knowledge — it unions and filters. The command (or upstream task) knows what views make the change visible and must declare them all in `exclude_viewing`.
- **VAPID keys are generated once and stored in the secrets manager.** Rotating them invalidates all existing push subscriptions — all users must re-subscribe. Only rotate if the private key is compromised.
- **`/notifications/vapid-public-key` requires no authentication.** The frontend needs it before login to set up the service worker subscription.
