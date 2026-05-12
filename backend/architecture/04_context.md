# 04 — ServiceContext Contract

## What ServiceContext is

`ServiceContext` is the typed request envelope that travels from the router into every command and query. It carries **three things only**:

1. **Identity** — who is making this request (JWT claims: `user_id`, `workspace_id`, `role_name`, `app_scope`, etc.)
2. **Incoming data** — the raw payload or query params from the HTTP request
3. **Session** — the `AsyncSession` for this request, injected by the router via `Depends(get_db)`

That is its full scope. It is not a configuration object.

For complex write operations that need to track touched entities, pending events, or cascading response data, use a command-local `WorkContext` instead. See [39_work_context.md](39_work_context.md).

---

## Canonical definition

```python
# services/context.py
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class ServiceContext:

    def __init__(
        self,
        incoming_data:  dict | None = None,
        incoming_file:  object | None = None,
        query_params:   dict | None = None,
        identity:       dict | None = None,
        session:        "AsyncSession | None" = None,
    ) -> None:
        self.incoming_data: dict  = incoming_data or {}
        self.incoming_file        = incoming_file
        self.query_params:  dict  = query_params or {}
        self._identity:     dict  = identity or {}
        self.session:       "AsyncSession | None" = session
        self.warnings:      list[str] = []

    # --- Identity convenience properties ---

    @property
    def workspace_id(self) -> str:
        """The active workspace. All domain queries must filter by this."""
        return self._identity["workspace_id"]

    @property
    def user_id(self) -> str:
        return self._identity["user_id"]

    @property
    def workspace_role_id(self) -> str:
        return self._identity["workspace_role_id"]

    @property
    def role_name(self) -> str:
        """Base role name: 'admin' | 'member' | 'field'. For fast role checks in commands."""
        return self._identity["role_name"]

    @property
    def app_scope(self) -> str:
        """Surface the session is scoped to: 'admin' | 'field' | 'client'."""
        return self._identity["app_scope"]

    @property
    def time_zone(self) -> str:
        return self._identity.get("time_zone", "UTC")

    # --- Permission checks (read from JWT — zero DB lookups) ---

    def has_permission(self, permission: "Permission") -> bool:
        """Non-raising check. Use for conditional logic, not access control."""
        return permission.value in self._identity.get("backend_permissions", [])

    def require_permission(self, permission: "Permission") -> None:
        """Raises PermissionDenied if the caller lacks the given permission.
        ADMIN role bypasses this check — admins are never locked out.
        Call at the top of every write command before any other work.
        """
        from my_app.errors import PermissionDenied
        if self._identity.get("role_name") == "admin":
            return
        if not self.has_permission(permission):
            raise PermissionDenied(
                f"Your role does not have the '{permission.value}' permission."
            )

    # --- Warning accumulator (non-fatal issues surfaced to the client) ---

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)
```

**Property notes:**
- `workspace_id` does **not** use `.get()` with a fallback — it raises `KeyError` if missing, because a session without a workspace is a misconfigured token and should fail immediately.
- All identity fields are read-only properties. Commands never mutate `ctx._identity`.
- `require_permission` and `has_permission` read only from `self._identity["backend_permissions"]` — the list embedded in the JWT at login time. No database access.
- `ctx.session` is the `AsyncSession` for the current request. It is injected by the router and is always present in HTTP request handlers. It may be `None` in background workers that construct their own session.

---

## How to construct ServiceContext in a route handler

The router builds `ServiceContext` from injected FastAPI dependencies:

```python
# routers/api_v1/record.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from my_app.models.database import get_db
from my_app.routers.utils.jwt_dep import require_roles
from my_app.routers.utils.roles import ADMIN, MEMBER
from my_app.services.context import ServiceContext
from my_app.services.run_service import run_service
from my_app.services.commands.record.create_record import create_record

router = APIRouter()


@router.put("/")
async def create_record_route(
    body: RecordCreateBody,
    claims: dict = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data=body.model_dump(),
        identity=claims,
        session=session,
    )
    outcome = await run_service(create_record, ctx)
    ...
```

`session` is always injected from `Depends(get_db)` — never constructed manually inside a route handler.

---

## How to use ServiceContext in commands

Commands receive `ctx` and use `ctx.session` for all database operations:

```python
# services/commands/<domain>/create_record.py
from sqlalchemy import select
from my_app.services.context import ServiceContext
from my_app.models.tables.<domain>.record import Record


async def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)

    async with ctx.session.begin():
        record = Record(
            workspace_id=ctx.workspace_id,   # always from ctx, never from incoming_data
            created_by_id=ctx.user_id,
            ...
        )
        ctx.session.add(record)

    return {"record": serialize_record_full(record)}
```

---

## How to use ServiceContext in queries

```python
# services/queries/<domain>/list_records.py
from sqlalchemy import select
from my_app.services.context import ServiceContext
from my_app.models.tables.<domain>.record import Record


async def list_records(ctx: ServiceContext) -> dict:
    stmt = (
        select(Record)
        .where(
            Record.workspace_id == ctx.workspace_id,   # mandatory first filter
            Record.deleted_at.is_(None),
        )
        .order_by(Record.created_at.desc())
    )
    result = await ctx.session.execute(stmt)
    records = result.scalars().all()
    return {"records": [serialize_record_compact(r) for r in records]}
```

---

## What ServiceContext must NOT contain

| Anti-pattern | Why it is wrong | Correct approach |
|---|---|---|
| `prevent_event_bus: bool` | Business configuration leaking into transport | Command decides its own event behavior based on its inputs |
| `on_create_return: str` | Serialization strategy as a flag | Commands return explicit typed dicts; serializers are not configurable at the router |
| `check_workspace_id: bool` | Authorization policy as a flag | Commands and queries always enforce workspace scope — never conditionally |
| `inject_workspace_id: bool` | ORM mutation policy as a flag | Commands assign `workspace_id=ctx.workspace_id` explicitly — no injection helpers |
| `allow_is_system_modification: bool` | Privilege escalation as a flag | System-level modification is a separate command with its own authorization path |
| `touched_entities: dict` | Operation-local mutation tracking | Use `WorkContext` inside complex commands |
| `pending_events: list` | Command side-effect tracking | Use `WorkContext.events`, then emit after commit |
| `current_workspace: str` | Workspace-switching state as context | The JWT *is* the workspace session — switch by issuing a new token |

**The rule:** If a command needs to behave differently based on a flag, it is two commands. If the flag is set at the router, it is business logic at the wrong layer.

---

## Warnings

Warnings are non-fatal notices surfaced to the API client in the response body. Use them for degraded-but-successful outcomes:

```python
ctx.add_warning("Geocoding failed for address — order saved without coordinates.")
```

Do not use warnings for errors. If the operation cannot succeed, raise a `DomainError` subclass.
