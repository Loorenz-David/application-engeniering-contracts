# 09 — Router Contract

## Minimum skeleton — copy this, never read another router as a template

```python
# routers/api_v1/<domain>.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from my_app.models.database import get_db
from my_app.routers.http.response import build_err, build_ok
from my_app.routers.utils.jwt_dep import require_roles
from my_app.routers.utils.roles import ADMIN, MEMBER
from my_app.services.commands.<domain>.create_record import create_record
from my_app.services.context import ServiceContext
from my_app.services.run_service import run_service

router = APIRouter()


class RecordCreateBody(BaseModel):
    name: str


@router.post("")
async def create_record_route(
    body:    RecordCreateBody,
    claims:  dict         = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data=body.model_dump(),
        identity=claims,
        session=session,
    )
    outcome = await run_service(create_record, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data)
```

Register this router in `routers/api_v1/__init__.py`:

```python
from .record import router as record_router
app.include_router(record_router, prefix="/api/v1/records", tags=["records"])
```

Add fields to `RecordCreateBody` and swap the command import — do not change the handler shape.

---

## What routers own

A route handler does exactly these steps, in this order:

1. Receive and validate the request body (Pydantic `BaseModel`) or query parameters
2. Inject JWT claims via `Depends(require_roles([...]))` or `Depends(get_jwt_claims)`
3. Inject the DB session via `Depends(get_db)`
4. Build a `ServiceContext`
5. Call `await run_service(command_or_query, ctx)`
6. Check `outcome.success` and return the appropriate HTTP response

That is the entire job. No business logic. No ORM calls. No domain decisions.

---

## File structure

One router module per domain resource:

```
routers/
├── http/
│   └── response.py          # build_ok(), build_err()
├── utils/
│   ├── jwt_dep.py           # get_jwt_claims, require_roles, require_app_scope
│   └── roles.py             # ADMIN, MEMBER, FIELD constants
└── api_v1/
    ├── __init__.py           # register_v1_routers(app)
    ├── auth.py              # sign-in, logout, token refresh — auth flows only
    ├── users.py             # user CRUD, profile, registration
    ├── cases.py             # case domain
    ├── events.py            # event domain
    └── ...                  # one file per domain resource
```

### Router domain ownership

Each router file owns one domain. **Do not add routes to an existing router because it is convenient or nearby.** The question to ask is: "what resource does this operation act on?"

| Domain | Router file | Prefix | What belongs here |
|---|---|---|---|
| Auth flows | `auth.py` | `/api/v1/auth` | sign-in, logout, refresh — operations on tokens and sessions |
| Users | `users.py` | `/api/v1/users` | register, get profile, update profile, list users |
| Cases | `cases.py` | `/api/v1/cases` | CRUD on case entities |
| Events | `events.py` | `/api/v1/events` | CRUD on event entities |

**The auth router does not own user registration.** Registration creates a `User` record — it belongs in `users.py`. The auth router owns operations on auth tokens (sign-in, logout, refresh). If registration and sign-in are both needed at launch, register them in their correct routers from the start.

Anti-pattern: adding `/auth/register` because the register command imports auth helpers or because the auth router was open at the time. The route path and router file both declare the domain — keep them consistent.

---

## Router registration

```python
# routers/api_v1/__init__.py
from fastapi import FastAPI
from .record   import router as record_router
from .category import router as category_router
from .auth     import router as auth_router


def register_v1_routers(app: FastAPI) -> None:
    app.include_router(record_router,   prefix="/api/v1/records",    tags=["records"])
    app.include_router(category_router, prefix="/api/v1/categories", tags=["categories"])
    app.include_router(auth_router,     prefix="/api/v1/auth",       tags=["auth"])
```

All routers are registered in one place. The app factory calls `register_v1_routers(app)`.

---

## Standard route handler

```python
# routers/api_v1/record.py
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from my_app.models.database import get_db
from my_app.routers.http.response import build_ok, build_err
from my_app.routers.utils.jwt_dep import require_roles
from my_app.routers.utils.roles import ADMIN, MEMBER
from my_app.services.context import ServiceContext
from my_app.services.run_service import run_service
from my_app.services.commands.record.create_record import create_record
from my_app.services.queries.record.list_records import list_records

router = APIRouter()


class RecordCreateBody(BaseModel):
    client_id:   str | None = None
    name:        str
    category_id: str | None = None


@router.get("/")
async def list_records_route(
    claims:       dict         = Depends(require_roles([ADMIN, MEMBER])),
    session:      AsyncSession = Depends(get_db),
    limit:        int          = Query(50, le=200),
    after_cursor: str | None   = Query(None),
):
    ctx = ServiceContext(
        query_params={"limit": limit, "after_cursor": after_cursor},
        identity=claims,
        session=session,
    )
    outcome = await run_service(list_records, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data, warnings=ctx.warnings)


@router.put("/")
async def create_record_route(
    body:    RecordCreateBody,
    claims:  dict         = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data=body.model_dump(),
        identity=claims,
        session=session,
    )
    outcome = await run_service(create_record, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data, warnings=ctx.warnings)
```

## Route declaration order

FastAPI matches routes in declaration order. **Static paths and collection-level routes must always be declared before wildcard path-parameter routes** (`/{id}`). A static segment like `/unread-counts` or `/links` will be silently captured as a path parameter value if a wildcard route is declared first.

Correct order for a resource router:

```python
@router.post("")          # collection create
@router.get("")           # collection list
@router.get("/unread-counts")   # static sub-path — must come BEFORE /{id}
@router.delete("/links")        # static sub-path — must come BEFORE /{id}
@router.post("/reorder")        # static sub-path — must come BEFORE /{id}
@router.get("/{resource_id}")   # wildcard — declared LAST among single-segment routes
@router.patch("/{resource_id}")
@router.delete("/{resource_id}")
@router.get("/{resource_id}/sub-resource")  # multi-segment wildcards are safe after
```

Routes with a multi-segment static prefix (e.g. `/conversations/{id}/messages`) do not conflict with `/{id}` and can appear anywhere, but are conventionally grouped after the single-segment wildcard routes.

## Path parameter routes

URL path parameters are **merged into `ctx.incoming_data`** before calling `run_service`. Path params are logically part of the command or query input and are owned by the service layer, not the router.

```python
@router.get("/{record_client_id}")
async def get_record_route(
    record_client_id: str,
    claims:  dict         = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=claims,
        session=session,
    )
    outcome = await run_service(get_record, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data, warnings=ctx.warnings)


@router.delete("/{record_client_id}")
async def delete_record_route(
    record_client_id: str,
    claims:  dict         = Depends(require_roles([ADMIN])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id},
        identity=claims,
        session=session,
    )
    outcome = await run_service(delete_record, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data, warnings=ctx.warnings)
```

When a route has both a path parameter and a JSON body (e.g., PATCH), merge them:

```python
class RecordUpdateBody(BaseModel):
    name:   str | None = None
    status: str | None = None


@router.patch("/{record_client_id}")
async def update_record_route(
    record_client_id: str,
    body:    RecordUpdateBody,
    claims:  dict         = Depends(require_roles([ADMIN, MEMBER])),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        incoming_data={"client_id": record_client_id, **body.model_dump(exclude_none=True)},
        identity=claims,
        session=session,
    )
    outcome = await run_service(update_record, ctx)
    if not outcome.success:
        return build_err(outcome.error)
    return build_ok(outcome.data, warnings=ctx.warnings)
```

The command reads `client_id` from `ctx.incoming_data` via its request parser, exactly as it reads any other field.

---

## Response builder

```python
# routers/http/response.py
from fastapi.responses import JSONResponse
from my_app.errors import DomainError, NotFound, PermissionDenied, ValidationFailed, Conflict

_STATUS_MAP: dict[type[DomainError], int] = {
    NotFound:        404,
    PermissionDenied: 403,
    ValidationFailed: 400,
    Conflict:        409,
    DomainError:     500,
}


def build_ok(
    payload:  object,
    warnings: list[str] | None = None,
    status:   int = 200,
) -> JSONResponse:
    return JSONResponse(
        content={"data": payload, "warnings": warnings or []},
        status_code=status,
    )


def build_err(error: DomainError) -> JSONResponse:
    http_status = _STATUS_MAP.get(type(error), _STATUS_MAP[DomainError])
    return JSONResponse(
        content={"error": error.message, "code": error.code},
        status_code=http_status,
    )
```

Two functions, not a class.

---

## Cookie responses

When a route must set or delete a cookie (e.g., refresh token), inject a `Response` parameter:

```python
from fastapi import Response


@router.post("/sign-in")
async def sign_in_route(
    body:     SignInBody,
    response: Response,
    session:  AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(incoming_data=body.model_dump(), identity={}, session=session)
    outcome = await run_service(sign_in_user, ctx)
    if not outcome.success:
        return build_err(outcome.error)

    data = dict(outcome.data)
    refresh_token = data.pop("_refresh_token")

    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=30 * 24 * 60 * 60,
    )
    return build_ok(data)
```

---

## `run_service`

```python
# services/run_service.py
from my_app.services.context import ServiceContext
from my_app.services.outcome import Outcome
from my_app.errors import DomainError
import logging

logger = logging.getLogger(__name__)


async def run_service(fn, ctx: ServiceContext) -> Outcome:
    try:
        result = await fn(ctx)
        return Outcome.ok(result)
    except DomainError as e:
        return Outcome.err(e)
    except Exception as e:
        logger.exception("Unhandled error in %s", fn.__name__)
        from my_app.errors import DomainError as GenericError
        return Outcome.err(GenericError(str(e)))
```

Pass the function directly — never `lambda c: fn(c)`.

---

## Rules

- `await run_service(fn, ctx)` — pass the function directly.
- `build_ok` and `build_err` are module-level functions from `routers/http/response.py`, not a class instantiated per request.
- No business data is extracted or modified in the router. `incoming_data` goes directly into the context untouched.
- Do not pop flags out of `incoming_data` in the router. The command is responsible for interpreting its own input.
- URL path parameters are the only data the router merges into `incoming_data`. All other data comes from the Pydantic body model or query parameters.
- Public resource path parameters are `client_id` values. Do not introduce internal integer IDs in public routes.
- Declare request body Pydantic models in the router file (unless shared across multiple routes, in which case extract to `routers/schemas/<domain>.py`).

---

## HTTP method conventions

| Intent | Method | URL pattern |
|---|---|---|
| List resources | GET | `/api/v1/records/` |
| Get one resource | GET | `/api/v1/records/{record_client_id}` |
| Create | PUT | `/api/v1/records/` |
| Update (partial) | PATCH | `/api/v1/records/{record_client_id}` |
| Delete | DELETE | `/api/v1/records/{record_client_id}` |
| Sub-resource action | PATCH | `/api/v1/records/{record_client_id}/archive` |

---

## What the router must NOT do

- Call `ctx.session` directly (only routes that call `run_service` indirectly touch the session)
- Call domain functions directly
- Inspect the payload for business flags (`if "prevent_event_bus" in data`)
- Return different shapes based on business conditions
- Instantiate ORM models
- Perform data transformations beyond merging path params into `incoming_data`
