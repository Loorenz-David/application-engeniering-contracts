# 10 — Auth & RBAC Contract

## JWT strategy

Authentication uses **Flask-JWT-Extended**. The JWT access token carries all identity claims needed to authorize a request. There is no additional database lookup per request to determine who the user is or which workspace they are in.

Claims stored in the JWT:

```python
{
    "user_id": 42,
    "workspace_id": 7,              # internal integer — used by service layer for DB joins
    "workspace_client_id": "7f3c4d2a-...",  # UUID string — returned in API responses
    "workspace_role_id": 12,        # the user's role in this workspace
    "base_role_id": 1,              # permission tier: 1=ADMIN, 2=MEMBER, 3=FIELD
    "permissions": [                # resolved from WorkspaceRole.permissions
        "workspace:manage",
        "member:manage",
        "role:manage",
        "record:view",
        "record:create",
        "report:view",
        # ... all permissions the role grants — feature:action format
    ],
    "app_scope": "admin",           # app surface — application-defined string (e.g. "admin", "field"); set at login from the user's tier
    "time_zone": "America/New_York",
}
```

`permissions` is the list of string values from the `WorkspaceRole.permissions` JSONB column, resolved at login time from the database and embedded directly in the token. No permission database lookup is required on subsequent requests.

Permission strings use the `feature:action` format (e.g. `"record:create"`, `"member:manage"`). This format must match the frontend permission keys declared in each feature's `permissions.ts` — the frontend checks membership via `permissionSet.has(key)`. See [28_roles_permissions.md](28_roles_permissions.md) for the full `Permission` enum and naming rules.

**What is NOT in the token:**
- `team_id` / `active_team_id` / `has_team_workspace` / `current_workspace` — legacy fields. Use `workspace_id` only.
- Any flag that encodes workspace-switching state — the JWT *is* the workspace session. Switching workspaces means issuing a new JWT.

See [28_roles_permissions.md](28_roles_permissions.md) for the full permission system — how permissions are defined, how roles are seeded, and how enforcement works at the service layer.

The JWT secret is `JWT_SECRET_KEY` from config. Never share the secret between applications.

See [24_multi_tenancy.md](24_multi_tenancy.md) for the full workspace architecture — how the JWT is built from the `WorkspaceMembership` row and how workspace switching works.

---

## Role constants

```python
# routers/utils/role_decorator.py
ADMIN = 1
MEMBER = 2
FIELD = 3     # rename to fit your domain: OPERATOR, DRIVER, AGENT, etc.
```

These are stable integer IDs corresponding to rows in the global `base_roles` table. They are baked into the JWT (`base_role_id`) and never looked up from the database at request time.

Applications should alias `FIELD` to a domain-specific name in router files (e.g., `DRIVER`, `AGENT`, `OPERATOR`), but the underlying integer values are fixed. See [28_roles_permissions.md](28_roles_permissions.md) for how to define tiers for your application type.

---

## `@role_required` decorator

```python
from functools import wraps
from typing import Iterable

from flask_jwt_extended import get_jwt, verify_jwt_in_request

from my_app.errors import PermissionDenied, ValidationFailed
from my_app.routers.http.response import build_err


def role_required(allowed_roles: Iterable[int]):
    allowed_set = set(allowed_roles)

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request(optional=False)
            claims = get_jwt()

            base_role_id = claims.get("base_role_id")
            if base_role_id is None:
                return build_err(ValidationFailed("Role not found in token."))

            if allowed_set and base_role_id not in allowed_set:
                return build_err(PermissionDenied("Insufficient role permissions."))

            return fn(*args, **kwargs)

        return wrapper

    return decorator
```

Usage on any protected route:

```python
@record_bp.route("/", methods=["GET"])
@jwt_required()
@role_required([ADMIN, MEMBER])
def list_records_route():
    ...
```

---

## App scope guard

Some endpoints must only be accessible from a specific app surface (admin panel vs. driver app). The scope is embedded in the JWT as `app_scope`.

### Per-route decorator

```python
def app_scope_required(required_scope: str | Iterable[str]):
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            verify_jwt_in_request(optional=False)
            claims = get_jwt()
            app_scope = claims.get("app_scope")

            allowed = {required_scope} if isinstance(required_scope, str) else set(required_scope)
            if app_scope not in allowed:
                return build_err(PermissionDenied("This session cannot access this resource."))

            return fn(*args, **kwargs)

        return wrapper

    return decorator
```

### Blueprint-level guard (preferred for entire field-tier or member-tier blueprints)

```python
def install_blueprint_scope_guard(blueprint: Blueprint, required_scope: str | Iterable[str]) -> Blueprint:
    if getattr(blueprint, "_scope_guard_installed", False):
        return blueprint

    @blueprint.before_request
    def _guard():
        if "Authorization" not in request.headers:
            return None
        try:
            verify_jwt_in_request(optional=False)
            _validate_app_scope(required_scope)
        except (PermissionDenied, ValidationFailed) as e:
            return build_err(e)
        return None

    blueprint._scope_guard_installed = True
    return blueprint
```

Apply immediately after blueprint creation:

```python
# Use the generic tier name as the scope string; alias to your domain name in router files.
# e.g. rename "field" to "driver", "agent", "mobile", "client" to match your app surface.
field_bp = Blueprint("api_v1_field", __name__)
install_blueprint_scope_guard(field_bp, required_scope="field")
```

---

## Token refresh

Access tokens have a short TTL (15–30 min). Clients use a refresh endpoint to obtain a new access token without re-authenticating. The refresh token has a longer TTL (7–30 days).

The refresh endpoint is the only endpoint that accepts a refresh token. All other endpoints require an access token.

---

## Token blacklisting (logout)

When a user logs out, the access token and refresh token must be invalidated immediately. Because JWTs are stateless, invalidation is implemented via a Redis blocklist:

```python
# services/commands/auth/logout_user.py
from my_app.services.infra.redis.connection import get_redis
from flask_jwt_extended import get_jwt

BLOCKLIST_KEY_PREFIX = f"{current_app.config['REDIS_KEY_PREFIX']}:auth:blocklist:"


def logout_user(ctx: ServiceContext) -> dict:
    claims = get_jwt()
    jti = claims["jti"]          # JWT ID — unique per token
    exp = claims["exp"]          # Unix expiry timestamp
    ttl = int(exp - __import__("time").time()) + 60  # keep blocklist entry until after expiry

    r = get_redis()
    r.set(f"{BLOCKLIST_KEY_PREFIX}{jti}", "1", ex=max(ttl, 1))

    return {"logged_out": True}
```

Flask-JWT-Extended provides a `token_in_blocklist_loader` callback. Register it in `_init_extensions`:

```python
# my_app/__init__.py — inside _init_extensions
@jwt.token_in_blocklist_loader
def check_if_token_revoked(jwt_header: dict, jwt_payload: dict) -> bool:
    jti = jwt_payload["jti"]
    r = get_redis()
    return r.exists(f"{BLOCKLIST_KEY_PREFIX}{jti}") == 1
```

**Rules:**
- The blocklist TTL must be at least as long as the token's remaining lifetime. Set it to `(exp - now) + 60` seconds as a buffer.
- Both the access token and the refresh token must be blocklisted on logout. The client sends two requests (one per token) or the server blocklists both from the session context.
- Never store blocklist entries in the database — Redis TTL-based expiry is the correct mechanism.
- The blocklist check runs on every authenticated request. Redis must be available. If Redis is down, fail closed (reject the request) — do not fall back to allowing all tokens.

---

## Service-level authorization

RBAC decorators at the router layer check *who can call this endpoint*. Commands and queries perform additional checks on *whether the caller can act on this specific resource*:

```python
from my_app.services.identity.records import resolve_record


def delete_record(ctx: ServiceContext) -> dict:
    request = parse_delete_record_request(ctx.incoming_data)
    # resolve_record enforces workspace_id, soft-delete filtering, and raises NotFound — see 38_identity_resolution.md
    record = resolve_record(ctx, request.ref)

    if not can_record_be_deleted(record):
        raise PermissionDenied("This record cannot be deleted in its current state.")
    ...
```

Both layers of authorization are mandatory. The router check prevents unauthorized access to an endpoint. The command check prevents privilege escalation within an authorized session (IDOR attacks, cross-team data access).

---

## Password storage

Passwords are stored as bcrypt hashes. Never store plaintext passwords. Never log passwords. Never return password hashes in API responses.

```python
from werkzeug.security import generate_password_hash, check_password_hash

hashed = generate_password_hash(plain_password)
is_valid = check_password_hash(hashed, plain_password)
```

---

## What auth must NOT do

- Look up the user from the database on every request — JWT claims are the source of truth
- Hardcode role IDs in business logic outside of `role_decorator.py`
- Store auth state in the Flask `g` object across requests
- Accept tokens via query string — tokens must be in the `Authorization: Bearer <token>` header only

---

## Sign-in response shape

The sign-in command returns the access token, the user's identity, and the active workspace ID in a single flat envelope. The refresh token is written as an `httpOnly` `Set-Cookie` header by the router — it is not in the JSON body.

```python
# services/commands/auth/sign_in_user.py

def build_auth_response(user, *, workspace, membership, app_scope: str, time_zone: str | None = None) -> dict:
    """
    Build the full sign-in / token-refresh response returned to the client.
    Rename build_user_tokens → build_auth_response when adding this to your project.
    """
    role: WorkspaceRole = membership.workspace_role

    claims = {
        "user_id": user.id,
        "workspace_id": workspace.id,          # internal integer — for service layer use only
        "workspace_client_id": workspace.client_id,  # UUID string — for API response assembly
        "workspace_role_id": role.id,
        "base_role_id": role.base_role_id,
        "permissions": role.permissions,       # feature:action strings
        "app_scope": app_scope,
        "time_zone": time_zone or workspace.time_zone or "UTC",
    }

    access_token  = create_access_token(identity=str(user.id), additional_claims=claims)
    refresh_token = create_refresh_token(identity=str(user.id), additional_claims=claims)

    return {
        "access_token":  access_token,
        # refresh_token is NOT returned in the body — the router sets it as an httpOnly cookie
        "_refresh_token": refresh_token,       # prefixed _ so callers know to set it as a cookie
        "user": {
            "id":          user.client_id,     # UUID string — public identifier, never internal int
            "email":       user.email,
            "name":        user.name,
            "roles":       [role.name],        # workspace role name(s) — for display / app-shell decisions only
            "permissions": role.permissions,   # feature:action strings — for frontend can() checks
        },
        "workspace_id": workspace.client_id,   # UUID string — public identifier, never internal int
    }
```

The router extracts `_refresh_token`, sets it as a cookie, and strips it from the body:

```python
# routers/api_v1/auth.py
@auth_bp.route("/sign-in", methods=["POST"])
def sign_in_route():
    ctx = ServiceContext(incoming_data=request.get_json(), identity={})
    outcome = run_service(sign_in_user, ctx)
    if not outcome.ok:
        return build_err(outcome.error)

    data = outcome.value
    refresh_token = data.pop("_refresh_token")    # remove before sending body

    response = make_response(jsonify(data), 200)
    response.set_cookie(
        "refresh_token",
        refresh_token,
        httponly=True,
        secure=True,
        samesite="Lax",
        max_age=30 * 24 * 60 * 60,  # 30 days
    )
    return response
```

**Rules for the response fields:**
- `user.id` is `user.client_id` — the public UUID string. Never the internal integer primary key.
- `workspace_id` is `workspace.client_id` — the public UUID string. Never the internal integer.
- `user.roles` is an array containing the `WorkspaceRole.name` of the user's current workspace role (e.g. `["Admin"]`, `["Senior Editor"]`). Used by the frontend for display and broad app-shell navigation decisions only — not for permission checks.
- `user.permissions` is the resolved `feature:action` string list. The frontend's `can()` hook checks this directly.
- The token-refresh endpoint (`POST /api/v1/auth/refresh`) must return the same body shape.

---

## `GET /api/v1/me` — current user profile

The frontend calls this on every boot (via `AuthProvider`) and after profile updates. It returns the full user identity and profile data for the active workspace session.

**Required response shape:**

```python
def get_current_user(ctx: ServiceContext) -> dict:
    """
    Returns the full profile for the authenticated user in their active workspace.
    Endpoint: GET /api/v1/me
    """
    user = db.session.query(User).filter(User.id == ctx.user_id).one()
    membership = (
        db.session.query(WorkspaceMembership)
        .filter(
            WorkspaceMembership.workspace_id == ctx.workspace_id,
            WorkspaceMembership.user_id == user.id,
            WorkspaceMembership.is_active == True,
        )
        .one()
    )
    role: WorkspaceRole = membership.workspace_role

    return {
        "id":             user.client_id,          # UUID string
        "email":          user.email,
        "name":           user.name,
        "roles":          [role.name],             # workspace role name(s) — display only
        "permissions":    role.permissions,        # feature:action strings — authoritative
        "workspace_id":   ctx.workspace_client_id, # UUID string — from ServiceContext
        "avatar_file_id": user.avatar_file_id,     # UUID string | None — see 34_file_storage.md
        "timezone":       user.timezone or "UTC",  # IANA tz string
        "preferences": {
            "email_notifications": user.preferences.get("email_notifications", True),
            "theme":               user.preferences.get("theme", "system"),
        },
        "created_at": user.created_at.isoformat(),
    }
```

**User model columns required by this endpoint** (add to `User` in [24_multi_tenancy.md](24_multi_tenancy.md)):

```python
name:           Mapped[str]              = mapped_column(String(255), nullable=False, default="")
avatar_file_id: Mapped[str | None]       = mapped_column(String(64), ForeignKey("files.id"), nullable=True)
timezone:       Mapped[str | None]       = mapped_column(String(64), nullable=True)
preferences:    Mapped[dict]             = mapped_column(JSONB, nullable=False, default=dict)
```

`preferences` is a JSONB dict. The current schema is:
```json
{
  "email_notifications": true,
  "theme": "light" | "dark" | "system"
}
```

**Rules:**
- `roles` uses the same shape as the sign-in response — `WorkspaceRole.name` string(s).
- `permissions` is read live from the database on this endpoint (the JWT may be stale if a role was updated). This is the one place where the authoritative permission set is re-fetched. The `ctx.workspace_client_id` property on `ServiceContext` resolves the workspace's public `client_id` — add it as a cached lookup or a claim in the JWT.
- The frontend `AuthProvider` refreshes the auth store from this response on boot — see [Frontend_architecture/12_auth.md](../../Frontend_architecture/12_auth.md).
