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
    team_id = Column(Integer, ForeignKey("teams.id"))        # home workspace
    team_workspace_team_id = Column(Integer, nullable=True)  # "invited to another team"
    admin_app_current_workspace = Column(String)             # "personal" | "team"
```

This breaks when:
- A user needs to be a member of more than 2 workspaces
- Workspace switching state is stored in the DB (concurrency issues) instead of in the session token
- A user is deactivated in one workspace but remains active in another
- You want to know which users belong to a workspace without scanning the users table

The correct pattern is a **membership table**. The join table is the relationship. The user table knows nothing about which workspace the user is currently "in" — that lives in the JWT.

---

## The five tables

```
┌─────────────────────────────────────────────────────────────────────┐
│  GLOBAL TABLES (no workspace_id)                                    │
│                                                                     │
│  users              — identity: email, password, name, avatar       │
│  base_roles         — permission tiers: ADMIN / MEMBER / FIELD      │
│                                                                     │
│  WORKSPACE TABLES                                                   │
│                                                                     │
│  workspaces         — the isolated application instance             │
│  workspace_roles    — named roles within a workspace (custom names) │
│  workspace_memberships — user ↔ workspace ↔ role (the join)        │
│                                                                     │
│  ALL DOMAIN TABLES                                                  │
│                                                                     │
│  orders / plans / etc — workspace_id (not nullable, indexed)        │
└─────────────────────────────────────────────────────────────────────┘
```

### `users` — global identity

```python
class User(db.Model):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    profile_picture: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership", back_populates="user", lazy="select"
    )
```

**No `workspace_id` column on User.** A user's relationship to a workspace lives entirely in `WorkspaceMembership`.

### `base_roles` — global permission tiers

```python
class BaseRole(db.Model):
    __tablename__ = "base_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Seed data — three rows, created once, never modified:

| id | name | Description |
|---|---|---|
| 1 | ADMIN | Full access — manages workspace, users, and all domain data |
| 2 | MEMBER | Standard collaborator — can read and write domain data |
| 3 | FIELD | Restricted/mobile user — rename to fit domain (DRIVER, AGENT, OPERATOR) |

**`base_roles` is a global table. No `workspace_id`. Never team-scoped.**

### `workspaces` — the isolated application instance

```python
class Workspace(db.Model):
    __tablename__ = "workspaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    default_country_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    subscription: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    memberships: Mapped[list["WorkspaceMembership"]] = relationship(
        "WorkspaceMembership", back_populates="workspace", lazy="select"
    )
    roles: Mapped[list["WorkspaceRole"]] = relationship(
        "WorkspaceRole", back_populates="workspace", lazy="select"
    )
```

### `workspace_roles` — named roles within a workspace

```python
class WorkspaceRole(db.Model):
    __tablename__ = "workspace_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)

    base_role_id: Mapped[int] = mapped_column(Integer, ForeignKey("base_roles.id"), nullable=False)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="roles", lazy="select")
    base_role: Mapped["BaseRole"] = relationship("BaseRole", lazy="select")

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_roles_workspace_name"),
    )
```

Each workspace gets its own role instances (e.g., "Operations Manager" maps to MEMBER tier). Three system roles are auto-created with each workspace. Admins can create custom roles on top of the system ones.

### `workspace_memberships` — the join table

```python
class WorkspaceMembership(db.Model):
    __tablename__ = "workspace_memberships"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    workspace_role_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspace_roles.id"), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    invited_by_user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True)

    workspace: Mapped["Workspace"] = relationship("Workspace", back_populates="memberships", lazy="select")
    user: Mapped["User"] = relationship("User", foreign_keys=[user_id], back_populates="memberships", lazy="select")
    workspace_role: Mapped["WorkspaceRole"] = relationship("WorkspaceRole", lazy="select")

    __table_args__ = (
        UniqueConstraint("workspace_id", "user_id", name="uq_workspace_memberships_workspace_user"),
    )
```

A deactivated membership (`is_active=False`) blocks access without deleting history. The constraint ensures a user can only have one active role per workspace (promoted via update, not new row).

---

## Registration — the solo user path

When a user registers, the system creates a workspace for them automatically and makes them the ADMIN. This is the same code path whether the product will ever have 1 user or 100.

```python
def register_user(ctx: ServiceContext) -> dict:
    request = parse_register_user_request(ctx.incoming_data)

    _assert_email_not_taken(request.email)
    _assert_username_not_taken(request.username)

    pending_events: list[dict] = []

    with db.session.begin():
        user = User(
            client_id=generate_client_id(),
            email=request.email,
            username=request.username,
            password_hash=hash_password(request.password),
        )
        db.session.add(user)
        db.session.flush()  # get user.id

        workspace = Workspace(
            client_id=generate_client_id(),
            name=request.workspace_name or f"{request.username}'s Workspace",
            time_zone=request.time_zone or "UTC",
            default_country_code=request.default_country_code,
        )
        db.session.add(workspace)
        db.session.flush()  # get workspace.id

        # Seed the three system roles for this workspace
        admin_role, member_role, field_role = _seed_system_roles(workspace)
        db.session.flush()

        # Founding member — always ADMIN
        membership = WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            workspace_role_id=admin_role.id,
            invited_by_user_id=None,
        )
        db.session.add(membership)

        pending_events.append(build_workspace_created_event(workspace, user))

    emit_workspace_events(ctx, pending_events)

    tokens = build_user_tokens(user, workspace=workspace, membership=membership)
    return {"tokens": tokens, "workspace": serialize_workspace(workspace)}
```

---

## The invitation flow

Users are added to a workspace through invitations. The flow:

1. An ADMIN sends an invite specifying the target email and role.
2. A `workspace_invitations` row is created (`status=pending`, `token=UUID`).
3. An email is sent with the invitation link (contains the token — do not embed the URL).
4. The target user follows the link:
   - If they have an account → accept endpoint creates `WorkspaceMembership`
   - If new → registration form → creates `User` + `WorkspaceMembership` in one transaction
5. The invitation row is marked `status=accepted`.

```python
class WorkspaceInvitation(db.Model):
    __tablename__ = "workspace_invitations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspaces.id"), nullable=False, index=True)
    invited_by_user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    target_email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    workspace_role_id: Mapped[int] = mapped_column(Integer, ForeignKey("workspace_roles.id"), nullable=False)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")  # pending | accepted | expired | cancelled
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
```

**Rules:**
- Store `workspace_role_id`, not a role name string — the role name can change, the ID is stable.
- `token` is a UUID — never a sequential ID. The token is the authorization mechanism.
- `expires_at` is required. Invitations older than 7 days are expired automatically.
- Never re-use an invitation token. Resend = new token, old one cancelled.

---

## JWT — the workspace session

The JWT encodes a session scoped to one workspace. It is the source of truth for who the user is and which workspace they are currently operating in.

```python
{
    "user_id": 42,
    "workspace_id": 7,          # the active workspace
    "workspace_role_id": 12,    # the user's role in this workspace
    "base_role_id": 1,          # ADMIN=1, MEMBER=2, FIELD=3 — for fast permission checks
    "app_scope": "admin",       # surface scope, e.g. "admin" | "mobile" | "client"
    "time_zone": "America/New_York",
}
```

**Key changes from the naive approach:**
- `workspace_id` replaces `team_id` and `active_team_id` — there is one concept, one field.
- No `has_team_workspace`, `current_workspace`, or `primals_team_id` in the token. Workspace context is resolved once at login, not re-derived from flags.

### Building the token

```python
def build_user_tokens(
    user: User,
    *,
    workspace: Workspace,
    membership: WorkspaceMembership,
    app_scope: str,
    time_zone: str | None = None,
) -> dict:
    claims = {
        "user_id": user.id,
        "workspace_id": workspace.id,
        "workspace_role_id": membership.workspace_role_id,
        "base_role_id": membership.workspace_role.base_role_id,
        "app_scope": app_scope,
        "time_zone": time_zone or workspace.time_zone or "UTC",
    }
    access_token = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)
    return {"access_token": access_token, "refresh_token": refresh_token}
```

The membership is loaded from the database at login time. Everything the token needs is already in the membership row and the workspace row — no resolution logic, no flags.

---

## Workspace switching

A user with multiple memberships switches workspaces by calling the switch endpoint. The server validates the membership, issues a new JWT, and the client discards the old one.

```python
def switch_workspace(ctx: ServiceContext) -> dict:
    request = parse_switch_workspace_request(ctx.incoming_data)

    membership = (
        db.session.query(WorkspaceMembership)
        .join(WorkspaceMembership.workspace_role)
        .filter(
            WorkspaceMembership.user_id == ctx.user_id,
            WorkspaceMembership.workspace_id == request.workspace_id,
            WorkspaceMembership.is_active == True,
        )
        .options(selectinload(WorkspaceMembership.workspace_role))
        .first()
    )
    if membership is None:
        raise PermissionDenied("User is not an active member of the requested workspace.")

    workspace = db.session.get(Workspace, request.workspace_id)
    user = db.session.get(User, ctx.user_id)

    tokens = build_user_tokens(
        user,
        workspace=workspace,
        membership=membership,
        app_scope=ctx.app_scope,
        time_zone=ctx.time_zone,
    )
    return tokens
```

**No database mutation on workspace switch.** The user's current workspace is a session concept, not a stored field. Switching workspaces does not touch any row in any table.

---

## `ServiceContext` — workspace_id property

`ServiceContext` exposes `workspace_id` as the canonical isolation property. Commands and queries use `ctx.workspace_id` exclusively:

```python
class ServiceContext:
    @property
    def workspace_id(self) -> int:
        return self._identity["workspace_id"]

    @property
    def user_id(self) -> int:
        return self._identity["user_id"]

    @property
    def base_role_id(self) -> int:
        return self._identity["base_role_id"]

    @property
    def workspace_role_id(self) -> int:
        return self._identity["workspace_role_id"]

    @property
    def app_scope(self) -> str:
        return self._identity["app_scope"]

    @property
    def time_zone(self) -> str:
        return self._identity.get("time_zone", "UTC")
```

---

## Domain table scoping

Every domain table has `workspace_id`:

```python
class Record(db.Model):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    # ...
```

Every command writes it from the context, never from the request body:

```python
record = Record(
    workspace_id=ctx.workspace_id,   # from JWT, verified — never from request
    ...
)
```

Every query filters by it as the first condition:

```python
query = (
    db.session.query(Record)
    .filter(Record.workspace_id == ctx.workspace_id)   # mandatory — first filter
    ...
)
```

---

## Existing applications using `team_id`

Applications built before this contract used `team_id` as the isolation column. That name is an alias for `workspace_id`. The pattern is identical — only the column name differs. When migrating, rename `team_id` → `workspace_id` in a migration and update all query references. Do not introduce a second column alongside `team_id`.

---

## Checklist — workspace architecture review

Apply when creating a new application or adding a new domain:

**Data model:**
- [ ] `users` table has no `workspace_id` column
- [ ] `base_roles` table is global (no `workspace_id`)
- [ ] `workspace_memberships` table exists and is the sole user↔workspace join
- [ ] All domain tables have `workspace_id` (not nullable, indexed)

**Commands:**
- [ ] `workspace_id` written from `ctx.workspace_id` — never from `ctx.incoming_data`
- [ ] No command reads `team_workspace_team_id` or `primals_team_id` from the user row

**Queries:**
- [ ] First filter on every domain query is `ModelClass.workspace_id == ctx.workspace_id`
- [ ] No query returns domain data without a workspace scope unless the table is explicitly global

**JWT:**
- [ ] Token carries `workspace_id`, `workspace_role_id`, `base_role_id`
- [ ] No `has_team_workspace`, `current_workspace`, or multi-field workspace state in token
- [ ] Workspace switching issues a new JWT — no DB mutation

**Registration:**
- [ ] Creating a user also creates a workspace and a founding membership in the same transaction
- [ ] The founding membership has `base_role_id=1` (ADMIN)

---

## What must NEVER happen

| Violation | Risk |
|---|---|
| `workspace_id` read from `ctx.incoming_data` | Workspace spoofing — caller claims access to any workspace |
| Workspace membership stored on the User row | Caps collaboration at a hardcoded number of workspaces |
| Current workspace stored as a DB column on User | Concurrency bugs — same user logged into two apps simultaneously |
| Workspace filter missing from a domain query | Data leak — another workspace's records returned |
| `base_roles` with a `workspace_id` column | Global roles cannot be global — defeats the permission tier model |
| Issuing a JWT with `workspace_id` the user is not a member of | Full privilege escalation |
