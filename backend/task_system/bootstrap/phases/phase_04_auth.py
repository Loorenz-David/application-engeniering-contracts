from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase4(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 4 - Auth, RBAC & Multi-Tenancy ------------------------------")

    _touch(root / a / "domain" / "roles" / "__init__.py", force=force)
    _write(root / a / "domain" / "roles" / "enums.py", """\
from enum import StrEnum


class RoleNameEnum(StrEnum):
    ADMIN = "admin"
    MEMBER = "member"
    FIELD = "field"
""", force=force)
    _write(root / a / "domain" / "roles" / "permissions.py", '''\
from enum import StrEnum


class Permission(StrEnum):
    """Add app-specific backend permissions as METHOD:/api/v1/path entries."""
    pass


def resolve_permissions_for_role(role) -> dict:
    """Return JWT-ready backend and UI permissions for a role.

    Applications should replace this scaffold with the relational permission
    resolver once permission atoms and groups have been seeded.
    """
    return {
        "backend": [],
        "ui": {
            "apps": [],
            "pages": [],
            "buttons": [],
            "actions": [],
            "query_filters": [],
        },
    }
''', force=force)

    _touch(root / a / "models" / "tables" / "roles" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "roles" / "role.py", f"""\
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from {a}.domain.roles.enums import RoleNameEnum
from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class Role(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "role"
    __tablename__ = "roles"

    name: Mapped[RoleNameEnum] = mapped_column(
        SAEnum(RoleNameEnum, name="role_name_enum", create_type=True),
        nullable=False,
        unique=True,
        index=True,
    )
""", force=force)
    _write(root / a / "models" / "tables" / "roles" / "workspace_role.py", f"""\
from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class WorkspaceRole(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "wsr"
    __tablename__ = "workspace_roles"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_workspace_roles_workspace_name"),
    )

    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id", deferrable=True), nullable=False, index=True)
    role_id: Mapped[str] = mapped_column(String(64), ForeignKey("roles.client_id", deferrable=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    role: Mapped["Role"] = relationship("Role")
""", force=force)

    _touch(root / a / "models" / "tables" / "workspaces" / "__init__.py", force=force)
    _write(root / a / "models" / "tables" / "workspaces" / "workspace.py", f"""\
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class Workspace(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "ws"
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    time_zone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    created_by_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
""", force=force)
    _write(root / a / "models" / "tables" / "workspaces" / "workspace_membership.py", f"""\
from sqlalchemy import Boolean, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from datetime import datetime, timezone

from {a}.models.base.base import Base
from {a}.models.base.identity import IdentityMixin


class WorkspaceMembership(IdentityMixin, Base):
    CLIENT_ID_PREFIX = "wsm"
    __tablename__ = "workspace_memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "workspace_id", name="uq_workspace_memberships_user_workspace"),
    )

    user_id: Mapped[str] = mapped_column(String(64), ForeignKey("users.client_id", deferrable=True), nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id", deferrable=True), nullable=False, index=True)
    workspace_role_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspace_roles.client_id", deferrable=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    workspace_role: Mapped["WorkspaceRole"] = relationship("WorkspaceRole")
""", force=force)

    _touch(root / a / "routers" / "utils" / "__init__.py", force=force)
    _write(root / a / "routers" / "utils" / "roles.py", """\
ADMIN  = "admin"
MEMBER = "member"
FIELD  = "field"
""", force=force)
    _write(root / a / "routers" / "utils" / "jwt_dep.py", f"""\
import threading

import jwt
from cachetools import TTLCache
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from {a}.config import settings

_bearer = HTTPBearer()

_claim_cache: TTLCache = TTLCache(maxsize=2000, ttl=60)
_cache_lock = threading.Lock()


async def get_jwt_claims(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> dict:
    token = credentials.credentials

    with _cache_lock:
        if token in _claim_cache:
            return _claim_cache[token]

    try:
        claims = jwt.decode(token, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")

    jti = claims.get("jti")
    if jti and await _is_blocklisted(jti):
        raise HTTPException(status_code=401, detail="Token has been revoked.")

    with _cache_lock:
        _claim_cache[token] = claims

    return claims


def require_roles(allowed_roles: list[str]):
    allowed_set = set(allowed_roles)

    async def _check(claims: dict = Depends(get_jwt_claims)) -> dict:
        if claims.get("role_name") not in allowed_set:
            raise HTTPException(status_code=403, detail="Insufficient role permissions.")
        return claims

    return _check


def require_app_scope(required_scope: str | list[str]):
    allowed = {{required_scope}} if isinstance(required_scope, str) else set(required_scope)

    async def _check(claims: dict = Depends(get_jwt_claims)) -> dict:
        if claims.get("app_scope") not in allowed:
            raise HTTPException(status_code=403, detail="This session cannot access this resource.")
        return claims

    return _check


async def _is_blocklisted(jti: str) -> bool:
    try:
        from {a}.services.infra.redis.async_client import get_async_redis
        redis = get_async_redis()
        return await redis.exists(f"{{settings.redis_key_prefix}}:auth:blocklist:{{jti}}") == 1
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Auth blocklist unavailable.") from exc
""", force=force)

    _write(root / a / "routers" / "utils" / "rate_limit.py", f"""\
from fastapi import Depends, HTTPException, Request

from {a}.config import settings
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.services.infra.redis.async_client import get_async_redis


async def _apply_rate_limit(key: str, max_requests: int, window_seconds: int) -> None:
    if settings.environment in ("development", "testing"):
        return
    redis = get_async_redis()
    async with redis.pipeline(transaction=True) as pipe:
        await pipe.incr(key)
        await pipe.expire(key, window_seconds)
        results = await pipe.execute()
    if results[0] > max_requests:
        raise HTTPException(status_code=429, detail="Rate limit exceeded. Please wait before retrying.")


def rate_limit(max_requests: int, window_seconds: int, key_prefix: str):
    \"\"\"Rate limit for authenticated endpoints — keys by user_id from JWT.\"\"\"
    async def _check(claims: dict = Depends(get_jwt_claims)) -> None:
        user_id = claims.get("user_id", "anonymous")
        key = f"{{settings.redis_key_prefix}}:ratelimit:{{key_prefix}}:{{user_id}}"
        await _apply_rate_limit(key, max_requests, window_seconds)
    return _check


def ip_rate_limit(max_requests: int, window_seconds: int, key_prefix: str):
    \"\"\"Rate limit for unauthenticated endpoints — keys by client IP.\"\"\"
    async def _check(request: Request) -> None:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else "unknown")
        key = f"{{settings.redis_key_prefix}}:ratelimit:{{key_prefix}}:{{ip}}"
        await _apply_rate_limit(key, max_requests, window_seconds)
    return _check
""", force=force)

    _touch(root / a / "routers" / "middleware" / "__init__.py", force=force)
    _write(root / a / "routers" / "middleware" / "backend_permission.py", f"""\
import re

import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from {a}.config import settings


class BackendPermissionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not request.url.path.startswith("/api/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return await call_next(request)

        try:
            claims = jwt.decode(
                auth_header.removeprefix("Bearer "),
                settings.jwt_secret_key,
                algorithms=["HS256"],
            )
        except jwt.PyJWTError:
            return await call_next(request)

        if claims.get("app_scope") == "admin":
            return await call_next(request)

        allowed = set(claims.get("backend_permissions", []))
        normalized = _normalize_api_path(f"{{request.method}}:{{request.url.path}}")
        if normalized not in allowed:
            return JSONResponse(
                status_code=403,
                content={{"error": "Your role does not have access to this endpoint."}},
            )
        return await call_next(request)


def _normalize_api_path(key: str) -> str:
    return re.sub(r"/[a-z]{{2,5}}_[A-Z0-9]{{10,}}", "/<client_id>", key)
""", force=force)

    _touch(root / a / "services" / "commands" / "auth" / "__init__.py", force=force)
    _write(root / a / "services" / "commands" / "auth" / "sign_in_user.py", f"""\
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import bcrypt
import jwt
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from {a}.config import settings
from {a}.domain.roles.permissions import resolve_permissions_for_role
from {a}.errors.permissions import PermissionDenied
from {a}.models.tables.users.user import User
from {a}.models.tables.roles.workspace_role import WorkspaceRole
from {a}.models.tables.workspaces.workspace import Workspace
from {a}.models.tables.workspaces.workspace_membership import WorkspaceMembership
from {a}.services.context import ServiceContext


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


async def sign_in_user(ctx: ServiceContext) -> dict:
    data = ctx.incoming_data
    identifier = data.get("email") or data.get("username")
    password = data.get("password", "")

    result = await ctx.session.execute(
        select(User).where((User.email == identifier) | (User.username == identifier))
    )
    user = result.scalar_one_or_none()
    if user is None or not _verify_password(password, user.password):
        raise PermissionDenied("Invalid credentials.")

    membership_result = await ctx.session.execute(
        select(WorkspaceMembership)
        .options(selectinload(WorkspaceMembership.workspace_role).selectinload(WorkspaceRole.role))
        .where(
            WorkspaceMembership.user_id == user.client_id,
            WorkspaceMembership.is_active.is_(True),
        )
    )
    membership = membership_result.scalar_one_or_none()
    if membership is None:
        raise PermissionDenied("User has no workspace membership.")

    workspace = await ctx.session.get(Workspace, membership.workspace_id)
    return build_auth_response(user, workspace=workspace, membership=membership, app_scope=data.get("app_scope", "admin"))


def build_auth_response(user: User, *, workspace: Workspace, membership: WorkspaceMembership, app_scope: str) -> dict:
    workspace_role = membership.workspace_role
    permission_role = workspace_role.role
    permissions = resolve_permissions_for_role(permission_role)
    now = datetime.now(timezone.utc)
    claims = {{
        "user_id": user.client_id,
        "username": user.username,
        "workspace_id": workspace.client_id,
        "workspace_role_id": workspace_role.client_id,
        "role_name": permission_role.name.value,
        "app_scope": app_scope,
        "time_zone": workspace.time_zone or "UTC",
        "backend_permissions": permissions["backend"],
        "ui": permissions["ui"],
    }}
    access_token = jwt.encode(
        {{**claims, "jti": str(uuid4()), "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes)}},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    refresh_token = jwt.encode(
        {{**claims, "jti": str(uuid4()), "exp": now + timedelta(days=settings.jwt_refresh_token_expire_days)}},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    return {{
        "access_token": access_token,
        "_refresh_token": refresh_token,
        "user": {{
            "client_id": user.client_id,
            "email": user.email,
            "username": user.username,
            "role": workspace_role.name,
            "backend_permissions": permissions["backend"],
            "ui": permissions["ui"],
        }},
        "workspace_id": workspace.client_id,
    }}
""", force=force)
    _write(root / a / "services" / "commands" / "auth" / "logout_user.py", f"""\
import time

import jwt

from {a}.config import settings
from {a}.services.context import ServiceContext


async def logout_user(ctx: ServiceContext) -> dict:
    await _blocklist_token(ctx.identity)
    raw_refresh = ctx.incoming_data.get("refresh_token")
    if raw_refresh:
        try:
            refresh_claims = jwt.decode(
                raw_refresh,
                settings.jwt_secret_key,
                algorithms=["HS256"],
                options={{"verify_exp": False}},
            )
            await _blocklist_token(refresh_claims)
        except Exception:
            pass
    return {{"logged_out": True}}


async def _blocklist_token(claims: dict) -> None:
    jti = claims.get("jti")
    exp = claims.get("exp")
    if not jti or not exp:
        return
    from {a}.services.infra.redis.async_client import get_async_redis
    ttl = max(int(exp - time.time()) + 60, 1)
    await get_async_redis().set(f"{{settings.redis_key_prefix}}:auth:blocklist:{{jti}}", "1", ex=ttl)
""", force=force)
    _write(root / a / "services" / "commands" / "auth" / "refresh_token.py", f"""\
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt

from {a}.config import settings
from {a}.errors.permissions import PermissionDenied
from {a}.services.context import ServiceContext


async def refresh_token(ctx: ServiceContext) -> dict:
    raw_refresh = ctx.incoming_data.get("refresh_token")
    if not raw_refresh:
        raise PermissionDenied("Refresh token missing.")
    try:
        claims = jwt.decode(raw_refresh, settings.jwt_secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise PermissionDenied("Invalid refresh token.") from exc

    now = datetime.now(timezone.utc)
    claims.pop("exp", None)
    claims["jti"] = str(uuid4())
    access_token = jwt.encode(
        {{**claims, "exp": now + timedelta(minutes=settings.jwt_access_token_expire_minutes)}},
        settings.jwt_secret_key,
        algorithm="HS256",
    )
    return {{"access_token": access_token}}
""", force=force)

    _write(root / a / "routers" / "api_v1" / "auth.py", f"""\
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from {a}.models.database import get_db
from {a}.routers.http.response import build_err, build_ok
from {a}.routers.utils.jwt_dep import get_jwt_claims
from {a}.routers.utils.rate_limit import ip_rate_limit
from {a}.services.commands.auth.logout_user import logout_user
from {a}.services.commands.auth.refresh_token import refresh_token
from {a}.services.commands.auth.sign_in_user import sign_in_user
from {a}.services.context import ServiceContext
from {a}.services.run_service import run_service

router = APIRouter()
_REFRESH_COOKIE = "refresh_token"


class SignInBody(BaseModel):
    email: str | None = None
    username: str | None = None
    password: str
    app_scope: str = "admin"


@router.post("/sign-in")
async def sign_in_route(
    body: SignInBody,
    response: Response,
    session: AsyncSession = Depends(get_db),
    _rate: None = Depends(ip_rate_limit(10, 60, "sign-in")),
):
    outcome = await run_service(sign_in_user, ServiceContext(identity={{}}, incoming_data=body.model_dump(), session=session))
    if not outcome.success:
        return build_err(outcome.error)
    data = dict(outcome.data)
    refresh_token_value = data.pop("_refresh_token")
    response.set_cookie(_REFRESH_COOKIE, refresh_token_value, httponly=True, secure=True, samesite="lax")
    return build_ok(data)


@router.post("/logout")
async def logout_route(
    request: Request,
    response: Response,
    claims: dict = Depends(get_jwt_claims),
    session: AsyncSession = Depends(get_db),
):
    ctx = ServiceContext(identity=claims, incoming_data={{"refresh_token": request.cookies.get(_REFRESH_COOKIE)}}, session=session)
    outcome = await run_service(logout_user, ctx)
    response.delete_cookie(_REFRESH_COOKIE, httponly=True, samesite="lax")
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)


@router.post("/refresh")
async def refresh_route(request: Request, session: AsyncSession = Depends(get_db)):
    ctx = ServiceContext(identity={{}}, incoming_data={{"refresh_token": request.cookies.get(_REFRESH_COOKIE)}}, session=session)
    outcome = await run_service(refresh_token, ctx)
    return build_ok(outcome.data) if outcome.success else build_err(outcome.error)
""", force=force)

    append_once(root / a / "models" / "__init__.py", (
        f"from {a}.models.tables.roles import role  # noqa: F401\n"
        f"from {a}.models.tables.roles import workspace_role  # noqa: F401\n"
        f"from {a}.models.tables.workspaces import workspace  # noqa: F401\n"
        f"from {a}.models.tables.workspaces import workspace_membership  # noqa: F401\n"
    ))
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        f"from {a}.routers.api_v1 import health\n",
        f"from {a}.routers.api_v1 import auth, health\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "__init__.py",
        '    app.include_router(health.router, prefix="/health", tags=["health"])\n',
        '    app.include_router(health.router, prefix="/health", tags=["health"])\n'
        '    app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])\n',
    )
    replace_once(
        root / a / "__init__.py",
        "    app.add_middleware(\n        CORSMiddleware,",
        f"    from {a}.routers.middleware.backend_permission import BackendPermissionMiddleware\n"
        "    app.add_middleware(BackendPermissionMiddleware)\n\n"
        "    app.add_middleware(\n        CORSMiddleware,"
    )
