# 28 — Roles & Permissions Contract

## What changed

The old system stored permissions as a flat JSONB list of `feature:action` strings on a `WorkspaceRole` row. The new system is **fully relational and data-driven**:

- Permissions are rows in the database, not enum values in code.
- UI permissions and backend permissions are separated into independent hierarchies.
- A `Role` is assigned one `UIGroupPermissions` bundle and one `BackendGroupPermissions` bundle.
- Backend permissions are **endpoint-level** — specific HTTP method + path combinations.
- UI permissions are **element-level** — specific apps, pages, buttons, actions, and query filters.

---

## Architecture

```
Role
 ├── UIGroupPermissions ─────────────────────────────────────────────┐
 │     ├── AppPermission (junction) → AppName                         │
 │     ├── PagePermission (junction) → PageName                       │  UI
 │     ├── ButtonPermission (junction) → ButtonName                   │  layer
 │     ├── ActionPermission (junction) → ActionName                   │
 │     └── QueryFilterPermission (junction) → QueryFilter             │
 │                                                                    ┘
 └── BackendGroupPermissions ────────────────────────────────────────┐
       └── BackendPermission (junction) → Endpoints (method + path)   │  Backend
                                                                      ┘  layer
```

At login time, both trees are resolved from the DB and embedded in the JWT so no permission DB lookup is required per request.

---

## Enums

```python
# models/tables/roles/enums.py
import enum


class RoleNameEnum(enum.Enum):
    """
    Application-defined role names. Rename/add values to match your domain.
    The first value in seed order is always the highest-privilege role.
    """
    ADMIN = "admin"
    MEMBER = "member"
    FIELD = "field"       # rename: DRIVER, TECHNICIAN, AGENT, CLIENT, etc.


class HttpMethodEnum(enum.Enum):
    GET = "GET"
    POST = "POST"
    PATCH = "PATCH"
    DELETE = "DELETE"
```

---

## Models

All addressable models in this system inherit from `IdentityMixin` (`models/base/identity.py`), which provides `client_id` as the primary key. See [03_models.md](03_models.md).

### Core role tables

```python
# models/tables/roles/role.py
from sqlalchemy import String, ForeignKey
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin
from .enums import RoleNameEnum


class Role(IdentityMixin, db.Model):
    __tablename__ = "roles"

    name: Mapped[RoleNameEnum] = mapped_column(
        SAEnum(RoleNameEnum, name="role_name_enum", create_type=True),
        nullable=False,
        index=True,
    )

    ui_group_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("ui_group_permissions.client_id"), nullable=True
    )
    backend_group_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("backend_group_permissions.client_id"), nullable=True
    )

    ui_permissions: Mapped["UIGroupPermissions | None"] = relationship(
        "UIGroupPermissions", foreign_keys=[ui_group_id]
    )
    backend_permissions: Mapped["BackendGroupPermissions | None"] = relationship(
        "BackendGroupPermissions", foreign_keys=[backend_group_id]
    )
```

`Role` is a global permission tier. It is never assigned directly to `User`. A user receives permissions only through the active workspace membership path: `WorkspaceMembership.workspace_role -> WorkspaceRole.role -> Role`.

```python
# models/tables/roles/ui_group_permissions.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class UIGroupPermissions(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "uig"
    __tablename__ = "ui_group_permissions"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    app_permissions: Mapped[list["AppPermission"]] = relationship(
        "AppPermission", back_populates="ui_group"
    )
    page_permissions: Mapped[list["PagePermission"]] = relationship(
        "PagePermission", back_populates="ui_group"
    )
    button_permissions: Mapped[list["ButtonPermission"]] = relationship(
        "ButtonPermission", back_populates="ui_group"
    )
    action_permissions: Mapped[list["ActionPermission"]] = relationship(
        "ActionPermission", back_populates="ui_group"
    )
    query_filter_permissions: Mapped[list["QueryFilterPermission"]] = relationship(
        "QueryFilterPermission", back_populates="ui_group"
    )
```

```python
# models/tables/roles/backend_group_permissions.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db


from my_app.models.base.identity import IdentityMixin


class BackendGroupPermissions(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "bkg"
    __tablename__ = "backend_group_permissions"

    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)

    backend_permissions: Mapped[list["BackendPermission"]] = relationship(
        "BackendPermission", back_populates="backend_group"
    )
```

### Junction tables

```python
# models/tables/roles/backend_permission.py
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db


from my_app.models.base.identity import IdentityMixin


class BackendPermission(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "bkp"
    __tablename__ = "backend_permissions"

    backend_group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("backend_group_permissions.client_id"), nullable=False, index=True
    )
    endpoint_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("endpoints.client_id"), nullable=False, index=True
    )

    backend_group: Mapped["BackendGroupPermissions"] = relationship(
        "BackendGroupPermissions", back_populates="backend_permissions"
    )
    endpoint: Mapped["Endpoints"] = relationship("Endpoints")
```

```python
# models/tables/roles/app_permission.py
from sqlalchemy import String, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db


from my_app.models.base.identity import IdentityMixin


class AppPermission(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "app"
    __tablename__ = "app_permissions"

    ui_group_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ui_group_permissions.client_id"), nullable=False, index=True
    )
    app_name_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("app_names.client_id"), nullable=False, index=True
    )

    ui_group: Mapped["UIGroupPermissions"] = relationship(
        "UIGroupPermissions", back_populates="app_permissions"
    )
    app_name: Mapped["AppName"] = relationship("AppName")
```

`PagePermission`, `ButtonPermission`, `ActionPermission`, and `QueryFilterPermission` follow the identical pattern — each uses `IdentityMixin`, a `ui_group_id` FK to `ui_group_permissions.client_id`, and the respective name table FK to `<name_table>.client_id`.

### Name / seed tables

These tables hold the defined permission atoms. Rows are seeded at deployment, not created at runtime.

```python
# models/tables/roles/names/app_name.py
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from my_app.models import db


from my_app.models.base.identity import IdentityMixin


class AppName(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "apn"
    __tablename__ = "app_names"

    app: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
```

`PageName`, `ButtonName`, `ActionName`, and `QueryFilter` follow the same pattern with their domain field (`page`, `button`, `action`, `filter`).

```python
# models/tables/roles/names/endpoints.py
from sqlalchemy import String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column
from my_app.models import db
from my_app.models.tables.roles.enums import HttpMethodEnum


from my_app.models.base.identity import IdentityMixin


class Endpoints(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "endp"
    __tablename__ = "endpoints"

    endpoint: Mapped[str] = mapped_column(String(255), nullable=False)
    method: Mapped[HttpMethodEnum] = mapped_column(
        SAEnum(HttpMethodEnum, name="http_method_enum", create_type=True),
        nullable=False,
    )

    from sqlalchemy import UniqueConstraint
    __table_args__ = (
        UniqueConstraint("endpoint", "method", name="uq_endpoints_endpoint_method"),
    )
```

---

## Seeding permission atoms

Name tables and endpoints are seeded once at deployment. Use a migration seed or a CLI command:

```python
# services/commands/seed/seed_permissions.py
from my_app.models import db
from my_app.models.tables.roles.names.app_name import AppName
from my_app.models.tables.roles.names.page_name import PageName
from my_app.models.tables.roles.names.button_name import ButtonName
from my_app.models.tables.roles.names.action_name import ActionName
from my_app.models.tables.roles.names.query_filter import QueryFilter
from my_app.models.tables.roles.names.endpoints import Endpoints
from my_app.models.tables.roles.enums import HttpMethodEnum


def seed_permission_atoms() -> None:
    """
    Idempotent. Safe to run multiple times — inserts only missing rows.
    Define all apps, pages, buttons, actions, query filters, and endpoints for this application.
    """
    _seed_apps(["admin_app", "field_app"])

    _seed_pages(["dashboard", "records", "members", "settings", "reports"])

    _seed_buttons(["btn_create_record", "btn_delete_record", "btn_export", "btn_invite_member"])

    _seed_actions(["action_export_csv", "action_bulk_update", "action_archive"])

    _seed_query_filters(["filter_by_status", "filter_by_date", "filter_by_assignee"])

    _seed_endpoints([
        ("GET",    "/api/v1/records/"),
        ("POST",   "/api/v1/records/"),     # PUT is used for create per contract 09
        ("PATCH",  "/api/v1/records/<client_id>"),
        ("DELETE", "/api/v1/records/<client_id>"),
        ("GET",    "/api/v1/members/"),
        ("POST",   "/api/v1/auth/sign-in"),
    ])

    db.session.commit()


def _seed_apps(apps: list[str]) -> None:
    existing = {r.app for r in db.session.query(AppName).all()}
    for app in apps:
        if app not in existing:
            db.session.add(AppName(app=app))


def _seed_endpoints(pairs: list[tuple[str, str]]) -> None:
    existing = {
        (r.method.value, r.endpoint)
        for r in db.session.query(Endpoints).all()
    }
    for method_str, path in pairs:
        if (method_str, path) not in existing:
            db.session.add(Endpoints(
                endpoint=path,
                method=HttpMethodEnum(method_str),
            ))

# _seed_pages, _seed_buttons, _seed_actions, _seed_query_filters follow same pattern
```

**Rules:**
- Seed functions are idempotent — check existence before inserting.
- Run as a CLI script (`python scripts/seed/seed_permissions.py`) after every migration that adds new atoms.
- Never create permission atom rows at request time. They are seeded infrastructure, not user data.

---

## Resolving permissions at login

At login, the full permission set is resolved from the DB once and embedded in the JWT. No permission DB lookup on subsequent requests.

```python
# services/commands/auth/sign_in_user.py

def resolve_permissions_for_role(role: "Role") -> dict:
    """
    Walk the role's permission groups and build the JWT permission payload.
    Called once at login; result embedded in the JWT.
    """
    ui: dict = {
        "apps": [],
        "pages": [],
        "buttons": [],
        "actions": [],
        "query_filters": [],
    }
    backend: list[str] = []

    if role.ui_permissions:
        ui["apps"]          = [p.app_name.app for p in role.ui_permissions.app_permissions]
        ui["pages"]         = [p.page_name.page for p in role.ui_permissions.page_permissions]
        ui["buttons"]       = [p.button_name.button for p in role.ui_permissions.button_permissions]
        ui["actions"]       = [p.action_name.action for p in role.ui_permissions.action_permissions]
        ui["query_filters"] = [p.query_filter.filter for p in role.ui_permissions.query_filter_permissions]

    if role.backend_permissions:
        backend = [
            f"{p.endpoint.method.value}:{p.endpoint.endpoint}"
            for p in role.backend_permissions.backend_permissions
        ]

    return {"ui": ui, "backend": backend}
```

```python
def build_auth_response(user, *, workspace, membership: "WorkspaceMembership", app_scope: str) -> dict:
    workspace_role = membership.workspace_role
    permission_role = workspace_role.role
    permissions = resolve_permissions_for_role(permission_role)

    claims = {
        "user_id":           user.client_id,
        "workspace_id":      workspace.client_id,
        "workspace_role_id": workspace_role.client_id,
        "role_name":         permission_role.name.value,
        "app_scope":         app_scope,
        "time_zone":         workspace.time_zone or "UTC",
        "backend_permissions": permissions["backend"],   # ["GET:/api/v1/records/", ...]
        "ui":            permissions["ui"],              # {"apps": [...], "pages": [...], ...}
    }

    access_token  = create_access_token(identity=user.client_id, additional_claims=claims)
    refresh_token = create_refresh_token(identity=user.client_id, additional_claims=claims)

    return {
        "access_token":   access_token,
        "_refresh_token": refresh_token,
        "user": {
            "id":                 user.client_id,
            "email":              user.email,
            "name":               user.name,
            "role":               workspace_role.name,
            "backend_permissions": permissions["backend"],
            "ui":                  permissions["ui"],
        },
    }
```

---

## Backend permission enforcement

### Per-request middleware

Register FastAPI middleware that checks every API route against the JWT's `backend_permissions` list.

```python
# routers/middleware/backend_permission.py
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class BackendPermissionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return await call_next(request)

        import jwt
        from my_app.config import settings
        try:
            claims = jwt.decode(
                auth_header.removeprefix("Bearer "),
                settings.jwt_secret_key,
                algorithms=["HS256"],
            )
        except jwt.PyJWTError:
            return await call_next(request)

        allowed = set(claims.get("backend_permissions", []))
        key = f"{request.method}:{request.url.path}"
        normalized = _normalize_api_path(key)

        if normalized not in allowed:
            return JSONResponse(
                status_code=403,
                content={"error": "Your role does not have access to this endpoint."},
            )

        return await call_next(request)
```

> **Note on path matching:** The `request.path` for a route like `/api/v1/records/rec_01...` will not match the seeded pattern `/api/v1/records/<client_id>`. Resolve this by normalizing the path before lookup — replace `client_id`-shaped segments with `<client_id>`. Implement a `normalize_api_path(path: str) -> str` helper in `routers/utils/` that strips dynamic segments.

Register it in `create_app()`:

```python
from my_app.routers.middleware.backend_permission import BackendPermissionMiddleware
app.add_middleware(BackendPermissionMiddleware)
```

### Route-level role dependencies

Use `require_roles([ADMIN, MEMBER])` as a `Depends()` in the route signature — see [10_auth.md](10_auth.md) for the full dependency definition.

```python
@router.get("/")
async def list_records_route(
    claims: dict = Depends(require_roles([ADMIN, MEMBER])),
):
    ...
```

---

## UI permission — frontend contract

The `ui` object in the JWT and API response is consumed directly by the frontend. Keys map to frontend UI element categories.

```json
{
  "ui": {
    "apps":          ["admin_app"],
    "pages":         ["dashboard", "records", "members"],
    "buttons":       ["btn_create_record", "btn_export"],
    "actions":       ["action_export_csv"],
    "query_filters": ["filter_by_status", "filter_by_date"]
  }
}
```

The frontend `can()` / `hasAccess()` hook checks membership in these arrays. String values must match exactly what is seeded in the name tables — they are the contract between backend and frontend.

---

## File structure

```
models/
└── tables/
    └── roles/
        ├── enums.py                         # RoleNameEnum, HttpMethodEnum
        ├── role.py                          # Role
        ├── ui_group_permissions.py          # UIGroupPermissions
        ├── backend_group_permissions.py     # BackendGroupPermissions
        ├── backend_permission.py            # junction: BackendGroup → Endpoint
        ├── app_permission.py                # junction: UIGroup → AppName
        ├── page_permission.py               # junction: UIGroup → PageName
        ├── button_permission.py             # junction: UIGroup → ButtonName
        ├── action_permission.py             # junction: UIGroup → ActionName
        ├── query_filter_permission.py       # junction: UIGroup → QueryFilter
        └── names/
            ├── app_name.py
            ├── page_name.py
            ├── button_name.py
            ├── action_name.py
            ├── query_filter.py
            └── endpoints.py

services/
└── commands/
    └── seed/
        └── seed_permissions.py              # idempotent permission atom seeder
```

---

## Checklist for a new application

- [ ] Define `RoleNameEnum` values for this application's role types
- [ ] Define `HttpMethodEnum` (already provided — extend only if needed)
- [ ] Seed `AppName`, `PageName`, `ButtonName`, `ActionName`, `QueryFilter`, `Endpoints` rows
- [ ] Create `UIGroupPermissions` and `BackendGroupPermissions` records for each role type
- [ ] Create junction rows linking each group to its permitted atoms
- [ ] Create `Role` rows linking each role to its UI and backend permission groups
- [ ] Verify `resolve_permissions_for_role()` returns the correct sets for each role
- [ ] Register backend permission middleware in `_register_middleware`
- [ ] Implement `normalize_api_path()` helper for dynamic-segment routes
- [ ] Confirm `role_name` strings in the JWT match the `RoleNameEnum` values the decorator checks
- [ ] Confirm `ui.*` string values match the frontend's permission key constants exactly
