# 02 — App Factory & Configuration Contract

## App factory

The application is created by a single `create_app()` function in `my_app/__init__.py`. There is no module-level `FastAPI` instance.

```python
# my_app/__init__.py
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from my_app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging
    from my_app.models.database import init_db, close_db
    await init_db()
    _start_socket_pubsub(app)
    logging.getLogger("my_app.startup").info(
        "startup | env=%s database_url=%s redis_url=%s "
        "db_pool_size=%d db_max_overflow=%d db_pool_recycle=%d",
        settings.environment,
        settings.database_url,
        settings.redis_url,
        settings.db_pool_size,
        settings.db_max_overflow,
        settings.db_pool_recycle,
    )
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
    _register_websocket_handlers(app)
    _validate_config()

    return app
```

**Rules:**
- `create_app` is the only entry point. No global `app = FastAPI()` at module level.
- All startup and shutdown logic lives in the `lifespan` context manager — not in `@app.on_event` hooks (deprecated in FastAPI).
- Extensions (DB engine, Redis) are initialized inside `lifespan`, not at import time.
- Router registration happens via `_register_routers(app)`. Not inline.
- `_validate_config()` runs last in `create_app` — fails loud before the first connection is accepted.

---

## Configuration contract

Config is managed by a single `Settings` class using `pydantic-settings`. It reads from environment variables and an optional `.env` file.

```
config.py   # single Settings class — replaces the per-environment class hierarchy
```

```python
# config.py
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
import os


class Settings(BaseSettings):
    # Core
    secret_key:     str = "devkey"
    jwt_secret_key: str = ""

    # Database — must use asyncpg driver: postgresql+asyncpg://...
    database_url: str = ""

    # Redis
    redis_uri:        str | None = None
    redis_key_prefix: str = "my_app"

    # CORS
    frontend_origins: list[str] = ["http://localhost:5173"]

    # JWT
    jwt_access_token_expire_minutes:  int = 30
    jwt_refresh_token_expire_days:    int = 30

    # Database connection pool
    db_pool_size:    int = Field(10,   alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(20,   alias="DB_MAX_OVERFLOW")
    db_pool_recycle: int = Field(1800, alias="DB_POOL_RECYCLE")

    # Idle sleep mode — see 22_performance.md
    sleep_mode_enabled:           bool = Field(True,  alias="SLEEP_MODE_ENABLED")
    idle_sleep_threshold_seconds: int  = Field(600,   alias="IDLE_SLEEP_THRESHOLD_SECONDS")

    # Environment
    environment: str = "development"   # "development" | "testing" | "production"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v


settings = Settings()
```

- One `settings` singleton imported everywhere — `from my_app.config import settings`.
- `pydantic-settings` validates types at startup. A wrong type in `.env` (e.g., a non-integer for an `int` field) raises at import time, not silently at runtime.
- Boolean env vars: declare as `bool` in `Settings` — pydantic-settings correctly parses `"true"` / `"false"` strings.
- Integer env vars: declare as `int` — no `int(os.environ.get(...))` needed.
- `frontend_origins` is a comma-separated string in `.env` (`FRONTEND_ORIGINS=http://localhost:5173,https://app.example.com`). The validator splits it into a list.

---

## Async database setup

```python
# models/database.py
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from typing import AsyncIterator
from my_app.config import settings

_engine: AsyncEngine | None = None
_AsyncSessionLocal: async_sessionmaker | None = None


async def init_db() -> None:
    global _engine, _AsyncSessionLocal
    _engine = create_async_engine(
        settings.database_url,
        connect_args={"server_settings": {"timezone": "UTC"}},
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_recycle=settings.db_pool_recycle,
        pool_timeout=30,
        pool_pre_ping=True,
        echo=settings.environment == "development",
    )
    _AsyncSessionLocal = async_sessionmaker(
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
    """FastAPI dependency — yields one AsyncSession per request."""
    async with _AsyncSessionLocal() as session:
        yield session
```

**Rules:**
- `database_url` must use the `asyncpg` driver: `postgresql+asyncpg://user:pass@host/db`.
- `expire_on_commit=False` prevents SQLAlchemy from expiring loaded attributes after a commit — required in async because lazy-loading after expiry would raise `MissingGreenlet`.
- `pool_pre_ping=True` validates connections before use — essential for long-idle production deployments.
- `get_db` is a FastAPI dependency injected into route handlers via `Depends(get_db)`. One session per request; the `async with` ensures it is closed after the response is sent.

---

## Router registration

```python
# my_app/__init__.py
from my_app.routers.api_v1 import register_v1_routers
from my_app.sockets.handlers import router as ws_router


def _register_routers(app: FastAPI) -> None:
    register_v1_routers(app)


def _register_websocket_handlers(app: FastAPI) -> None:
    app.include_router(ws_router)
```

```python
# routers/api_v1/__init__.py
from fastapi import FastAPI
from .record import router as record_router
from .auth   import router as auth_router


def register_v1_routers(app: FastAPI) -> None:
    app.include_router(record_router, prefix="/api/v1/records", tags=["records"])
    app.include_router(auth_router,   prefix="/api/v1/auth",    tags=["auth"])
```

---

## Middleware contract

FastAPI middleware is added via `app.add_middleware()`. ASGI middleware classes, not `before_request` / `after_request` hooks.

**Standard middleware stack (order matters — registered in reverse execution order):**

```python
# my_app/__init__.py — inside create_app()
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from my_app.routers.middleware.no_cache import NoCacheMiddleware
from my_app.routers.middleware.sleep import SleepMiddleware
from my_app.routers.middleware.timeout import TimeoutMiddleware

# Registered last → executes first on request, last on response
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.frontend_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization"],
)

# Gzip: compresses responses > 1 KB — typically 70–80% reduction for JSON
app.add_middleware(GZipMiddleware, minimum_size=1000)

# No-cache: sets Cache-Control: no-store on all /api/ responses
app.add_middleware(NoCacheMiddleware)

# Sleep: touches ActivityTracker on every request — wakes the app if it is sleeping
app.add_middleware(SleepMiddleware)

# Timeout: enforces a hard deadline on every request — prevents slow clients
# or DB deadlocks from holding a uvicorn worker indefinitely
app.add_middleware(TimeoutMiddleware)
```

**`TimeoutMiddleware` implementation:**

```python
# routers/middleware/timeout.py
import asyncio
import os
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from fastapi.responses import JSONResponse

REQUEST_TIMEOUT_SECONDS = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30"))


class TimeoutMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, timeout: int = REQUEST_TIMEOUT_SECONDS):
        super().__init__(app)
        self.timeout = timeout

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except asyncio.TimeoutError:
            return JSONResponse(
                {"detail": "Request timed out."},
                status_code=504,
            )
```

**Rules:**
- `REQUEST_TIMEOUT_SECONDS` defaults to 30. Override via env var for specific deployments — never hardcode.
- File upload endpoints must be excluded or given a longer timeout. Override by not running the timeout check when `request.url.path.startswith("/api/v1/files/")`.
- The timeout fires the full ASGI cancel path — background tasks spawned inside the handler are also cancelled. Long-running work must use the task queue (see [16_background_jobs.md](16_background_jobs.md)), not inline async work.
- WebSocket connections are not subject to this middleware — their lifecycle is managed by the socket handler.

Middleware registered here applies to all routes. Route-specific header overrides belong in the route handler. See [22_performance.md](22_performance.md) for the `NoCacheMiddleware` implementation.

---

## Health check endpoint

```python
# routers/api_v1/health.py
from fastapi import APIRouter
from sqlalchemy import text
from my_app.models.database import get_db
from my_app.services.infra.redis import assert_redis_available
from my_app.config import settings

router = APIRouter()


@router.get("/")
async def health():
    status = {"status": "ok", "services": {}}
    ok = True

    try:
        async for session in get_db():
            await session.execute(text("SELECT 1"))
        status["services"]["db"] = "ok"
    except Exception as e:
        status["services"]["db"] = f"error: {e}"
        ok = False

    try:
        assert_redis_available(settings.redis_uri)
        status["services"]["redis"] = "ok"
    except Exception as e:
        status["services"]["redis"] = f"error: {e}"
        ok = False

    status["status"] = "ok" if ok else "degraded"
    from fastapi.responses import JSONResponse
    return JSONResponse(content=status, status_code=200 if ok else 503)
```

Health checks use standard HTTP 200 / 503. `except Exception` is acceptable here — health checks must never crash.

---

## Startup validation

```python
# my_app/__init__.py

_REQUIRED_IN_PRODUCTION = [
    "secret_key",
    "jwt_secret_key",
    "database_url",
    "redis_uri",
    "frontend_origins",
]


def _validate_config() -> None:
    if settings.environment == "testing":
        return

    if settings.environment == "production":
        missing = [k for k in _REQUIRED_IN_PRODUCTION if not getattr(settings, k)]
        if missing:
            raise RuntimeError(
                f"Missing required production config keys: {', '.join(missing)}"
            )
```

- Validation is skipped in `testing` — tests provide minimal config.
- In production, any missing required key raises `RuntimeError` before the server accepts connections.
- Development tolerates missing optional keys — no validation fires.

---

## Uvicorn configuration

Production deployments run under `uvicorn` with multiple workers. The uvicorn configuration lives in `uvicorn.conf.py` at the project root:

```python
# uvicorn.conf.py  (used via --config flag or imported by a process manager)
import multiprocessing

workers = multiprocessing.cpu_count() * 2 + 1
host    = "0.0.0.0"
port    = 5000
```

Start command (production):

```bash
uvicorn my_app:app --workers 4 --host 0.0.0.0 --port 5000
```

For apps that export a `create_app` factory instead of a module-level `app`:

```bash
uvicorn "my_app:create_app" --factory --workers 4 --host 0.0.0.0 --port 5000
```

Development:

```bash
uvicorn "my_app:create_app" --factory --reload --port 5000
```

**Rules:**
- Never use `eventlet` or `gevent` workers — uvicorn is a native ASGI server; no compatibility layer is needed.
- `--workers` is derived from CPU count, never hardcoded.
- `--reload` is for development only. Never use it in production.
- WebSocket connections work natively with uvicorn — no special worker class required.

---

## run.py (development entry point)

```python
# run.py
import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "my_app:create_app",
        factory=True,
        host="0.0.0.0",
        port=5000,
        reload=True,
    )
```
