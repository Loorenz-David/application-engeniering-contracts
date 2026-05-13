from pathlib import Path

import typer

from bootstrap.writer import append_once, replace_once, touch_file as _touch, write_file as _write


def _phase10_observability(root: Path, a: str, force: bool) -> None:
    typer.echo("\n-- Phase 11 - Observability ----------------------------------------")

    _touch(root / a / "core" / "__init__.py", force=force)
    _touch(root / a / "core" / "logging" / "__init__.py", force=force)
    _touch(root / a / "core" / "observability" / "__init__.py", force=force)

    _write(root / a / "core" / "logging" / "context.py", """\
from __future__ import annotations

from contextvars import ContextVar
from uuid import uuid4

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_execution_id: ContextVar[str | None] = ContextVar("execution_id", default=None)
_worker_id: ContextVar[str | None] = ContextVar("worker_id", default=None)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def ensure_correlation_id() -> str:
    value = _correlation_id.get()
    if value:
        return value
    value = _new_id("corr")
    _correlation_id.set(value)
    return value


def bind_request_context(*, correlation_id: str | None = None, request_id: str | None = None) -> tuple[str, str]:
    corr = correlation_id or ensure_correlation_id()
    req = request_id or _new_id("req")
    _correlation_id.set(corr)
    _request_id.set(req)
    return corr, req


def bind_execution_context(execution_id: str | None = None, worker_id: str | None = None) -> tuple[str, str]:
    eid = execution_id or _new_id("exec")
    wid = worker_id or _new_id("wrk")
    _execution_id.set(eid)
    _worker_id.set(wid)
    return eid, wid


def clear_context() -> None:
    _correlation_id.set(None)
    _request_id.set(None)
    _execution_id.set(None)
    _worker_id.set(None)


def get_log_context() -> dict[str, str | None]:
    return {
        "correlation_id": _correlation_id.get(),
        "request_id": _request_id.get(),
        "execution_id": _execution_id.get(),
        "worker_id": _worker_id.get(),
    }
""", force=force)

    _write(root / a / "core" / "logging" / "formatter.py", """\
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .context import get_log_context


class StructuredJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event_type": getattr(record, "event_type", record.msg),
            "message": record.getMessage(),
            "duration_ms": getattr(record, "duration_ms", None),
        }
        payload.update(get_log_context())

        for key in ("service", "path", "method", "status_code", "error", "db_health", "redis_health"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value

        return json.dumps(payload, ensure_ascii=True)
""", force=force)

    _write(root / a / "core" / "logging" / "config.py", f"""\
from __future__ import annotations

import logging
import logging.config

from {a}.config import settings


_CONFIGURED = False


def configure_logging() -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    logging.config.dictConfig(
        {{
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {{
                "json": {{
                    "()": "{a}.core.logging.formatter.StructuredJsonFormatter",
                }}
            }},
            "handlers": {{
                "default": {{
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                }}
            }},
            "root": {{
                "level": "INFO",
                "handlers": ["default"],
            }},
        }}
    )
    _CONFIGURED = True


def log_event(event_type: str, **extra: object) -> None:
    logger = logging.getLogger("app")
    payload = {{"event_type": event_type, "service": settings.redis_key_prefix}}
    payload.update(extra)
    logger.info(event_type, extra=payload)
""", force=force)

    _write(root / a / "core" / "logging" / "middleware.py", f"""\
from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware

from {a}.core.logging.config import log_event
from {a}.core.logging.context import bind_request_context, clear_context


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        corr = request.headers.get("x-correlation-id")
        _, request_id = bind_request_context(correlation_id=corr)
        started = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log_event(
                "http.request.error",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                error=str(exc),
            )
            clear_context()
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["x-request-id"] = request_id
        if corr:
            response.headers["x-correlation-id"] = corr

        log_event(
            "http.request.completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )
        clear_context()
        return response
""", force=force)

    _write(root / a / "core" / "observability" / "runtime.py", f"""\
from __future__ import annotations

from {a}.core.logging.config import log_event


def log_startup() -> None:
    log_event("runtime.startup")


def log_shutdown() -> None:
    log_event("runtime.shutdown")


def log_health(db_health: str, redis_health: str) -> None:
    log_event("runtime.health", db_health=db_health, redis_health=redis_health)
""", force=force)

    replace_once(
        root / a / "__init__.py",
        "def create_app() -> FastAPI:\n    app = FastAPI(lifespan=lifespan)\n",
        "def create_app() -> FastAPI:\n    from "
        f"{a}.core.logging.config import configure_logging\n"
        f"    from {a}.core.logging.middleware import RequestContextMiddleware\n"
        "\n"
        "    configure_logging()\n"
        "    app = FastAPI(lifespan=lifespan)\n"
        "    app.add_middleware(RequestContextMiddleware)\n",
    )

    replace_once(
        root / a / "__init__.py",
        "@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    from "
        f"{a}.models.database import init_db, close_db\n"
        "    await init_db()\n"
        "    yield\n"
        "    await close_db()\n",
        "@asynccontextmanager\nasync def lifespan(app: FastAPI):\n    from "
        f"{a}.core.observability.runtime import log_shutdown, log_startup\n"
        f"    from {a}.models.database import close_db, init_db\n"
        "\n"
        "    log_startup()\n"
        "    await init_db()\n"
        "    yield\n"
        "    await close_db()\n"
        "    log_shutdown()\n",
    )

    append_once(
        root / a / "routers" / "api_v1" / "health.py",
        "\n\n# Observability runtime health logging\n"
        f"from {a}.core.observability.runtime import log_health\n",
    )
    replace_once(
        root / a / "routers" / "api_v1" / "health.py",
        '    status["status"] = "ok" if ok else "degraded"\n    return JSONResponse(content=status, status_code=200 if ok else 503)\n',
        '    status["status"] = "ok" if ok else "degraded"\n'
        '    log_health(status["services"].get("db", "unknown"), status["services"].get("redis", "unknown"))\n'
        '    return JSONResponse(content=status, status_code=200 if ok else 503)\n',
    )
