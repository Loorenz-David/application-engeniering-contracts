from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase8(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 8 - Notification System ------------------------------------")

    # ── Notification domain enums + results ───────────────────────────────────
    _write(root / a / "domain" / "notifications" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "notifications" / "enums.py", """\
import enum


class NotificationType(str, enum.Enum):
    \"\"\"str enum — values stored directly in String column, no migration on extension.\"\"\"
    # Case domain
    CASE_MESSAGE           = "case:message"
    CASE_STATE_CHANGED     = "case:state-changed"
    CASE_PARTICIPANT_ADDED = "case:participant-added"
    # Add more notification types here as domains expand.
""", force=force)

    _write(root / a / "domain" / "notifications" / "results.py", """\
from dataclasses import dataclass


@dataclass
class NotificationResult:
    client_id:         str
    notification_type: str
    title:             str
    body:              str
    entity_type:       str | None
    entity_client_id:  str | None
    read_at:           str | None
    created_at:        str
""", force=force)

    # ── Notification models (47_notifications.md — contract-compliant) ────────
    _touch(root / a / "models" / "tables" / "notifications" / "__init__.py", force=force)

    _write(root / a / "models" / "tables" / "notifications" / "notification.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class Notification(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ntf"
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_unread", "user_id", "read_at"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    notification_type: Mapped[str] = mapped_column(String(64),  nullable=False, index=True)
    title:             Mapped[str] = mapped_column(String(256), nullable=False)
    body:              Mapped[str] = mapped_column(Text,        nullable=False)

    # Deep-link target
    entity_type:      Mapped[str | None] = mapped_column(String(64),  nullable=True)
    entity_client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    read_at:    Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime]        = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
""", force=force)

    _write(root / a / "models" / "tables" / "notifications" / "push_subscription.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class PushSubscription(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "psub"
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", "endpoint", name="uq_push_subscription_user_endpoint"),
    )

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )

    endpoint:     Mapped[str]        = mapped_column(Text,        nullable=False)
    p256dh:       Mapped[str]        = mapped_column(Text,        nullable=False)
    auth:         Mapped[str]        = mapped_column(Text,        nullable=False)
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)

    created_at:   Mapped[datetime]        = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped["User"] = relationship("User", foreign_keys=[user_id])
""", force=force)

    _write(root / a / "models" / "tables" / "notifications" / "notification_pin.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class NotificationPin(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "npin"
    __tablename__ = "notification_pins"
    __table_args__ = (
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
""", force=force)

    # ── VAPID helper ──────────────────────────────────────────────────────────
    _touch(root / a / "services" / "infra" / "push" / "__init__.py", force=force)
    _write(root / a / "services" / "infra" / "push" / "vapid.py", f"""\
import json
import logging

from pywebpush import WebPushException, webpush

from {a}.config import settings

logger = logging.getLogger(__name__)


def send_web_push(
    endpoint: str,
    p256dh:   str,
    auth:     str,
    payload:  dict,
) -> None:
    \"\"\"Send a single push notification to one browser subscription.
    Raises WebPushException on failure.
    Caller must handle 410 Gone by deleting the PushSubscription row.
    \"\"\"
    webpush(
        subscription_info={{"endpoint": endpoint, "keys": {{"p256dh": p256dh, "auth": auth}}}},
        data=json.dumps(payload),
        vapid_private_key=settings.vapid_private_key,
        vapid_claims={{"sub": f"mailto:{{settings.vapid_contact_email}}"}},
    )
""", force=force)

    # ── Notification commands ─────────────────────────────────────────────────
    _touch(root / a / "services" / "commands" / "notifications" / "__init__.py", force=force)

    _write(root / a / "services" / "commands" / "notifications" / "register_push_subscription.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import select

from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.services.context import ServiceContext


async def register_push_subscription(ctx: ServiceContext) -> dict:
    \"\"\"Upsert PushSubscription for (user_id, endpoint). Idempotent.\"\"\"
    data     = ctx.incoming_data
    endpoint = data["endpoint"]

    result = await ctx.session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == ctx.user_id,
            PushSubscription.endpoint == endpoint,
        )
    )
    sub = result.scalar_one_or_none()

    if sub is None:
        sub = PushSubscription(
            user_id=ctx.user_id,
            endpoint=endpoint,
            p256dh=data["p256dh"],
            auth=data["auth"],
        )
        ctx.session.add(sub)
    else:
        sub.p256dh = data["p256dh"]
        sub.auth   = data["auth"]

    sub.device_label = data.get("device_label")
    sub.last_used_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    return {{"subscription": {{"client_id": sub.client_id}}}}
""", force=force)

    _write(root / a / "services" / "commands" / "notifications" / "unregister_push_subscription.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.services.context import ServiceContext


async def unregister_push_subscription(ctx: ServiceContext) -> dict:
    \"\"\"Hard-delete PushSubscription by endpoint. No-op if already deleted.\"\"\"
    endpoint = ctx.incoming_data.get("endpoint")
    result   = await ctx.session.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == ctx.user_id,
            PushSubscription.endpoint == endpoint,
        )
    )
    sub = result.scalar_one_or_none()
    if sub:
        await ctx.session.delete(sub)
        await ctx.session.commit()
    return {{}}
""", force=force)

    _write(root / a / "services" / "commands" / "notifications" / "pin_notification.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.notification_pin import NotificationPin
from {a}.services.context import ServiceContext


async def pin_notification(ctx: ServiceContext) -> dict:
    \"\"\"Upsert NotificationPin for (user_id, entity_type, entity_client_id). Idempotent.\"\"\"
    data = ctx.incoming_data
    result = await ctx.session.execute(
        select(NotificationPin).where(
            NotificationPin.user_id          == ctx.user_id,
            NotificationPin.entity_type      == data["entity_type"],
            NotificationPin.entity_client_id == data["entity_client_id"],
        )
    )
    pin = result.scalar_one_or_none()
    if pin is None:
        pin = NotificationPin(
            user_id=ctx.user_id,
            entity_type=data["entity_type"],
            entity_client_id=data["entity_client_id"],
        )
        ctx.session.add(pin)
        await ctx.session.commit()
    return {{"pin": {{"client_id": pin.client_id}}}}
""", force=force)

    _write(root / a / "services" / "commands" / "notifications" / "unpin_notification.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.notification_pin import NotificationPin
from {a}.services.context import ServiceContext


async def unpin_notification(ctx: ServiceContext) -> dict:
    \"\"\"Hard-delete NotificationPin. No-op if it does not exist.\"\"\"
    data   = ctx.incoming_data
    result = await ctx.session.execute(
        select(NotificationPin).where(
            NotificationPin.user_id          == ctx.user_id,
            NotificationPin.entity_type      == data["entity_type"],
            NotificationPin.entity_client_id == data["entity_client_id"],
        )
    )
    pin = result.scalar_one_or_none()
    if pin:
        await ctx.session.delete(pin)
        await ctx.session.commit()
    return {{}}
""", force=force)

    _write(root / a / "services" / "commands" / "notifications" / "mark_notifications_read.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import select, update

from {a}.models.tables.notifications.notification import Notification
from {a}.services.context import ServiceContext


async def mark_notifications_read(ctx: ServiceContext) -> dict:
    \"\"\"Set read_at = now() on unread notifications. Idempotent (already-read rows skipped).

    incoming_data keys:
      notification_client_ids (list[str]) — explicit list, OR
      mark_all_read           (bool)      — mark every unread notification for the user.
    \"\"\"
    data        = ctx.incoming_data
    mark_all    = data.get("mark_all_read", False)
    target_ids  = data.get("notification_client_ids", [])
    now         = datetime.now(timezone.utc)

    stmt = (
        update(Notification)
        .where(
            Notification.user_id == ctx.user_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=now)
    )

    if not mark_all:
        if not target_ids:
            return {{"marked_read": 0}}
        stmt = stmt.where(Notification.client_id.in_(target_ids))

    result = await ctx.session.execute(stmt)
    await ctx.session.commit()
    return {{"marked_read": result.rowcount}}
""", force=force)

    # ── Notification queries ──────────────────────────────────────────────────
    _touch(root / a / "services" / "queries" / "notifications" / "__init__.py", force=force)

    _write(root / a / "services" / "queries" / "notifications" / "list_notifications.py", f"""\
from sqlalchemy import func, select

from {a}.domain.notifications.results import NotificationResult
from {a}.models.tables.notifications.notification import Notification
from {a}.services.context import ServiceContext


async def list_notifications(ctx: ServiceContext) -> dict:
    \"\"\"Paginated notification list for the authenticated user.

    incoming_data keys:
      unread_only      (bool, default False)
      limit            (int,  default 30)
      before_client_id (str | None) — keyset cursor
    \"\"\"
    params       = ctx.incoming_data
    unread_only  = params.get("unread_only", False)
    limit        = min(int(params.get("limit", 30)), 100)
    cursor_id    = params.get("before_client_id")

    stmt = select(Notification).where(Notification.user_id == ctx.user_id)

    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))

    if cursor_id:
        cursor_result = await ctx.session.execute(
            select(Notification.created_at).where(Notification.client_id == cursor_id)
        )
        cursor_at = cursor_result.scalar_one_or_none()
        if cursor_at:
            stmt = stmt.where(Notification.created_at < cursor_at)

    stmt = stmt.order_by(Notification.created_at.desc()).limit(limit + 1)

    result = await ctx.session.execute(stmt)
    rows   = result.scalars().all()
    has_more = len(rows) > limit

    # Total unread count (always included for badge)
    count_result = await ctx.session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == ctx.user_id,
            Notification.read_at.is_(None),
        )
    )
    unread_count = count_result.scalar_one()

    return {{
        "notifications": [_serialize(n) for n in rows[:limit]],
        "has_more":      has_more,
        "unread_count":  unread_count,
    }}


def _serialize(n: Notification) -> dict:
    return {{
        "client_id":         n.client_id,
        "notification_type": n.notification_type,
        "title":             n.title,
        "body":              n.body,
        "entity_type":       n.entity_type,
        "entity_client_id":  n.entity_client_id,
        "read_at":           n.read_at.isoformat() if n.read_at else None,
        "created_at":        n.created_at.isoformat(),
    }}
""", force=force)

    _write(root / a / "services" / "queries" / "notifications" / "get_unread_notification_count.py", f"""\
from sqlalchemy import func, select

from {a}.models.tables.notifications.notification import Notification
from {a}.services.context import ServiceContext


async def get_unread_notification_count(ctx: ServiceContext) -> dict:
    \"\"\"Lightweight unread-count query for badge polling and post-login hydration.\"\"\"
    result = await ctx.session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.user_id == ctx.user_id,
            Notification.read_at.is_(None),
        )
    )
    return {{"unread_count": result.scalar_one()}}
""", force=force)

    # ── Notification task handlers ────────────────────────────────────────────
    _touch(root / a / "services" / "tasks" / "notifications" / "__init__.py", force=force)

    _write(root / a / "services" / "tasks" / "notifications" / "create_notifications.py", f"""\
import logging

from sqlalchemy import select

from {a}.models.tables.notifications.notification import Notification
from {a}.models.database import get_db_session
from {a}.services.infra.events.build_event import build_user_event
from {a}.services.infra.events import dispatch
from {a}.services.infra.presence import get_viewers

logger = logging.getLogger(__name__)


async def handle_create_notifications(payload: dict) -> None:
    notification_type = payload["notification_type"]
    user_ids          = list(payload.get("user_ids", []))
    title             = payload["title"]
    body              = payload["body"]
    entity_type       = payload.get("entity_type")
    entity_client_id  = payload.get("entity_client_id")
    exclude_viewing   = payload.get("exclude_viewing", [])

    # Exclude users currently viewing the entity contexts
    viewing_ids: set[str] = set()
    for ctx in exclude_viewing:
        viewing_ids |= get_viewers(ctx["entity_type"], ctx["entity_client_id"])
    if viewing_ids:
        user_ids = [uid for uid in user_ids if uid not in viewing_ids]
    if not user_ids:
        return

    pending_events = []

    async for session in get_db_session():
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

        await session.flush()

        # Collect client_ids for newly inserted notifications
        for obj in session.new:
            if isinstance(obj, Notification):
                pending_events.append(
                    build_user_event(
                        user_id=obj.user_id,
                        event_name="notification:new",
                        client_id=obj.client_id,
                    )
                )
                # Enqueue SEND_PUSH_NOTIFICATION task within same transaction
                from {a}.domain.execution.enums import TaskType
                from {a}.services.infra.execution.task_factory import create_instant_task
                await create_instant_task(
                    session=session,
                    task_type=TaskType.SEND_PUSH_NOTIFICATION,
                    payload={{
                        "user_id":                obj.user_id,
                        "notification_client_id": obj.client_id,
                        "title":                  title,
                        "body":                   body,
                        "entity_type":            entity_type,
                        "entity_client_id":       entity_client_id,
                    }},
                )

        await session.commit()

    await dispatch(pending_events)
""", force=force)

    _write(root / a / "services" / "tasks" / "notifications" / "send_push_notification.py", f"""\
import logging

from pywebpush import WebPushException
from sqlalchemy import delete, select

from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.models.database import get_db_session
from {a}.services.infra.push.vapid import send_web_push

logger = logging.getLogger(__name__)


async def handle_send_push_notification(payload: dict) -> None:
    user_id = payload["user_id"]

    async for session in get_db_session():
        result        = await session.execute(
            select(PushSubscription).where(PushSubscription.user_id == user_id)
        )
        subscriptions = result.scalars().all()
        if not subscriptions:
            return

        push_payload = {{
            "title": payload["title"],
            "body":  payload["body"],
            "data": {{
                "notification_client_id": payload.get("notification_client_id"),
                "entity_type":            payload.get("entity_type"),
                "entity_client_id":       payload.get("entity_client_id"),
            }},
        }}

        stale_ids = []
        for sub in subscriptions:
            try:
                send_web_push(sub.endpoint, sub.p256dh, sub.auth, push_payload)
            except WebPushException as exc:
                if exc.response and exc.response.status_code == 410:
                    stale_ids.append(sub.client_id)
                else:
                    logger.warning(
                        "push failed | sub=%s status=%s",
                        sub.client_id,
                        exc.response.status_code if exc.response else "no response",
                    )

        if stale_ids:
            await session.execute(
                delete(PushSubscription).where(PushSubscription.client_id.in_(stale_ids))
            )
            await session.commit()
""", force=force)

    # ── Case notification target resolver ─────────────────────────────────────
    _touch(root / a / "domain" / "cases" / "__init__.py", force=force)
    _write(root / a / "domain" / "cases" / "notification_targets.py", f"""\
import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.tables.notifications.notification_pin import NotificationPin


async def resolve_case_notification_targets(
    session: AsyncSession,
    case,
    *,
    exclude_user_id: str | None = None,
) -> set[str]:
    \"\"\"Return all user client_ids that should receive notifications for this case.
    Sources run concurrently. Add new sources without touching any command.
    \"\"\"
    sources = await asyncio.gather(
        _get_participants(session, case),
        _get_pinned_subscribers(session, case),
    )
    target_ids: set[str] = set().union(*sources)
    if exclude_user_id:
        target_ids.discard(exclude_user_id)
    return target_ids


async def _get_participants(session: AsyncSession, case) -> set[str]:
    try:
        from {a}.models.tables.cases.case_participant import CaseParticipant
        rows = await session.execute(
            select(CaseParticipant.user_id).where(CaseParticipant.case_id == case.client_id)
        )
        return {{row[0] for row in rows}}
    except Exception:
        return set()


async def _get_pinned_subscribers(session: AsyncSession, case) -> set[str]:
    rows = await session.execute(
        select(NotificationPin.user_id).where(
            NotificationPin.entity_type      == "case",
            NotificationPin.entity_client_id == case.client_id,
        )
    )
    return {{row[0] for row in rows}}
""", force=force)

    # ── Notifications API router (full contract surface) ──────────────────────
    _write(root / a / "routers" / "api_v1" / "notifications.py", f"""\
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.config import settings
from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.commands.notifications.mark_notifications_read import mark_notifications_read
from {a}.services.commands.notifications.pin_notification import pin_notification
from {a}.services.commands.notifications.register_push_subscription import register_push_subscription
from {a}.services.commands.notifications.unpin_notification import unpin_notification
from {a}.services.commands.notifications.unregister_push_subscription import unregister_push_subscription
from {a}.services.context import ServiceContext
from {a}.services.queries.notifications.get_unread_notification_count import get_unread_notification_count
from {a}.services.queries.notifications.list_notifications import list_notifications
from {a}.services.run_service import run_service

router = APIRouter()


class PushSubscriptionBody(BaseModel):
    endpoint:     str
    p256dh:       str
    auth:         str
    device_label: str | None = None


class PinBody(BaseModel):
    entity_type:      str
    entity_client_id: str


class MarkReadBody(BaseModel):
    notification_client_ids: list[str] | None = None
    mark_all_read:           bool = False


async def _run(command, incoming_data: dict, claims: dict, session: AsyncSession):
    outcome = await run_service(
        command,
        ServiceContext(identity=claims, incoming_data=incoming_data, session=session),
    )
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


# ── List / unread count ──────────────────────────────────────────────────────

@router.get("")
async def list_notifications_route(
    unread_only: bool = False,
    limit: int = 30,
    before_client_id: str | None = None,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(
        list_notifications,
        {{"unread_only": unread_only, "limit": limit, "before_client_id": before_client_id}},
        claims,
        session,
    )


@router.get("/unread-count")
async def unread_count_route(
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(get_unread_notification_count, {{}}, claims, session)


@router.post("/mark-read")
async def mark_read_route(
    body: MarkReadBody,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(mark_notifications_read, body.model_dump(), claims, session)


# ── Push subscriptions ───────────────────────────────────────────────────────

@router.post("/push-subscription")
async def subscribe_route(
    body: PushSubscriptionBody,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(register_push_subscription, body.model_dump(), claims, session)


@router.delete("/push-subscription")
async def unsubscribe_route(
    body: PushSubscriptionBody,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(unregister_push_subscription, body.model_dump(), claims, session)


@router.get("/vapid-public-key")
async def vapid_public_key_route():
    \"\"\"Public endpoint — no auth required. Frontend fetches before login.\"\"\"
    return build_ok({{"public_key": getattr(settings, "vapid_public_key", "")}})


# ── Pins ─────────────────────────────────────────────────────────────────────

@router.post("/pins")
async def pin_route(
    body: PinBody,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(pin_notification, body.model_dump(), claims, session)


@router.delete("/pins")
async def unpin_route(
    body: PinBody,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    return await _run(unpin_notification, body.model_dump(), claims, session)
""", force=force)

    # ── Add VAPID config settings ─────────────────────────────────────────────
    replace_once(
        root / a / "config.py",
        "    # Environment\n",
        "    # VAPID (Web Push)\n"
        "    vapid_private_key:   str | None = Field(default=None, alias=\"VAPID_PRIVATE_KEY\")\n"
        "    vapid_public_key:    str | None = Field(default=None, alias=\"VAPID_PUBLIC_KEY\")\n"
        "    vapid_contact_email: str        = Field(default=\"admin@example.com\", alias=\"VAPID_CONTACT_EMAIL\")\n\n"
        "    # Environment\n",
    )
    replace_once(
        root / ".env.example",
        "# Comma-separated for multiple origins\n",
        "# Web Push (VAPID)\n"
        "# VAPID_PRIVATE_KEY=\n"
        "# VAPID_PUBLIC_KEY=\n"
        "# VAPID_CONTACT_EMAIL=admin@example.com\n\n"
        "# Comma-separated for multiple origins\n",
    )

    # ── Wire models into __init__.py ──────────────────────────────────────────
    append_once(
        root / a / "models" / "__init__.py",
        (
            f"from {a}.models.tables.notifications import notification  # noqa: F401\n"
            f"from {a}.models.tables.notifications import notification_pin  # noqa: F401\n"
            f"from {a}.models.tables.notifications import push_subscription  # noqa: F401\n"
        ),
    )

    # ── Wire router into api_v1/__init__.py ───────────────────────────────────
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import auth, health\n",
        f"from {a}.routers.api_v1 import auth, health, notifications\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n',
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n'
        '    app.include_router(notifications.router, prefix="/api/v1/notifications", tags=["notifications"])\n',
    )
