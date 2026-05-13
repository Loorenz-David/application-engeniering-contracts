# 24 — Multi-Tenancy & Workspace Architecture

## The core concept

Every application built under this contract must be designed to work for **a single user or a thousand collaborating users with no architectural difference**. The mechanism that enables this is called a **workspace**.

A workspace is the isolated application instance. All domain data belongs to a workspace. A user belongs to workspaces through an explicit membership record. There is no cap on how many users a workspace can have, and no cap on how many workspaces a user can belong to.

This is the architecture that SaaS products like Slack, Notion, and Linear use. It is the correct default for any product that may need collaboration, even if it launches as a single-user tool.

---

## Why the naive `team_id` column approach fails

The anti-pattern is storing workspace membership directly on the `User` row:

```python
# ANTI-PATTERN — do not do this
class User(db.Model):
    team_id = Column(String(64), ForeignKey("teams.client_id"))
    role_id = Column(String(64), ForeignKey("roles.client_id"))  # global role on user
```

This breaks when:
- A user needs to be a member of more than one workspace
- A user is deactivated in one workspace but remains active in another
- You want to know which users belong to a workspace without scanning the users table
- A user has different roles in different workspaces

The role is a property of the **relationship** (user in workspace), not of the user alone. The correct pattern is a membership table. The user table knows nothing about which workspace the user is in — that lives in the JWT.

---

## The five tables

```
┌─────────────────────────────────────────────────────────────────────┐
│  GLOBAL TABLES (no workspace_id)                                    │
│                                                                     │
│  users              — identity: email, password, username           │
│  roles              — global permission tiers (ADMIN/MEMBER/FIELD) │
│                        each role owns UIGroupPermissions +          │
│                        BackendGroupPermissions — see 28_roles.md    │
│                                                                     │
│  WORKSPACE TABLES                                                   │
│                                                                     │
│  workspaces         — the isolated application instance             │
│  workspace_roles    — named roles within a workspace, each mapped   │
│                        to a global permission Role tier              │
│  workspace_memberships — user ↔ workspace ↔ workspace_role (join)  │
│                                                                     │
│  ALL DOMAIN TABLES                                                  │
│                                                                     │
│  cases / images / etc — workspace_id (not nullable, indexed)        │
└─────────────────────────────────────────────────────────────────────┘
```

All tables use `client_id` (prefixed ULID string) as the true primary key via `IdentityMixin`. See [40_identity.md](40_identity.md).

---

## Models

### `workspaces`

```python
# models/tables/workspaces/workspace.py
from datetime import datetime, timezone
from sqlalchemy import String, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class Workspace(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "ws"
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    default_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    subscription: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership", back_populates="workspace"
    )
    workspace_roles: Mapped[list["WorkspaceRole"]] = relationship(
        "WorkspaceRole", back_populates="workspace"
    )
```

### `workspace_roles`

Each workspace gets its own named role instances (e.g. "Operations Manager" → MEMBER tier, "Field Agent" → FIELD tier). Three system roles are auto-created with each workspace. The base `Role` owns the permission bundles — `WorkspaceRole` is a display-name alias pointing at it.

```python
# models/tables/workspaces/workspace_role.py
from sqlalchemy import String, Boolean, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class WorkspaceRole(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "wsr"
    __tablename__ = "workspace_roles"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True
    )
    role_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("roles.client_id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="workspace_roles")
    role: Mapped["Role"] = relationship("Role")
    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership", back_populates="workspace_role"
    )

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_roles_workspace_name"),
    )
```

Permission resolution at login: `membership.workspace_role.role` is the permission `Role` whose `UIGroupPermissions` and `BackendGroupPermissions` are walked by `resolve_permissions_for_role()`. See [28_roles_permissions.md](28_roles_permissions.md).

### `workspace_memberships`

The join table. The role is a property of the relationship, not of the user or the workspace alone.

```python
# models/tables/workspaces/workspace_membership.py
from datetime import datetime, timezone
from sqlalchemy import String, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from my_app.models import db
from my_app.models.base.identity import IdentityMixin


class WorkspaceMembership(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "wsm"
    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=False, index=True
    )
    workspace_role_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspace_roles.client_id"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    invited_by_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.client_id"), nullable=True
    )

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="memberships")
    user: Mapped["User"] = relationship(
        "User", foreign_keys=[user_id], back_populates="memberships"
    )
    workspace_role: Mapped["WorkspaceRole"] = relationship(
        "WorkspaceRole", back_populates="memberships"
    )
    invited_by: Mapped["User | None"] = relationship(
        "User", foreign_keys=[invited_by_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"
        ),
    )
```

A deactivated membership (`is_active=False`) blocks access without deleting history. The unique constraint ensures one active role per workspace per user — role changes are updates, not new rows.

### `users` — global, no workspace_id

```python
class User(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "usr"
    __tablename__ = "users"

    # No role_id column — role lives in WorkspaceMembership
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    password: Mapped[str] = mapped_column(String(255), nullable=False)
    ...

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership",
        foreign_keys="[WorkspaceMembership.user_id]",
        back_populates="user",
    )
```

**No `workspace_id` column on User.** A user's relationship to a workspace lives entirely in `WorkspaceMembership`.

---

## Registration — creating a user and their first workspace

```python
def register_user(ctx: ServiceContext) -> dict:
    user = User(email=..., username=..., password=hash_password(...))
    db.session.add(user)
    db.session.flush()

    workspace = Workspace(name=f"{user.username}'s Workspace", time_zone=...)
    db.session.add(workspace)
    db.session.flush()

    # Seed three system workspace_roles for this workspace
    admin_wsr, member_wsr, field_wsr = _seed_system_workspace_roles(workspace)
    db.session.flush()

    # Founding member — always the ADMIN system workspace_role
    membership = WorkspaceMembership(
        workspace_id=workspace.client_id,
        user_id=user.client_id,
        workspace_role_id=admin_wsr.client_id,
        invited_by_id=None,
    )
    db.session.add(membership)
    db.session.commit()

    return build_auth_response(user, workspace=workspace, membership=membership, app_scope="admin")


def _seed_system_workspace_roles(workspace: Workspace) -> tuple:
    """
    Create the three system WorkspaceRoles for a new workspace.
    Looks up the global permission Role rows by name.
    """
    from my_app.models.tables.roles.role import Role
    from my_app.domain.roles.enums import RoleNameEnum

    roles = {r.name: r for r in db.session.query(Role).all()}

    admin_wsr = WorkspaceRole(
        workspace_id=workspace.client_id,
        role_id=roles[RoleNameEnum.ADMIN].client_id,
        name="Admin", is_system=True,
    )
    member_wsr = WorkspaceRole(
        workspace_id=workspace.client_id,
        role_id=roles[RoleNameEnum.MEMBER].client_id,
        name="Member", is_system=True,
    )
    field_wsr = WorkspaceRole(
        workspace_id=workspace.client_id,
        role_id=roles[RoleNameEnum.FIELD].client_id,
        name="Field", is_system=True,
    )
    db.session.add_all([admin_wsr, member_wsr, field_wsr])
    return admin_wsr, member_wsr, field_wsr
```

---

## JWT — the workspace session

The JWT encodes a session scoped to one workspace. It carries all claims needed to authorize a request without a DB lookup.

```python
{
    "user_id":           "usr_01ARZ...",   # user.client_id
    "workspace_id":      "ws_01ARZ...",    # workspace.client_id
    "workspace_role_id": "wsr_01ARZ...",   # workspace_role.client_id
    "role_name":         "admin",          # permission Role.name.value — for @role_required
    "app_scope":         "admin",          # surface scope
    "time_zone":         "America/New_York",
    "backend_permissions": ["GET:/api/v1/cases/", ...],
    "ui": { "apps": [...], "pages": [...], "buttons": [...], ... },
}
```

Permission resolution at login walks `membership → workspace_role → role → permission groups`. See [28_roles_permissions.md](28_roles_permissions.md) for `resolve_permissions_for_role()`.

### Building the token

```python
def build_auth_response(
    user: User,
    *,
    workspace: Workspace,
    membership: WorkspaceMembership,
    app_scope: str,
) -> dict:
    workspace_role: WorkspaceRole = membership.workspace_role
    permission_role = workspace_role.role

    permissions = resolve_permissions_for_role(permission_role)

    claims = {
        "user_id":           user.client_id,
        "workspace_id":      workspace.client_id,
        "workspace_role_id": workspace_role.client_id,
        "role_name":         permission_role.name.value,
        "app_scope":         app_scope,
        "time_zone":         workspace.time_zone or "UTC",
        "backend_permissions": permissions["backend"],
        "ui":                  permissions["ui"],
    }

    access_token  = create_access_token(identity=user.client_id, additional_claims=claims)
    refresh_token = create_refresh_token(identity=user.client_id, additional_claims=claims)

    return {
        "access_token":   access_token,
        "_refresh_token": refresh_token,   # router sets as httpOnly cookie
        "user": {
            "id":                  user.client_id,
            "email":               user.email,
            "username":            user.username,
            "role":                workspace_role.name,   # display name
            "backend_permissions": permissions["backend"],
            "ui":                  permissions["ui"],
        },
        "workspace_id": workspace.client_id,
    }
```

---

## Workspace switching

A user with multiple memberships switches by calling the switch endpoint. The server validates the membership and issues a new JWT. No DB mutation — the token is the session.

```python
def switch_workspace(ctx: ServiceContext) -> dict:
    target_workspace_id = ctx.incoming_data["workspace_id"]

    membership = (
        db.session.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.user_id == ctx.user_id,
            WorkspaceMembership.workspace_id == target_workspace_id,
            WorkspaceMembership.is_active == True,
        )
        .one_or_none()
    )
    if membership is None:
        raise PermissionDenied("User is not an active member of the requested workspace.")

    workspace = db.session.get(Workspace, target_workspace_id)
    user = db.session.get(User, ctx.user_id)

    return build_auth_response(user, workspace=workspace, membership=membership, app_scope=ctx.app_scope)
```

---

## `ServiceContext` — workspace properties

`ctx.workspace_id` is the mandatory isolation property. Every command and query uses it.

```python
class ServiceContext:

    @property
    def user_id(self) -> str:
        return self._identity["user_id"]

    @property
    def workspace_id(self) -> str:
        """The active workspace. Use as the first filter on every domain query."""
        return self._identity["workspace_id"]

    @property
    def workspace_role_id(self) -> str:
        return self._identity["workspace_role_id"]

    @property
    def role_name(self) -> str:
        return self._identity["role_name"]
```

---

## Domain table scoping

Every domain table has `workspace_id`:

```python
class Case(IdentityMixin, db.Model):
    __tablename__ = "cases"

    workspace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("workspaces.client_id"), nullable=False, index=True
    )
    ...
```

Every command writes it from context, never from the request body:

```python
case = Case(
    workspace_id=ctx.workspace_id,   # from JWT — never from incoming_data
    created_by_id=ctx.user_id,
    ...
)
```

Every query filters by it as the **first condition**:

```python
cases = (
    db.session.query(Case)
    .filter(
        Case.workspace_id == ctx.workspace_id,   # mandatory — always first
        Case.deleted_at.is_(None),
    )
    .all()
)
```

---

## File structure

```
models/
└── tables/
    ├── workspaces/
    │   ├── __init__.py
    │   ├── workspace.py
    │   ├── workspace_role.py
    │   └── workspace_membership.py
    ├── roles/           # global base roles + permission tables — see 28_roles_permissions.md
    └── users/
        └── user.py      # no role_id column — role is in workspace_memberships
```

---

## Rules

| Rule | Reason |
|---|---|
| `workspace_id` written from `ctx.workspace_id` only | Prevents workspace spoofing via request body |
| First filter on every domain query is `workspace_id == ctx.workspace_id` | Prevents cross-workspace data leaks |
| No `workspace_id` on `users` or `roles` | These are global tables |
| No `role_id` directly on `User` | Role is a property of the membership, not the user |
| Workspace switching issues a new JWT — no DB mutation | Current workspace is a session concept, not a stored field |
| System workspace roles created in same transaction as workspace | Workspace is never in an incomplete state |
| `invited_by_id` on membership — nullable for founding member | First user has no inviter |
| A deactivated membership blocks access without deleting history | `is_active=False` pattern, same as soft delete |

---

## What must NEVER happen

| Violation | Risk |
|---|---|
| `workspace_id` read from `ctx.incoming_data` | Workspace spoofing |
| `workspace_id` stored on the `User` row | Caps collaboration, concurrency bugs |
| Workspace filter missing from a domain query | Cross-workspace data leak |
| `role_id` stored directly on `User` | User has one global role — wrong model |
| Issuing a JWT with a `workspace_id` the user is not a member of | Full privilege escalation |
| Storing current workspace as a DB column on User | Concurrency bugs with concurrent sessions |
