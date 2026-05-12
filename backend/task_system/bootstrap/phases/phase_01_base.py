import socket
from pathlib import Path

import typer

from bootstrap.writer import touch_file as _touch
from bootstrap.writer import write_file as _write


def _find_free_port(start: int, max_attempts: int = 20) -> int:
    """Return the first TCP port >= start that is not bound on localhost."""
    for port in range(start, start + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(
        f"No free port found in range {start}–{start + max_attempts - 1}. "
        "Free up a port and retry."
    )


def _phase1(root: Path, a: str, force: bool) -> None:
    typer.echo("\n── Phase 1 — Base Application Scaffold ──────────────────────────────")

    # ── detect free ports at generation time ─────────────────────────────────
    pg_port = _find_free_port(5432)
    redis_port = _find_free_port(6379)
    app_port = _find_free_port(8000)
    if pg_port != 5432:
        typer.echo(f"  ⚠ Port 5432 in use — using {pg_port} for postgres")
    if redis_port != 6379:
        typer.echo(f"  ⚠ Port 6379 in use — using {redis_port} for redis")
    if app_port != 8000:
        typer.echo(f"  ⚠ Port 8000 in use — using {app_port} for app server")

    # ── app factory ──────────────────────────────────────────────────────────
    _write(root / a / "__init__.py", f"""\
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from {a}.config import settings


_REQUIRED_SETTINGS = ["secret_key", "jwt_secret_key", "database_url", "redis_url"]


def _validate_config() -> None:
    missing = [k for k in _REQUIRED_SETTINGS if not getattr(settings, k, None)]
    if missing:
        raise RuntimeError(
            f"Missing required config keys: {{', '.join(missing)}}"
        )


def _register_routers(app: FastAPI) -> None:
    from {a}.routers.api_v1 import register_v1_routers
    register_v1_routers(app)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from {a}.models.database import init_db, close_db
    await init_db()
    yield
    await close_db()


def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.frontend_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["Content-Type", "Authorization"],
    )

    _register_routers(app)
    _validate_config()
    return app
""", force=force)

    # ── config ───────────────────────────────────────────────────────────────
    _write(root / a / "config.py", f"""\
from typing import Annotated
import os

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _resolve_env_file() -> str:
    app_env = (os.getenv("APP_ENV") or "development").strip().lower()
    if app_env == "testing":
        return ".env.testing"
    if app_env == "validation":
        return ".env.validation"
    if app_env == "production":
        return ".env.production"
    return ".env"


class Settings(BaseSettings):
    # Core
    secret_key: str | None = Field(default=None, alias="SECRET_KEY")
    jwt_secret_key: str | None = Field(default=None, alias="JWT_SECRET_KEY")

    # Database - must use asyncpg driver: postgresql+asyncpg://...
    database_url: str | None = Field(default=None, alias="DATABASE_URL")

    # Redis
    redis_url: str | None = Field(default=None, alias="REDIS_URL")
    redis_key_prefix: str = Field(default="{a}", alias="REDIS_KEY_PREFIX")

    # CORS
    frontend_origins: Annotated[list[str], NoDecode] = Field(
        default=["http://localhost:5173"],
        alias="FRONTEND_ORIGINS",
    )

    # JWT
    jwt_access_token_expire_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    jwt_refresh_token_expire_days: int = Field(default=30, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")

    # Environment
    environment: str = Field(default="development", alias="ENVIRONMENT")

    model_config = SettingsConfigDict(
        # Load deterministic env profile selected by APP_ENV.
        # APP_ENV can be: development | testing | validation | production.
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        env_ignore_empty=True,
        extra="ignore",
    )

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def _parse_origins(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",")]
        return v  # type: ignore[return-value]

    @model_validator(mode="after")
    def _require_critical_settings(self):
        required = ["secret_key", "jwt_secret_key", "database_url", "redis_url"]
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            raise ValueError(f"Missing required settings: {{', '.join(missing)}}")
        return self


settings = Settings()
""", force=force)

    # ── models ───────────────────────────────────────────────────────────────
    _write(root / a / "models" / "base" / "base.py", """\
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
""", force=force)

    _touch(root / a / "models" / "base" / "__init__.py", force=force)

    _write(root / a / "models" / "__init__.py", f"""\
from {a}.models.base.base import Base  # noqa: F401

# Import every table module here so Alembic detects schema changes.
# Add one line per domain as you build it:
# from {a}.models.tables.users import user  # noqa: F401
""", force=force)

    _write(root / a / "models" / "database.py", f"""\
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from {a}.config import settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker | None = None


async def init_db() -> None:
    global _engine, _session_factory
    _engine = create_async_engine(
        settings.database_url,
        connect_args={{"server_settings": {{"timezone": "UTC"}}, "timeout": 5}},
        pool_pre_ping=True,
        echo=settings.environment == "development",
    )
    _session_factory = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def close_db() -> None:
    global _engine
    if _engine:
        await _engine.dispose()
        _engine = None


async def get_db() -> AsyncIterator[AsyncSession]:
    \"\"\"FastAPI dependency — one session per request.\"\"\"
    if _session_factory is None:
        raise RuntimeError("DB not initialised — init_db() must run first.")
    async with _session_factory() as session:
        yield session


async def get_db_session() -> AsyncIterator[AsyncSession]:
    \"\"\"Background task helper — same pool, usable outside request context.\"\"\"
    if _session_factory is None:
        raise RuntimeError("DB not initialised — init_db() must run first.")
    async with _session_factory() as session:
        yield session
""", force=force)

    # ── errors ───────────────────────────────────────────────────────────────
    _touch(root / a / "errors" / "__init__.py", force=force)

    _write(root / a / "errors" / "base.py", """\
class DomainError(Exception):
    \"\"\"Only DomainError subclasses cross layer boundaries.\"\"\"
    http_status: int = 500

    def __init__(self, message: str = "An unexpected error occurred.") -> None:
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:
        return self.message
""", force=force)

    _write(root / a / "errors" / "not_found.py", f"""\
from {a}.errors.base import DomainError


class NotFound(DomainError):
    http_status = 404

    def __init__(self, message: str = "Resource not found.") -> None:
        super().__init__(message)
""", force=force)

    _write(root / a / "errors" / "permissions.py", f"""\
from {a}.errors.base import DomainError


class PermissionDenied(DomainError):
    http_status = 403

    def __init__(self, message: str = "You do not have permission to perform this action.") -> None:
        super().__init__(message)


class AuthenticationRequired(DomainError):
    http_status = 401

    def __init__(self, message: str = "Authentication required.") -> None:
        super().__init__(message)
""", force=force)

    _write(root / a / "errors" / "validation.py", f"""\
from {a}.errors.base import DomainError


class ValidationError(DomainError):
    http_status = 422

    def __init__(self, message: str = "Validation failed.") -> None:
        super().__init__(message)


class ConflictError(DomainError):
    http_status = 409

    def __init__(self, message: str = "A conflict occurred.") -> None:
        super().__init__(message)
""", force=force)

    # ── routers ──────────────────────────────────────────────────────────────
    _touch(root / a / "routers" / "__init__.py", force=force)
    _touch(root / a / "routers" / "http" / "__init__.py", force=force)

    _write(root / a / "routers" / "http" / "response.py", f"""\
from fastapi.responses import JSONResponse

from {a}.errors.base import DomainError


def build_ok(data: dict | list | None = None, status_code: int = 200) -> JSONResponse:
    return JSONResponse(content={{"data": data, "ok": True}}, status_code=status_code)


def build_err(error: DomainError) -> JSONResponse:
    return JSONResponse(
        content={{"error": error.message, "ok": False}},
        status_code=error.http_status,
    )
""", force=force)

    _write(root / a / "routers" / "api_v1" / "__init__.py", f"""\
from fastapi import FastAPI

from {a}.routers.api_v1 import health


def register_v1_routers(app: FastAPI) -> None:
    app.include_router(health.router, prefix="/health", tags=["health"])
    # Add domain routers here as you build them:
    # app.include_router(users.router, prefix="/api/v1/users", tags=["users"])
""", force=force)

    _write(root / a / "routers" / "api_v1" / "health.py", f"""\
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from {a}.config import settings
from {a}.models.database import get_db

router = APIRouter()


@router.get("")
async def health_check() -> JSONResponse:
    status: dict = {{"status": "ok", "services": {{}}}}
    ok = True

    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
        status["services"]["db"] = "ok"
    except Exception as exc:
        status["services"]["db"] = f"error: {{exc}}"
        ok = False

    try:
        import redis as _r
        _r.from_url(settings.redis_url).ping()
        status["services"]["redis"] = "ok"
    except Exception as exc:
        status["services"]["redis"] = f"error: {{exc}}"
        ok = False

    status["status"] = "ok" if ok else "degraded"
    return JSONResponse(content=status, status_code=200 if ok else 503)
""", force=force)

    # ── empty directory stubs ─────────────────────────────────────────────────
    for stub in [
        root / a / "domain" / "__init__.py",
        root / a / "services" / "__init__.py",
        root / a / "services" / "commands" / "__init__.py",
        root / a / "services" / "queries" / "__init__.py",
        root / a / "services" / "infra" / "__init__.py",
        root / a / "sockets" / "__init__.py",
    ]:
        _touch(stub, force=force)

    # ── project root files ────────────────────────────────────────────────────
    _write(root / "run.py", f"""\
import uvicorn
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()  # Load .env before reading PORT or any other env var

from scripts.wait_for_services import wait_for_services

if __name__ == "__main__":
    asyncio.run(wait_for_services())
    uvicorn.run(
        "{a}:create_app",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "{app_port}")),
        reload=os.getenv("UVICORN_RELOAD", "1") != "0",
    )
""", force=force)

    _write(root / "requirements.txt", """\
fastapi==0.115.12
uvicorn==0.34.2
pydantic==2.11.3
pydantic-settings==2.9.1
sqlalchemy==2.0.40
greenlet==3.1.1
alembic==1.15.2
asyncpg==0.30.0
python-socketio==5.12.1
PyJWT==2.10.1
bcrypt==4.3.0
passlib[bcrypt]==1.7.4
redis==5.2.1
rq==2.3.2
typer==0.15.3
click==8.1.8
python-ulid==3.0.0
pywebpush==2.0.0
""", force=force)

    _write(root / "requirements-dev.txt", """\
-r requirements.txt
pytest==8.3.5
pytest-asyncio==0.25.3
freezegun==1.5.1
httpx==0.28.1
""", force=force)

    _write(root / ".env.example", f"""\
# Copy to .env and fill in real values. Never commit .env.

SECRET_KEY=replace-me
JWT_SECRET_KEY=replace-me

# Must use asyncpg driver.
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:{pg_port}/{a}

REDIS_URL=redis://localhost:{redis_port}/0
REDIS_KEY_PREFIX={a}

# App server port — auto-detected free port at generation time.
PORT={app_port}

# Docker Compose host ports — auto-detected free ports at generation time.
POSTGRES_PORT={pg_port}
REDIS_PORT={redis_port}

# Comma-separated for multiple origins
FRONTEND_ORIGINS=http://localhost:5173

JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

# development | testing | production
ENVIRONMENT=development

# Optional settings profile selector used by config.py.
# Supported values: development | testing | validation | production
APP_ENV=development
""", force=force)

    _write(root / ".env.local", f"""\
ENVIRONMENT=development
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:{pg_port}/{a}
REDIS_URL=redis://127.0.0.1:{redis_port}/0
REDIS_KEY_PREFIX={a}_local
SECRET_KEY=local-secret
JWT_SECRET_KEY=local-jwt-secret
""", force=force)

    _write(root / ".env.testing", f"""\
ENVIRONMENT=testing
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:{pg_port}/{a}_test
REDIS_URL=redis://127.0.0.1:6380/1
REDIS_KEY_PREFIX={a}_test
SECRET_KEY=testing-secret
JWT_SECRET_KEY=testing-jwt-secret
""", force=force)

    _write(root / ".env.validation", f"""\
ENVIRONMENT=validation
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:{pg_port}/{a}_validation
REDIS_URL=redis://127.0.0.1:6380/2
REDIS_KEY_PREFIX={a}_validation
SECRET_KEY=validation-secret
JWT_SECRET_KEY=validation-jwt-secret
UVICORN_RELOAD=0
""", force=force)

    _write(root / ".env.production", """\
ENVIRONMENT=production

# Provide production values via real environment or secret manager.
# Keep required keys commented so this file never overrides valid local values
# with empty strings when env files are layered.
# DATABASE_URL=
# REDIS_URL=
REDIS_KEY_PREFIX=app_production
# SECRET_KEY=
# JWT_SECRET_KEY=
""", force=force)

    _write(root / ".gitignore", """\
.env
.env.local
.env.testing
.env.validation
.env.production
__pycache__/
*.py[cod]
.venv/
venv/
*.egg-info/
dist/
build/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.log
.DS_Store
""", force=force)

    _write(root / ".python-version", "3.12.3\n", force=force)

    # ── Alembic ───────────────────────────────────────────────────────────────
    _write(root / "alembic.ini", f"""\
[alembic]
script_location = migrations
prepend_sys_path = .
# URL is set programmatically in env.py from settings — leave blank here.
sqlalchemy.url =

[loggers]
keys = root,sqlalchemy,alembic

[handlers]
keys = console

[formatters]
keys = generic

[logger_root]
level = WARN
handlers = console
qualname =

[logger_sqlalchemy]
level = WARN
handlers =
qualname = sqlalchemy.engine

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
datefmt = %H:%M:%S
""", force=force)

    (root / "migrations" / "versions").mkdir(parents=True, exist_ok=True)
    _touch(root / "migrations" / "versions" / ".gitkeep", force=force)

    _write(root / "migrations" / "env.py", f"""\
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Import all models so Alembic detects schema changes.
from {a}.models import Base  # noqa: F401
from {a}.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={{"paramstyle": "named"}},
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {{}}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
""", force=force)

    # Mako template — no f-string here to avoid escaping ${} syntax
    _write(root / "migrations" / "script.py.mako", '''\
"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
${imports if imports else ""}

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def upgrade() -> None:
    ${upgrades if upgrades else "pass"}


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
''', force=force)

    _write(root / "docker-compose.yml", f"""\
services:
  postgres:
    image: postgres:17
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: {a}
    ports:
      - "${{POSTGRES_PORT:-{pg_port}}}:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d {a}"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - postgres_data:/var/lib/postgresql/data

  redis:
    image: redis:7
    ports:
      - "${{REDIS_PORT:-{redis_port}}}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 5s
      retries: 20
    volumes:
      - redis_data:/data

volumes:
  postgres_data:
  redis_data:
""", force=force)

    _write(root / "Makefile", """\
.PHONY: dev-up dev-down dev-logs db-init db-migrate run help

help:
\t@echo "Development targets:"
\t@echo "  make dev-up       - Start Docker services (postgres + redis)"
\t@echo "  make dev-logs     - Stream Docker logs"
\t@echo "  make dev-down     - Stop Docker services"
\t@echo "  make db-init      - Wait for database to be ready"
\t@echo "  make db-migrate   - Run Alembic migrations"
\t@echo "  make run          - Start the FastAPI app with auto-reload"
\t@echo ""
\t@echo "Full workflow:"
\t@echo "  make dev-up && make db-init && make db-migrate && make run"

dev-up:
\tdocker compose up -d --wait

dev-down:
\tdocker compose down

dev-logs:
\tdocker compose logs -f

db-init:
\tPYTHONPATH=. APP_ENV=development python -m scripts.wait_for_services

db-migrate:
\tAPP_ENV=development alembic upgrade head

run:
\tAPP_ENV=development python run.py
""", force=force)

    _write(root / "README.md", f"""\
# {a}

## Local Development Services

Start PostgreSQL 17 and Redis 7:

```bash
docker compose up -d
```

The same commands are available through make:

```bash
make dev-up
make dev-logs
make dev-down
```

Copy the example environment before running app commands:

```bash
cp .env.example .env
```

Run migrations after the services are healthy:

```bash
APP_ENV=development alembic upgrade head
```

Start the FastAPI app:

```bash
python run.py
```

The app waits for PostgreSQL and Redis before Uvicorn starts. Startup fails if
`DATABASE_URL` is missing, Redis is unreachable, or the configured database is
not reachable.

## Bootstrap Validation

After installing dependencies, run:

```bash
python scripts/validate_bootstrap.py
```

The validation script starts Docker Compose services, creates the database if it
is missing, runs `alembic upgrade head`, starts FastAPI, and verifies `/health`
returns HTTP 200 with both DB and Redis connectivity marked `ok`.
""", force=force)

    _touch(root / "scripts" / "__init__.py", force=force)
    _write(root / "scripts" / "wait_for_services.py", f"""\
import asyncio
import time

import redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from {a}.config import settings


async def _check_db() -> None:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is missing.")
    engine = create_async_engine(
        settings.database_url,
        connect_args={{"timeout": 5}},
        pool_pre_ping=True,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def _check_redis() -> None:
    if not settings.redis_url:
        raise RuntimeError("REDIS_URL is missing.")
    redis.from_url(settings.redis_url, decode_responses=True).ping()


async def wait_for_services(timeout_seconds: int = 60, interval_seconds: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            await _check_db()
            _check_redis()
            return
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(interval_seconds)

    raise RuntimeError(f"Timed out waiting for database and Redis: {{last_error}}")


if __name__ == "__main__":
    asyncio.run(wait_for_services())
""", force=force)

    _write(root / "scripts" / "validate_bootstrap.py", """\
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "__APP_NAME__"


def _run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=check,
        text=True,
        env=env,
        stdout=None,
        stderr=None,
    )


def _capture(cmd: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=ROOT,
        check=False,
        text=True,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _ensure_env_file() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        return
    example_path = ROOT / ".env.example"
    if not example_path.exists():
        raise RuntimeError(".env.example is missing.")
    shutil.copyfile(example_path, env_path)


def _read_env_value(key: str) -> str:
    if os.environ.get(key):
        return os.environ[key]
    env_path = ROOT / ".env"
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip() == key:
            return value.strip().strip('"').strip("'")
    return ""


def _require_env() -> None:
    if not _read_env_value("DATABASE_URL"):
        raise RuntimeError("DATABASE_URL is missing.")
    if not _read_env_value("REDIS_URL"):
        raise RuntimeError("REDIS_URL is missing.")


def _require_docker_compose() -> None:
    if shutil.which("docker") is None:
        raise RuntimeError("Docker CLI is not installed or is not on PATH.")
    result = _capture(["docker", "compose", "version"])
    if result.returncode != 0:
        raise RuntimeError(f"Docker Compose is unavailable: {result.stderr.strip()}")


def _pick_host_port(preferred: int) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", preferred))
            return str(preferred)
        except OSError:
            pass

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def _validation_env() -> dict[str, str]:
    env = os.environ.copy()
    postgres_port = env.get("POSTGRES_PORT") or _pick_host_port(5432)
    redis_port = env.get("REDIS_PORT") or _pick_host_port(6379)
    app_port = env.get("PORT") or _pick_host_port(5000)
    env["POSTGRES_PORT"] = postgres_port
    env["REDIS_PORT"] = redis_port
    env["PORT"] = app_port
    env["DATABASE_URL"] = f"postgresql+asyncpg://postgres:postgres@127.0.0.1:{postgres_port}/{APP_NAME}"
    env["REDIS_URL"] = f"redis://127.0.0.1:{redis_port}/0"
    env["UVICORN_RELOAD"] = "0"
    return env


def _wait_for_compose_services(env: dict[str, str], timeout_seconds: int = 90) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        pg = _capture(["docker", "compose", "exec", "-T", "postgres", "pg_isready", "-U", "postgres", "-d", APP_NAME], env=env)
        redis = _capture(["docker", "compose", "exec", "-T", "redis", "redis-cli", "ping"], env=env)
        if pg.returncode == 0 and redis.returncode == 0 and "PONG" in redis.stdout:
            return
        last_error = (pg.stderr + redis.stderr + pg.stdout + redis.stdout).strip()
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for Docker Compose services: {last_error}")


def _create_database_if_missing(env: dict[str, str]) -> None:
    query = f"SELECT 1 FROM pg_database WHERE datname = '{APP_NAME}'"
    result = _capture(["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "postgres", "-tAc", query], env=env)
    if result.returncode != 0:
        raise RuntimeError(f"Failed to inspect PostgreSQL databases: {result.stderr.strip()}")
    if result.stdout.strip() == "1":
        return
    _run(["docker", "compose", "exec", "-T", "postgres", "createdb", "-U", "postgres", APP_NAME], env=env)


def _wait_for_health(env: dict[str, str], timeout_seconds: int = 60) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    url = f"http://127.0.0.1:{env['PORT']}/health"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                body = response.read().decode("utf-8")
                data = json.loads(body)
                if response.status == 200:
                    return data
                last_error = f"HTTP {response.status}: {body}"
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise RuntimeError(f"FastAPI health check did not become healthy: {last_error}")


def main() -> None:
    _ensure_env_file()
    _require_env()
    _require_docker_compose()
    env = _validation_env()
    _run(["docker", "compose", "up", "-d"], env=env)
    _wait_for_compose_services(env)
    _create_database_if_missing(env)
    _run([sys.executable, "-m", "scripts.wait_for_services"], env=env)
    _run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env)

    app = subprocess.Popen([sys.executable, "run.py"], cwd=ROOT, env=env, text=True)
    try:
        data = _wait_for_health(env)
        services = data.get("services", {})
        if services.get("db") != "ok":
            raise RuntimeError(f"DB health check failed: {services.get('db')}")
        if services.get("redis") != "ok":
            raise RuntimeError(f"Redis health check failed: {services.get('redis')}")
        print("Bootstrap validation passed.")
    finally:
        app.terminate()
        try:
            app.wait(timeout=10)
        except subprocess.TimeoutExpired:
            app.kill()
            app.wait(timeout=10)


if __name__ == "__main__":
    main()
""".replace("__APP_NAME__", a), force=force)

    _touch(root / "scripts" / ".gitkeep", force=force)
