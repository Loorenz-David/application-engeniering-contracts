# 28 — Roles & Permissions Contract

## What this contract defines

This contract defines **how to build a role and permission system for any application** — not what the roles or permissions are. The content (tier names, permission names) is always application-specific. The architecture is always the same.

Two concepts are defined here. Both are required. They solve different problems.

```
┌──────────────────────────────────────────────────────────────────────┐
│  TIERS  — "who is this user, broadly?"                               │
│                                                                      │
│  A small, fixed set of coarse access levels defined for your app.   │
│  Controls which app surface and broad feature set a user can reach. │
│  Checked at the ROUTER layer — fast, from JWT, no DB.               │
│                                                                      │
│  PERMISSIONS  — "what can this user specifically do?"                │
│                                                                      │
│  A named set of atomic capabilities your application defines.       │
│  Controls specific operations inside a feature.                     │
│  Checked at the COMMAND/SERVICE layer — from JWT, no DB.            │
└──────────────────────────────────────────────────────────────────────┘
```

Together they give you coarse access control at the door and fine-grained control inside. Neither replaces the other.

---

## Part 1 — Tiers

### What a tier is

A tier is a stable, global access level. Every user in every workspace belongs to exactly one tier. Tiers determine which app surface a user can reach — the admin panel, the mobile app, the client portal, the API.

Tiers are **not** business roles. "Operations Manager" is not a tier. "Staff" or "Administrator" is not a tier. A tier is the coarsest possible division of your user base.

### How many tiers to define

Every application defines between 2 and 4 tiers. More than 4 is a sign that your tiers are actually business roles — those belong in the permission layer, not here.

Common patterns:

| App type | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---|---|---|---|
| SaaS admin tool | Admin | Member | — | — |
| Marketplace | Admin | Seller | Buyer | — |
| Field service | Admin | Dispatcher | Technician | — |
| Healthcare platform | Admin | Clinician | Patient | — |
| B2B platform | Admin | Manager | Staff | Client |
| Content platform | Admin | Editor | Contributor | Viewer |

Choose tiers based on app surfaces, not job titles.

### Defining your tiers

Tiers are defined once in `domain/auth/tiers.py` as integer constants. The integer IDs are stable — they are baked into JWTs and must never change once deployed.

```python
# domain/auth/tiers.py

# Tier 1 — full workspace control, all operations, access to admin surface
ADMIN = 1

# Tier 2 — standard collaborator, admin surface, capabilities controlled by role
MEMBER = 2

# Tier 3 — restricted user, secondary app surface (mobile, client portal)
# Rename to match your domain: TECHNICIAN, AGENT, DRIVER, BUYER, PATIENT, etc.
FIELD = 3
```

Rules:
- Tier 1 (`id=1`) is always the highest-privilege tier. It is never restricted by permissions — it bypasses all permission checks (see enforcement below).
- Tier IDs are positive integers, assigned in descending privilege order: 1 = most, N = least.
- Names are uppercase strings. The constant name is what you use in code; the human label is on the `WorkspaceRole` record.
- You can have 2 tiers or 4 tiers. Never 1, never more than 4.

### The global tier table

Tiers are stored in a global seed table — one row per tier, shared across all workspaces, seeded once at deployment:

```python
class BaseRole(db.Model):
    __tablename__ = "base_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

**`base_roles` is a global table — no `workspace_id`.** These rows never change at runtime. They are seeded in a migration or seed command that runs once per environment.

---

## Part 2 — Permissions

### What a permission is

A permission is a named, atomic capability: the right to perform one specific operation. It is not a role. It is not a tier. It is a yes/no flag for one thing.

Good permissions are operation-scoped, not feature-scoped:

```
# Good — one operation, clear meaning
create_records
delete_records
export_data
manage_members
manage_billing

# Bad — too broad, meaningless
records_access
full_access
can_do_stuff
```

### Defining your permissions

Every application defines its own `Permission` enum in `domain/auth/permissions.py`. This is the single source of truth for all capabilities in the application.

```python
# domain/auth/permissions.py
import enum


class Permission(str, enum.Enum):
    """
    All atomic capabilities in this application.

    Naming rule: verb_noun in snake_case.
    Group by domain with a comment header.
    A permission protects an operation, not a feature.
    """

    # ── Workspace administration ───────────────────────────────────
    MANAGE_WORKSPACE    = "manage_workspace"    # settings, name, timezone
    MANAGE_MEMBERS      = "manage_members"      # invite, deactivate users
    MANAGE_ROLES        = "manage_roles"        # create, edit, delete roles
    VIEW_MEMBERS        = "view_members"

    MANAGE_BILLING      = "manage_billing"      # subscription, invoices
    VIEW_BILLING        = "view_billing"

    # ── [Your domain A] ───────────────────────────────────────────
    VIEW_RECORDS        = "view_records"
    CREATE_RECORDS      = "create_records"
    EDIT_RECORDS        = "edit_records"
    DELETE_RECORDS      = "delete_records"
    EXPORT_RECORDS      = "export_records"

    # ── [Your domain B] ───────────────────────────────────────────
    VIEW_REPORTS        = "view_reports"
    MANAGE_REPORTS      = "manage_reports"

    # ── [Your domain C] ───────────────────────────────────────────
    MANAGE_INTEGRATIONS = "manage_integrations"
    VIEW_INTEGRATIONS   = "view_integrations"
```

Replace the domain sections with the actual domains of your application. The workspace administration block is nearly universal — keep it in every application. The rest is yours to define.

**Naming rules:**

| Rule | Example |
|---|---|
| `verb_noun` in snake_case | `create_orders`, `view_analytics` |
| Verb describes the operation level | `view_` / `create_` / `edit_` / `delete_` / `manage_` / `export_` / `publish_` |
| `manage_` means full CRUD + sub-operations | `manage_members` covers invite, edit, deactivate |
| Noun is the resource being protected | `_orders`, `_members`, `_billing`, `_reports` |
| No `can_`, no `allow_`, no `has_` prefix | `create_orders`, not `can_create_orders` |
| No tier names in permission names | `edit_records`, not `admin_edit_records` |

### Default permission sets per tier

Every application maps its tiers to default permission sets. These defaults become the starting permission set for the three seeded system roles.

```python
# domain/auth/permissions.py (continued)

# Tier 1 — gets every permission automatically.
# Using frozenset(Permission) means new permissions are included the moment
# they are added to the enum, with no manual update required.
ALL_PERMISSIONS: frozenset[Permission] = frozenset(Permission)

# Tier 2 default — define what a standard collaborator can do by default.
# Admins can create custom roles that expand or restrict this.
MEMBER_DEFAULT_PERMISSIONS: frozenset[Permission] = frozenset({
    Permission.VIEW_MEMBERS,
    Permission.VIEW_RECORDS,
    Permission.CREATE_RECORDS,
    Permission.EDIT_RECORDS,
    Permission.VIEW_REPORTS,
})

# Tier 3 default — define what a restricted/field user can do by default.
# Typically read-only plus the operations they perform in the field.
FIELD_DEFAULT_PERMISSIONS: frozenset[Permission] = frozenset({
    Permission.VIEW_RECORDS,
})
```

**Key design decision — tier 1 always gets `ALL_PERMISSIONS`:** This is non-negotiable. The highest-privilege tier is never locked out by a role misconfiguration. This is the pattern GitHub, Notion, Linear, and every major SaaS platform follows.

---

## Part 3 — The data model

### `workspace_roles` — roles within a workspace

```python
from sqlalchemy.dialects.postgresql import JSONB

class WorkspaceRole(db.Model):
    __tablename__ = "workspace_roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    workspace_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("workspaces.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_role_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("base_roles.id"), nullable=False
    )
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_roles_name"),
    )
```

`permissions` is a JSONB list of strings — the `.value` properties from your `Permission` enum:

```json
["view_records", "create_records", "edit_records", "view_reports"]
```

### System roles vs custom roles

Every workspace has exactly one `WorkspaceRole` per tier, marked `is_system=True`. These are seeded when the workspace is created. They cannot be deleted or have their tier changed.

Workspace ADMINs can create additional custom roles (`is_system=False`) for any tier except Tier 1. Custom roles are named variations — "Senior Editor", "Read-Only Analyst", "Regional Manager" — with a curated permission subset.

| Property | System roles | Custom roles |
|---|---|---|
| Created | Automatically on workspace creation | By workspace ADMIN at runtime |
| `is_system` | `True` | `False` |
| Deletable | No | Yes (if no members assigned) |
| Tier changeable | No | No — tier is set at creation |
| Permission changeable | Yes | Yes |
| Count per tier | Exactly 1 | 0 to N |

---

## Part 4 — Seeding system roles

When a workspace is created, seed the system roles in the same transaction. This function is called from the workspace creation command:

```python
# services/commands/workspace/create_workspace.py
from domain.auth.permissions import ALL_PERMISSIONS, MEMBER_DEFAULT_PERMISSIONS, FIELD_DEFAULT_PERMISSIONS
from domain.auth.tiers import ADMIN, MEMBER, FIELD


def _seed_system_roles(workspace: Workspace) -> list[WorkspaceRole]:
    """
    Create the system roles for a new workspace.
    Adapt tier names and defaults to match your application's tier definition.
    """
    system_roles = [
        WorkspaceRole(
            client_id=generate_client_id(),
            workspace_id=workspace.id,
            name="Admin",                           # rename to match your top-tier label
            base_role_id=ADMIN,
            is_system=True,
            permissions=[p.value for p in ALL_PERMISSIONS],
        ),
        WorkspaceRole(
            client_id=generate_client_id(),
            workspace_id=workspace.id,
            name="Member",                          # rename: "Editor", "Staff", "Agent"
            base_role_id=MEMBER,
            is_system=True,
            permissions=[p.value for p in MEMBER_DEFAULT_PERMISSIONS],
        ),
        WorkspaceRole(
            client_id=generate_client_id(),
            workspace_id=workspace.id,
            name="Field User",                      # rename: "Technician", "Driver", "Client"
            base_role_id=FIELD,
            is_system=True,
            permissions=[p.value for p in FIELD_DEFAULT_PERMISSIONS],
        ),
    ]
    db.session.add_all(system_roles)
    return system_roles
```

If your application has 2 tiers, seed 2 system roles. If it has 4, seed 4. The seeder always mirrors your tier definition.

---

## Part 5 — JWT embedding

At login, the user's resolved permission list is embedded directly in the JWT. No permission lookup is required on subsequent requests.

```python
def build_user_tokens(user, *, workspace, membership, app_scope, time_zone=None) -> dict:
    role: WorkspaceRole = membership.workspace_role

    claims = {
        "user_id": user.id,
        "workspace_id": workspace.id,
        "workspace_role_id": role.id,
        "base_role_id": role.base_role_id,
        "permissions": role.permissions,        # list[str] — embedded at login time
        "app_scope": app_scope,
        "time_zone": time_zone or workspace.time_zone or "UTC",
    }
    access_token = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)
    return {"access_token": access_token, "refresh_token": refresh_token}
```

**Staleness trade-off:** When a role's permission set is updated, the change takes effect when the user's access token expires (typically 15–30 min). This is the same trade-off every major SaaS platform (GitHub, Notion, Linear) accepts. For immediate revocation of a security-critical session, use the JWT blocklist — see [10_auth.md](10_auth.md).

---

## Part 6 — Enforcement

### Router layer — tier check

The router asks: "does this type of user belong on this endpoint at all?"

```python
# routers/utils/role_decorator.py
from domain.auth.tiers import ADMIN, MEMBER, FIELD

@blueprint.route("/records/", methods=["POST"])
@jwt_required()
@role_required([ADMIN, MEMBER])          # FIELD users cannot create records
def create_record_route():
    ctx = ServiceContext(identity=get_jwt(), incoming_data=request.get_json())
    outcome = run_service(create_record, ctx)
    return build_ok(outcome) if outcome.ok else build_err(outcome.error)
```

The router never checks individual permissions. Tier check at the door; permission check inside.

### Service layer — permission check

The service layer asks: "does this specific user have the right to do this specific thing?"

```python
# services/commands/record/create_record.py
from domain.auth.permissions import Permission


def create_record(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.CREATE_RECORDS)   # raises PermissionDenied if not granted

    request = parse_create_record_request(ctx.incoming_data)

    with db.session.begin():
        record = Record(workspace_id=ctx.workspace_id, ...)
        db.session.add(record)
    ...
```

`ctx.require_permission()` is always the **first line** of any write command. Before parsing the request, before touching the DB.

Use `ctx.has_permission()` (non-raising) for conditional behavior — not for access control:

```python
def get_record(ctx: ServiceContext, record_id: int) -> dict:
    record = _load_record(ctx, record_id)

    # Audit trail is sensitive — only shown to users who have export access
    include_audit = ctx.has_permission(Permission.EXPORT_RECORDS)

    return serialize_record(record, include_audit=include_audit)
```

### `ServiceContext` — the two methods

```python
class ServiceContext:

    def has_permission(self, permission: Permission) -> bool:
        """Non-raising. Use for conditional data shaping, not access control."""
        return permission.value in self._identity.get("permissions", [])

    def require_permission(self, permission: Permission) -> None:
        """Raises PermissionDenied if the caller lacks this permission.
        Tier 1 (base_role_id == 1) always passes — the top tier is never locked out.
        """
        from my_app.errors import PermissionDenied
        if self._identity.get("base_role_id") == 1:
            return
        if not self.has_permission(permission):
            raise PermissionDenied(
                f"Your role does not have the '{permission.value}' permission."
            )
```

**The top-tier bypass is the only hardcoded privilege rule in the system.** Everything else is data.

---

## Part 7 — Custom role management

### Creating a custom role

```python
# services/commands/workspace_role/create_workspace_role.py
from domain.auth.permissions import Permission
from domain.auth.tiers import ADMIN


def create_workspace_role(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.MANAGE_ROLES)

    request = parse_create_workspace_role_request(ctx.incoming_data)

    if request.base_role_id == ADMIN:
        raise ValidationFailed("Cannot create a custom top-tier role. Only one exists per workspace.")

    unknown_permissions = set(request.permissions) - {p.value for p in Permission}
    if unknown_permissions:
        raise ValidationFailed(f"Unknown permissions: {', '.join(sorted(unknown_permissions))}")

    with db.session.begin():
        role = WorkspaceRole(
            client_id=generate_client_id(),
            workspace_id=ctx.workspace_id,
            name=request.name,
            description=request.description,
            base_role_id=request.base_role_id,
            is_system=False,
            permissions=list(set(request.permissions)),   # deduplicate
        )
        db.session.add(role)

    return serialize_workspace_role(role)
```

### Deleting a custom role

A custom role cannot be deleted while members are assigned to it:

```python
def delete_workspace_role(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.MANAGE_ROLES)

    role = _load_role(ctx, role_id)

    if role.is_system:
        raise ValidationFailed("System roles cannot be deleted.")

    member_count = (
        db.session.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.workspace_role_id == role.id,
            WorkspaceMembership.is_active == True,
        )
        .count()
    )
    if member_count > 0:
        raise ValidationFailed(
            f"Cannot delete a role with {member_count} active member(s). Reassign them first."
        )

    with db.session.begin():
        db.session.delete(role)

    return {"deleted": True}
```

---

## Part 8 — Domain-specific constraints

### The anti-pattern: generic constraint tables

The naive approach stores domain constraints as rows linked to roles:

```python
# DO NOT DO THIS
class DateRangeAccessRule(db.Model):       # "this role can only see records in this date range"
class StateTransitionRule(db.Model):       # "this role can only move records to these states"
```

This couples the role infrastructure to specific domain concepts. The role system cannot be reused in another application because it carries these tables with it.

### The correct pattern: domain guard functions

Domain constraints that vary by tier or role belong in the **domain layer** as pure guard functions. They take the entity and the caller's context; they raise `PermissionDenied` if violated.

```python
# domain/<resource>/<resource>_guards.py
from domain.auth.tiers import FIELD
from my_app.errors import PermissionDenied


def assert_can_transition_to_state(entity, target_state_id: int, base_role_id: int) -> None:
    """
    Tier-specific state transition rules for this domain.
    Define which states each tier can move a record into.
    """
    FIELD_ALLOWED_STATES = {4, 5, 6}   # define for your domain

    if base_role_id == FIELD and target_state_id not in FIELD_ALLOWED_STATES:
        raise PermissionDenied("Your role cannot transition records to this state.")
```

Called from the command — after the permission check, not instead of it:

```python
def transition_record_state(ctx: ServiceContext) -> dict:
    ctx.require_permission(Permission.EDIT_RECORDS)               # permission check first
    assert_can_transition_to_state(record, target_state_id, ctx.base_role_id)  # domain rule second
    ...
```

For time-window or scope restrictions, apply them in the query itself:

```python
def list_records(ctx: ServiceContext) -> dict:
    query = db.session.query(Record).filter(Record.workspace_id == ctx.workspace_id)

    # Tier-specific scope restriction — field users only see their own assigned records
    if ctx.base_role_id == FIELD:
        query = query.filter(Record.assigned_user_id == ctx.user_id)

    return paginate_and_serialize(query, ctx)
```

Domain constraints live in domain code — readable, testable in isolation, version-controlled, and portable.

---

## Part 9 — File locations

```
domain/
  auth/
    tiers.py            # ADMIN = 1, MEMBER = 2, FIELD = 3 (your names)
    permissions.py      # Permission enum + default sets per tier
    role_guards.py      # Optional: standalone require_permission() if not on ServiceContext

  <resource>/
    <resource>_guards.py   # Domain-specific tier constraints for this resource

routers/
  utils/
    role_decorator.py   # @role_required, @app_scope_required decorators

models/
  tables/
    auth/
      base_role.py        # Global — one row per tier
      workspace_role.py   # Per workspace — system + custom roles

services/
  commands/
    workspace_role/
      create_workspace_role.py
      update_workspace_role.py
      delete_workspace_role.py
  queries/
    workspace_role/
      list_workspace_roles.py
      get_workspace_role.py
```

---

## Part 10 — Checklist for a new application

**Design phase:**
- [ ] Decided how many tiers (2–4) and what they represent in this application
- [ ] Defined tier integer IDs — these are permanent once deployed
- [ ] Defined all permissions the application needs in `domain/auth/permissions.py`
- [ ] Defined default permission sets for each tier
- [ ] Confirmed tier 1 always uses `ALL_PERMISSIONS`

**Implementation:**
- [ ] `base_roles` seeded with one row per tier
- [ ] `_seed_system_roles()` called inside the workspace creation transaction
- [ ] JWT includes `base_role_id` and `permissions: list[str]`
- [ ] `ctx.require_permission()` is the first line of every write command
- [ ] Unknown permission strings are rejected at write time (validate against `Permission` enum)

**What is absent (intentionally):**
- [ ] No generic constraint tables (`DateRangeAccessRule`, `StateTransitionRule`)
- [ ] No `ctx.allow_is_system_modification` flag
- [ ] No permission logic at the router layer
- [ ] No hardcoded tier IDs in commands (use the constants from `tiers.py`)

---

## Common mistakes

| Mistake | What it looks like | Why it fails |
|---|---|---|
| Designing tiers as business roles | 5+ tiers: Owner, Manager, Editor, Viewer, Guest | Tiers multiply when business role logic leaks in. Use custom permission sets instead. |
| Putting permission checks at the router | `@permission_required(Permission.CREATE_RECORDS)` as a decorator | Routers only know tiers. Permissions belong in commands. |
| Permissions as database rows | `PermissionRow` table with `name`, `description` columns | The permission set is code, not data. Storing it in DB adds a migration for every new operation. |
| Generic constraint tables | `AccessRule(role_id, model_name, from_date, to_date)` | Ties role infrastructure to domain models. Not portable. |
| Top tier restricted by permissions | ADMIN role with `VIEW_BILLING` removed | Admins can always be locked out. Top tier must bypass permission checks. |
| Mutable tier IDs | Renaming base_role_id=2 from MEMBER to something else | IDs are in JWTs. Changing them invalidates all active sessions silently. |
| One permission for a broad feature | `ORDERS_ACCESS = "orders_access"` | Too coarse. Cannot grant view without create. Use `VIEW_`, `CREATE_`, `EDIT_`, `DELETE_` separately. |
