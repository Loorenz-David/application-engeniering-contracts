from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase8(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 8 - Notification System ------------------------------------")

    _write(root / a / "domain" / "notifications" / "__init__.py", "", force=force)
    _write(root / a / "domain" / "notifications" / "enums.py", '''\
from enum import StrEnum


class NotificationType(StrEnum):
    """Add app-specific notification types, e.g. NEW_MESSAGE."""
    pass
''', force=force)

    _touch(root / a / "models" / "tables" / "notifications" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "notifications" / "notification.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class Notification(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ntf"
    __tablename__ = "notifications"

    recipient_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_client_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(String(1024), nullable=False)
    link: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
""", force=force)
    _write(root / a / "models" / "tables" / "notifications" / "push_subscription.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class PushSubscription(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "psub"
    __tablename__ = "push_subscriptions"

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    p256dh: Mapped[str] = mapped_column(String(512), nullable=False)
    auth: Mapped[str] = mapped_column(String(512), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
""", force=force)
    _write(root / a / "models" / "tables" / "notifications" / "notification_pin.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class NotificationPin(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "npin"
    __tablename__ = "notification_pins"
    __table_args__ = (
        UniqueConstraint("user_id", "entity_type", "entity_client_id", name="uq_notification_pins_user_entity"),
    )

    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    entity_client_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    pinned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
""", force=force)

    _write(root / a / "services" / "tasks" / "notifications" / "create_notifications.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.notification import Notification
from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.models.tables.users.user import User
from {a}.services.infra.execution.db import task_db_session
from {a}.services.infra.presence import get_viewers
from {a}.sockets.manager import manager


async def handle_create_notifications(payload: dict) -> None:
    entity_type = payload.get("entity_type")
    entity_client_id = payload.get("entity_client_id")
    notification_type = payload.get("notification_type", "")
    title = payload.get("title", "")
    body = payload.get("body", "")
    link = payload.get("link")
    target_user_ids = set(payload.get("target_user_ids", []))
    if not entity_type or not entity_client_id or not target_user_ids:
        return
    target_user_ids -= get_viewers(entity_type, entity_client_id)

    async with task_db_session() as session:
        users = (await session.execute(select(User).where(User.client_id.in_(target_user_ids)))).scalars().all()
        for user in users:
            notification = Notification(
                recipient_id=user.id,
                entity_type=entity_type,
                entity_client_id=entity_client_id,
                notification_type=notification_type,
                title=title,
                body=body,
                link=link,
            )
            session.add(notification)
            await manager.send_to_user(user.client_id, "notification:new", {{"title": title, "body": body, "link": link}})

            subscription = (await session.execute(select(PushSubscription).where(PushSubscription.user_id == user.id))).scalar_one_or_none()
            if subscription:
                from {a}.domain.execution.enums import TaskType
                from {a}.services.infra.execution.task_factory import create_instant_task
                create_instant_task(TaskType.SEND_PUSH_NOTIFICATION, {{"push_subscription_id": subscription.client_id, "title": title, "body": body, "link": link}})
        await session.commit()
""", force=True)
    _write(root / a / "services" / "tasks" / "notifications" / "send_push_notification.py", f"""\
import json

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from {a}.config import settings
from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.services.infra.execution.db import task_db_session


async def handle_send_push_notification(payload: dict) -> None:
    subscription_id = payload.get("push_subscription_id")
    if not subscription_id:
        return
    async with task_db_session() as session:
        sub = (await session.execute(select(PushSubscription).where(PushSubscription.client_id == subscription_id))).scalar_one_or_none()
        if sub is None:
            return
        subscription_info = {{"endpoint": sub.endpoint, "keys": {{"p256dh": sub.p256dh, "auth": sub.auth}}}}
        try:
            webpush(
                subscription_info=subscription_info,
                data=json.dumps({{"title": payload.get("title"), "body": payload.get("body"), "link": payload.get("link")}}),
                vapid_private_key=getattr(settings, "vapid_private_key", None),
                vapid_claims={{"sub": getattr(settings, "vapid_subject", "mailto:admin@example.com")}},
            )
        except WebPushException as exc:
            if getattr(exc.response, "status_code", None) == 410:
                await session.delete(sub)
                await session.commit()
            raise
""", force=True)

    _touch(root / a / "services" / "commands" / "notifications" / "__init__.py", force=force)
    _write(root / a / "services" / "commands" / "notifications" / "subscribe_push.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import select

from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.models.tables.users.user import User
from {a}.services.context import ServiceContext


async def subscribe_push(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data
    user = (await ctx.session.execute(select(User).where(User.client_id == ctx.user_id))).scalar_one()
    sub = (await ctx.session.execute(select(PushSubscription).where(PushSubscription.endpoint == data["endpoint"]))).scalar_one_or_none()
    if sub is None:
        sub = PushSubscription(user_id=user.id, endpoint=data["endpoint"], p256dh=data["p256dh"], auth=data["auth"])
        ctx.session.add(sub)
    sub.user_agent = data.get("user_agent")
    sub.last_used_at = datetime.now(timezone.utc)
    await ctx.session.commit()
    return {{"client_id": sub.client_id}}
""", force=force)
    _write(root / a / "services" / "commands" / "notifications" / "unsubscribe_push.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.push_subscription import PushSubscription
from {a}.services.context import ServiceContext


async def unsubscribe_push(ctx: ServiceContext) -> dict:
    endpoint = ctx.incoming_data.get("endpoint")
    sub = (await ctx.session.execute(select(PushSubscription).where(PushSubscription.endpoint == endpoint))).scalar_one_or_none()
    if sub:
        await ctx.session.delete(sub)
        await ctx.session.commit()
    return {{"deleted": bool(sub)}}
""", force=force)
    _write(root / a / "services" / "commands" / "notifications" / "pin_notification.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.notification_pin import NotificationPin
from {a}.models.tables.users.user import User
from {a}.services.context import ServiceContext


async def pin_notification(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data
    user = (await ctx.session.execute(select(User).where(User.client_id == ctx.user_id))).scalar_one()
    pin = (await ctx.session.execute(
        select(NotificationPin).where(
            NotificationPin.user_id == user.id,
            NotificationPin.entity_type == data["entity_type"],
            NotificationPin.entity_client_id == data["entity_client_id"],
        )
    )).scalar_one_or_none()
    if pin is None:
        pin = NotificationPin(user_id=user.id, entity_type=data["entity_type"], entity_client_id=data["entity_client_id"])
        ctx.session.add(pin)
        await ctx.session.commit()
    return {{"client_id": pin.client_id}}
""", force=force)
    _write(root / a / "services" / "commands" / "notifications" / "unpin_notification.py", f"""\
from sqlalchemy import select

from {a}.models.tables.notifications.notification_pin import NotificationPin
from {a}.models.tables.users.user import User
from {a}.services.context import ServiceContext


async def unpin_notification(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data
    user = (await ctx.session.execute(select(User).where(User.client_id == ctx.user_id))).scalar_one()
    pin = (await ctx.session.execute(
        select(NotificationPin).where(
            NotificationPin.user_id == user.id,
            NotificationPin.entity_type == data["entity_type"],
            NotificationPin.entity_client_id == data["entity_client_id"],
        )
    )).scalar_one_or_none()
    if pin:
        await ctx.session.delete(pin)
        await ctx.session.commit()
    return {{"unpinned": bool(pin)}}
""", force=force)
    _write(root / a / "routers" / "api_v1" / "notifications.py", f"""\
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.commands.notifications.pin_notification import pin_notification
from {a}.services.commands.notifications.subscribe_push import subscribe_push
from {a}.services.commands.notifications.unpin_notification import unpin_notification
from {a}.services.commands.notifications.unsubscribe_push import unsubscribe_push
from {a}.services.context import ServiceContext
from {a}.services.run_service import run_service

router = APIRouter()


class PushSubscriptionBody(BaseModel):
    endpoint: str
    p256dh: str
    auth: str
    user_agent: str | None = None


class PinBody(BaseModel):
    entity_type: str
    entity_client_id: str


async def _run(command, body: BaseModel, claims: dict, session: AsyncSession):
    outcome = await run_service(command, ServiceContext(identity=claims, incoming_data=body.model_dump(), session=session))
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


@router.post("/push-subscriptions")
async def subscribe_push_route(body: PushSubscriptionBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(subscribe_push, body, claims, session)


@router.delete("/push-subscriptions")
async def unsubscribe_push_route(body: PushSubscriptionBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(unsubscribe_push, body, claims, session)


@router.post("/pins")
async def pin_route(body: PinBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(pin_notification, body, claims, session)


@router.delete("/pins")
async def unpin_route(body: PinBody, claims: dict = Depends(get_jwt_claims), session: AsyncSession = Depends(get_db)):
    return await _run(unpin_notification, body, claims, session)
""", force=force)

    append_once(root / a / "models" / "__init__.py", (
        f"from {a}.models.tables.notifications import notification  # noqa: F401\n"
        f"from {a}.models.tables.notifications import notification_pin  # noqa: F401\n"
        f"from {a}.models.tables.notifications import push_subscription  # noqa: F401\n"
    ))
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
