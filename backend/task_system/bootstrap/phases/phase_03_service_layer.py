from pathlib import Path

import typer

from bootstrap.writer import touch_file as _touch
from bootstrap.writer import write_file as _write


def _phase3(root: Path, a: str, force: bool) -> None:
    typer.echo("\n── Phase 3 — Service Layer ──────────────────────────────────────────")

    # ── ServiceContext ────────────────────────────────────────────────────────
    _write(root / a / "services" / "context.py", f"""\
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession


@dataclass
class ServiceContext:
    \"\"\"Carries identity and incoming data through every operation.

    Rules
    -----
    - identity   : decoded JWT claims dict from get_jwt_claims()
    - incoming_data: validated request payload (Pydantic .model_dump() or plain dict)
    - session    : the request-scoped AsyncSession from get_db()
    - Never add boolean flags or config values here
    \"\"\"
    identity: dict
    incoming_data: dict
    session: AsyncSession

    # ── convenience accessors (read from JWT claims) ──────────────────────
    @property
    def user_id(self) -> str:
        return self.identity.get("user_id", "")

    @property
    def username(self) -> str:
        return self.identity.get("username", "")

    @property
    def workspace_id(self) -> str:
        return self.identity.get("workspace_id", "")

    @property
    def workspace_role_id(self) -> str:
        return self.identity.get("workspace_role_id", "")

    @property
    def role_name(self) -> str:
        return self.identity.get("role_name", "")

    @property
    def backend_permissions(self) -> list[str]:
        return self.identity.get("backend_permissions", [])

    def has_permission(self, permission: str) -> bool:
        return permission in self.backend_permissions

    def require_permission(self, permission: str) -> None:
        from {a}.errors.permissions import PermissionDenied
        if not self.has_permission(permission):
            raise PermissionDenied(
                f"Your role does not have the '{{permission}}' permission."
            )
""", force=force)

    # ── StatusOutcome ─────────────────────────────────────────────────────────
    _write(root / a / "services" / "outcome.py", f"""\
from dataclasses import dataclass

from {a}.errors.base import DomainError


@dataclass
class StatusOutcome:
    success: bool
    data: dict | list | None = None
    error: DomainError | None = None
""", force=force)

    # ── run_service ───────────────────────────────────────────────────────────
    _write(root / a / "services" / "run_service.py", f"""\
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from {a}.errors.base import DomainError
from {a}.services.context import ServiceContext
from {a}.services.outcome import StatusOutcome

logger = logging.getLogger(__name__)


async def run_service(
    fn: Callable[[ServiceContext], Awaitable[Any]],
    ctx: ServiceContext,
) -> StatusOutcome:
    \"\"\"Single error boundary for all service calls.

    Catches DomainError and returns a failed StatusOutcome.
    Catches unexpected exceptions, logs the traceback, and returns a generic error.
    \"\"\"
    try:
        data = await fn(ctx)
        return StatusOutcome(success=True, data=data)
    except DomainError as exc:
        return StatusOutcome(success=False, error=exc)
    except Exception:
        logger.exception(
            "Unexpected error in %s | user=%s workspace=%s",
            fn.__name__,
            ctx.user_id,
            ctx.workspace_id,
        )
        return StatusOutcome(
            success=False,
            error=DomainError("An unexpected internal error occurred."),
        )
""", force=force)

    # ── WorkContext ───────────────────────────────────────────────────────────
    _write(root / a / "services" / "work_context.py", """\
from dataclasses import dataclass, field


@dataclass
class WorkContext:
    \"\"\"Accumulates state for complex commands with cascading writes.

    Use this when a single command touches multiple entities, emits multiple
    events, or needs to assemble a composite response.

    Rules
    -----
    - Create one WorkContext per command invocation, never share across commands.
    - Attach all touched entity client_ids to touched_entities.
    - Attach all emitted event type strings to emitted_events.
    - Build the response dict in data — routers read from here via StatusOutcome.
    \"\"\"
    touched_entities: list[str] = field(default_factory=list)
    emitted_events: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def touch(self, client_id: str) -> None:
        if client_id not in self.touched_entities:
            self.touched_entities.append(client_id)

    def emit(self, event_type: str) -> None:
        self.emitted_events.append(event_type)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
""", force=force)

    # ── identity resolution ───────────────────────────────────────────────────
    _write(root / a / "services" / "infra" / "identity.py", f"""\
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.errors.not_found import NotFound
from {a}.models.base.identity import IdentityMixin


async def resolve_by_client_id(
    session: AsyncSession,
    model: type[IdentityMixin],
    client_id: str,
    *,
    workspace_id: str | None = None,
    include_deleted: bool = False,
) -> IdentityMixin:
    \"\"\"Resolve a public client_id to an ORM instance.

    Raises NotFound if the entity does not exist, belongs to a different
    workspace, or has been soft-deleted (unless include_deleted=True).
    \"\"\"
    stmt = select(model).where(model.client_id == client_id)  # type: ignore[attr-defined]

    if workspace_id is not None and hasattr(model, "workspace_id"):
        stmt = stmt.where(model.workspace_id == workspace_id)  # type: ignore[attr-defined]

    if not include_deleted and hasattr(model, "is_deleted"):
        stmt = stmt.where(model.is_deleted.is_(False))  # type: ignore[attr-defined]

    result = await session.execute(stmt)
    instance = result.scalar_one_or_none()

    if instance is None:
        raise NotFound(f"{{model.__name__}} '{{client_id}}' not found.")

    return instance


async def resolve_user_client_id(session: AsyncSession, user_client_id: str) -> str:
    \"\"\"Validate and return a user client_id from a JWT claim or task payload.\"\"\"
    from {a}.models.tables.users.user import User

    stmt = select(User.client_id).where(User.client_id == user_client_id)
    result = await session.execute(stmt)
    resolved_user_id = result.scalar_one_or_none()

    if resolved_user_id is None:
        raise NotFound(f"User '{{user_client_id}}' not found.")

    return resolved_user_id
""", force=force)
