# 10 — Auth & RBAC Contract

## JWT strategy

Authentication uses **Flask-JWT-Extended**. The JWT access token carries all identity claims needed to authorize a request. There is no additional database lookup per request to determine who the user is or which workspace they are in.

Claims stored in the JWT:

```python
{
    "user_id": 42,
    "workspace_id": 7,              # the active workspace — one per session
    "workspace_role_id": 12,        # the user's role in this workspace
    "base_role_id": 1,              # permission tier: 1=ADMIN, 2=MEMBER, 3=FIELD
    "permissions": [                # resolved from WorkspaceRole.permissions
        "manage_workspace",
        "manage_members",
        "manage_roles",
        "view_records",
        "create_records",
        "view_analytics",
        # ... all permissions the role grants
    ],
    "app_scope": "admin",           # surface scope, e.g. "admin" | "mobile" | "client"
    "time_zone": "America/New_York",
}
```

`permissions` is the list of string values from the `WorkspaceRole.permissions` JSONB column, resolved at login time from the database and embedded directly in the token. No permission database lookup is required on subsequent requests.

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

### Blueprint-level guard (preferred for entire driver or client blueprints)

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
driver_bp = Blueprint("api_v1_driver", __name__)
install_blueprint_scope_guard(driver_bp, required_scope="driver")
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
def delete_record(ctx: ServiceContext, record_id: int) -> dict:
    record = db.session.get(Record, record_id)
    if record is None or record.workspace_id != ctx.workspace_id:
        raise NotFound(f"Record {record_id} not found.")

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
