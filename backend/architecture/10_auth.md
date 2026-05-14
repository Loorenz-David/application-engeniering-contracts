# 10 — Auth & RBAC Contract

## JWT strategy

Authentication uses `PyJWT` for JWT encoding and decoding. The JWT access token carries all identity and permission claims needed to authorize a request — no database lookup per request.

Claims stored in the JWT:

```python
{
    "user_id":           "usr_01ARZ...",     # user.client_id
    "username":          "john.doe",         # user.username — used by presence broadcasting
    "workspace_id":      "ws_01ARZ...",      # workspace.client_id
    "workspace_role_id": "wsr_01ARZ...",     # workspace_role.client_id
    "role_name":         "admin",            # permission Role.name via active workspace membership
    "app_scope":         "admin",            # surface scope: "admin" | "field" | "client"
    "time_zone":         "America/New_York",

    # Backend permissions — resolved at login from BackendGroupPermissions chain.
    # Format: "METHOD:/api/v1/path/" — checked in ctx.has_permission() and ctx.require_permission().
    "backend_permissions": [
        "GET:/api/v1/records/",
        "PUT:/api/v1/records/",
        "PATCH:/api/v1/records/<client_id>",
    ],

    # UI permissions — resolved at login from UIGroupPermissions chain.
    # Consumed by the frontend can()/hasAccess() checks.
    "ui": {
        "apps":          ["admin_app"],
        "pages":         ["dashboard", "records"],
        "buttons":       ["btn_create_record"],
        "actions":       ["action_export_csv"],
        "query_filters": ["filter_by_status"],
    },

    "jti": "...",   # JWT ID — unique per token; used for blocklist lookup
    "exp": 1234567, # Unix expiry timestamp
}
```

Permissions are resolved from the database **once at login** by walking the role's `UIGroupPermissions` and `BackendGroupPermissions` chains. No permission DB lookup on subsequent requests.

The JWT secret is `settings.jwt_secret_key`. Never share the secret between applications.

---

## Role constants

```python
# routers/utils/roles.py
from my_app.domain.roles.enums import RoleNameEnum

ADMIN  = RoleNameEnum.ADMIN.value   # "admin"
MEMBER = RoleNameEnum.MEMBER.value  # "member"
FIELD  = RoleNameEnum.FIELD.value   # "field"
```

Role names come from `RoleNameEnum` through the active membership (`WorkspaceMembership → WorkspaceRole → Role`) and are baked into the JWT as `role_name`. The `require_roles` dependency checks this string against the allowed list.

---

## JWT dependencies

```python
# routers/utils/jwt_dep.py
import threading
import time
from cachetools import TTLCache
from fastapi import Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt
from my_app.config import settings

_bearer = HTTPBearer()

# Per-process LRU+TTL cache for verified claims.
# Avoids one Redis round-trip per request at the cost of up to _CLAIM_CACHE_TTL
# seconds of staleness after a token is blocklisted. Acceptable for all
# non-critical paths — sensitive operations (logout, role change) blocklist
# immediately and rely on the TTL window draining.
_CLAIM_CACHE_MAXSIZE = 2000
_CLAIM_CACHE_TTL     = 60   # seconds
_claim_cache: TTLCache = TTLCache(maxsize=_CLAIM_CACHE_MAXSIZE, ttl=_CLAIM_CACHE_TTL)
_cache_lock  = threading.Lock()


async def get_jwt_claims(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    """Validates the Bearer token and returns its decoded claims.

    Raises HTTP 401 if the token is missing, invalid, expired, or blocklisted.
    Caches verified claims per process for up to _CLAIM_CACHE_TTL seconds.
    """
    token = credentials.credentials

    with _cache_lock:
        if token in _claim_cache:
            return _claim_cache[token]

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    jti = claims.get("jti")
    if jti and await _is_blocklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked.")

    with _cache_lock:
        _claim_cache[token] = claims

    return claims


def require_roles(allowed_roles: list[str]):
    """Dependency factory — gates a route to callers whose role_name is in allowed_roles."""
    allowed_set = set(allowed_roles)

    async def _check(claims: dict = Depends(get_jwt_claims)) -> dict:
        if claims.get("role_name") not in allowed_set:
            raise HTTPException(status_code=403, detail="Insufficient role permissions.")
        return claims

    return _check


def require_app_scope(required_scope: str | list[str]):
    """Dependency factory — gates a route to callers whose app_scope matches."""
    allowed = {required_scope} if isinstance(required_scope, str) else set(required_scope)

    async def _check(claims: dict = Depends(get_jwt_claims)) -> dict:
        if claims.get("app_scope") not in allowed:
            raise HTTPException(status_code=403, detail="This session cannot access this resource.")
        return claims

    return _check


async def _is_blocklisted(jti: str) -> bool:
    from my_app.services.infra.redis.async_client import get_async_redis
    redis  = get_async_redis()
    prefix = settings.redis_key_prefix
    return await redis.exists(f"{prefix}:auth:blocklist:{jti}") == 1
```

**Usage on protected routes:**

```python
@router.get("/")
async def list_records_route(
    claims: dict = Depends(require_roles([ADMIN, MEMBER])),
    ...
):
    ...
```

**Usage on field-scoped routes:**

```python
@router.get("/assignments")
async def list_assignments_route(
    claims: dict = Depends(require_app_scope("field")),
    ...
):
    ...
```

`require_roles` and `require_app_scope` each internally call `get_jwt_claims`, so the blocklist check always runs.

---

## Token refresh

Access tokens have a short TTL (30 min by default). Clients use the refresh endpoint to obtain a new access token. The refresh token has a longer TTL (30 days) and is stored as an `httpOnly` cookie.

The refresh endpoint is the only endpoint that reads the refresh cookie. All other endpoints require an access token in the `Authorization: Bearer` header.

---

## Token blocklisting (logout)

When a user logs out, both the access token and refresh token must be invalidated via a Redis blocklist:

```python
# services/commands/auth/logout_user.py
import time
import jwt
from my_app.config import settings
from my_app.services.infra.redis import get_redis_client


async def logout_user(ctx: ServiceContext) -> dict:
    _blocklist_token(ctx._identity)

    raw_refresh = ctx.incoming_data.get("refresh_token")
    if raw_refresh:
        try:
            refresh_claims = jwt.decode(
                raw_refresh,
                settings.jwt_secret_key,
                algorithms=["HS256"],
                options={"verify_exp": False},   # blocklist even if already expired
            )
            _blocklist_token(refresh_claims)
        except Exception:
            pass   # already invalid — safe to ignore

    from my_app.services.infra.execution.task_factory import create_instant_task
    from my_app.domain.execution.enums import TaskType
    create_instant_task(
        TaskType.PUSH_USER_SIGNAL,
        {"user_id": ctx.user_id, "signal": "session_invalidated"},
    )

    return {"logged_out": True}


def _blocklist_token(claims: dict) -> None:
    jti    = claims["jti"]
    exp    = claims["exp"]
    ttl    = max(int(exp - time.time()) + 60, 1)
    prefix = settings.redis_key_prefix
    r      = get_redis_client(settings.redis_uri)
    r.set(f"{prefix}:auth:blocklist:{jti}", "1", ex=ttl)
```

The logout router reads the refresh cookie and passes it to the service:

```python
# routers/api_v1/auth.py
_REFRESH_COOKIE = "refresh_token"

@router.post("/logout")
async def logout_route(
    request:  Request,
    response: Response,
    claims:   dict         = Depends(get_jwt_claims),
    session:  AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(
        identity=claims,
        incoming_data={"refresh_token": request.cookies.get(_REFRESH_COOKIE)},
        session=session,
    )
    outcome = await run_service(logout_user, ctx)
    if not outcome.success:
        return build_err(outcome.error)

    response.delete_cookie(_REFRESH_COOKIE, httponly=True, samesite="lax")
    return build_ok(outcome.data)
```

**Rules:**
- Blocklist TTL must be at least as long as the token's remaining lifetime. Use `(exp - now) + 60` seconds as a buffer.
- Both access and refresh tokens must be blocklisted on logout.
- Never store blocklist entries in the database — Redis TTL-based expiry is the correct mechanism.
- The blocklist check runs on every authenticated request inside `get_jwt_claims`. If Redis is unavailable, fail closed — do not allow tokens through.

---

## Sign-in response shape

The sign-in command returns the access token, user identity, and active workspace ID. The refresh token is returned as `_refresh_token` (prefixed `_`) and set as an `httpOnly` cookie by the router — it is not in the JSON body.

```python
# services/commands/auth/sign_in_user.py
import jwt
from datetime import datetime, timezone, timedelta
from my_app.config import settings
from my_app.domain.roles.permissions import resolve_permissions_for_role


async def build_auth_response(
    user,
    *,
    workspace,
    membership,
    app_scope: str,
) -> dict:
    workspace_role = membership.workspace_role
    permission_role = workspace_role.role
    permissions     = resolve_permissions_for_role(permission_role)

    now = datetime.now(timezone.utc)

    claims = {
        "user_id":             user.client_id,
        "username":            user.username,
        "workspace_id":        workspace.client_id,
        "workspace_role_id":   workspace_role.client_id,
        "role_name":           permission_role.name.value,
        "app_scope":           app_scope,
        "time_zone":           workspace.time_zone or "UTC",
        "backend_permissions": permissions["backend"],
        "ui":                  permissions["ui"],
    }

    access_token = jwt.encode(
        {**claims, "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes)},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    refresh_token = jwt.encode(
        {**claims, "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days)},
        settings.jwt_secret_key,
        algorithm="HS256",
    )

    return {
        "access_token":  access_token,
        "_refresh_token": refresh_token,   # router sets as httpOnly cookie
        "user": {
            "client_id":           user.client_id,
            "email":               user.email,
            "username":            user.username,
            "role":                workspace_role.name,
            "backend_permissions": permissions["backend"],
            "ui":                  permissions["ui"],
        },
        "workspace_id": workspace.client_id,
    }
```

The router extracts `_refresh_token`, sets it as a cookie, and returns the rest as JSON:

```python
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
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
    )
    return build_ok(data)
```

---

## Service-level authorization

RBAC dependencies at the router check *who can call this endpoint*. Commands perform additional checks on *whether the caller can act on this specific resource*:

```python
async def delete_record(ctx: ServiceContext) -> dict:
    request = parse_delete_record_request(ctx.incoming_data)
    record  = await resolve_record(ctx, request.ref)

    if not can_record_be_deleted(record):
        raise PermissionDenied("This record cannot be deleted in its current state.")
    ...
```

Both layers of authorization are mandatory. The router check prevents unauthorized access to an endpoint. The command check prevents privilege escalation within an authorized session (IDOR attacks, cross-workspace data access).

---

## Password storage

```python
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)

def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)
```

Never store plaintext passwords. Never log passwords. Never return password hashes in API responses.

---

## What auth must NOT do

- Look up the user from the database on every request — JWT claims are the source of truth
- Hardcode role names in business logic outside of `roles.py`
- Store auth state in FastAPI's `request.state` across requests
- Accept tokens via query string — tokens must be in the `Authorization: Bearer <token>` header only (exception: WebSocket `token` query param — see [13_sockets.md](13_sockets.md))
