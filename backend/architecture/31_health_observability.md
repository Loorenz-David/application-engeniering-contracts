# 31 — Health Checks & Observability Contract

## What this covers

Health checks tell the infrastructure whether the application is ready to serve traffic. Observability tells the team what the application is doing at runtime. These are operational requirements, not features — they must be wired before the first domain goes to production.

---

## The three health endpoints

Every application exposes exactly three health endpoints. They are unversioned and require no authentication.

| Endpoint | Purpose | Used by |
|---|---|---|
| `GET /health` | Deep check — all dependencies | Monitoring dashboards, oncall alerts |
| `GET /ready` | Readiness — safe to receive traffic | Load balancer, Kubernetes readiness probe |
| `GET /live` | Liveness — process is running | Kubernetes liveness probe, process supervisors |

### `/health` — full dependency check

Returns `200` only when all critical dependencies are reachable. Returns `503` with a breakdown when any dependency is unhealthy.

```python
# routers/api_v1/health.py
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from my_app.models.database import get_db
from my_app.services.infra.redis import assert_redis_available
from my_app.config import settings

router = APIRouter()


@router.get("/health")
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
    return JSONResponse(content=status, status_code=200 if ok else 503)
```

### `/ready` — readiness probe

The readiness check answers: "Is this instance ready to serve user traffic?" It is stricter than `/live`. A pod is not ready if:
- Migrations have not been applied (`alembic current` is behind head)
- A required cache warmup has not completed
- A required background worker is not registered

```python
@router.get("/ready")
async def ready():
    try:
        # Fast DB check — confirms connection pool is usable
        async for session in get_db():
            await session.execute(text("SELECT 1"))
        return JSONResponse(content={"status": "ready"}, status_code=200)
    except Exception:
        return JSONResponse(content={"status": "not ready"}, status_code=503)
```

### `/live` — liveness probe

The liveness check answers: "Is the process alive and not deadlocked?" It must return in under 100ms. It must not check external dependencies — a dependency outage should not restart healthy pods.

```python
@router.get("/live")
async def live():
    return {"status": "alive"}
```

---

## Health check registration

Register health endpoints in `create_app()` before any authentication middleware:

```python
def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    # Health endpoints registered first — no auth applied to them
    from .routers.api_v1.health import router as health_router
    app.include_router(health_router)
    ...
```

Health endpoints must never depend on `get_jwt_claims` or `require_roles`. An infrastructure probe does not carry a JWT.

---

## What "healthy" means per dependency

| Dependency | Healthy definition | Unhealthy consequence |
|---|---|---|
| PostgreSQL | `SELECT 1` returns in < 500ms | `503` — no reads or writes possible |
| Redis | `PING` returns `PONG` in < 200ms | `503` — auth blocklist, rate limiting, caching fail |
| RQ worker | At least one worker registered in the queue | Degraded — background jobs queue but do not process |
| External provider | Not checked in `/health` — use circuit breaker | Surfaced in logs and metrics, not health endpoint |

Do not check external providers (SMS, email, payment) in `/health`. Their outage should be tolerated by the circuit breaker pattern, not cause the pod to be marked unhealthy.

---

## Structured metrics

Metrics are emitted as structured log lines that a log aggregator (Datadog, Grafana Loki, CloudWatch) can parse into time-series data. Do not require a Prometheus client or push gateway unless the infrastructure team explicitly requires it.

Every request emits a metric log line via the logging middleware (see [17_logging.md](17_logging.md)):

```
INFO  request_completed | method=POST path=/api/v1/records status=201 duration_ms=47 workspace=7 user=42
```

### Counters to track

The following counters must be derivable from log output:

| Metric | Log field | Alert threshold |
|---|---|---|
| Request rate | `method` + `path` + `status` | Spike > 5× baseline in 5 min |
| Error rate | `status >= 500` | > 1% of requests in 5 min |
| Auth failure rate | `status=401` or `status=403` | > 50 in 1 min (brute force signal) |
| Slow requests | `duration_ms > 2000` | > 5% of requests in 5 min |
| Failed outbox events | `status=failed` in outbox | Count > 10 in 15 min |
| Background job failures | `job_status=failed` in job log | Any failure in critical queues |

### Timing instrumentation

Wrap slow or external-call paths with explicit timing:

```python
import time
import logging

logger = logging.getLogger(__name__)


def timed_external_call(label: str, fn, *args, **kwargs):
    start = time.monotonic()
    try:
        result = fn(*args, **kwargs)
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.info("external_call_ok | label=%s duration_ms=%d", label, elapsed_ms)
        return result
    except Exception:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        logger.error("external_call_failed | label=%s duration_ms=%d", label, elapsed_ms)
        raise
```

---

## Application info endpoint

Expose a read-only info endpoint for deployment verification:

```python
@router.get("/info")
async def info():
    return {
        "app": settings.app_name,
        "env": settings.environment,
        "version": settings.app_version,
    }
```

`APP_VERSION` is set at deploy time (e.g., the git SHA or release tag). This allows operators to confirm which version is running on each instance without SSH access.

---

## Alerting rules

| Alert | Condition | Severity | Action |
|---|---|---|---|
| `/health` returns 503 | Any dependency check fails | Critical | Page oncall immediately |
| Error rate elevated | > 1% 5xx in 5 min | High | Investigate; consider rollback |
| Auth spike | > 50 401/403 in 1 min | High | Check for credential stuffing |
| Slow p95 | p95 latency > 2s sustained | Medium | Investigate N+1 or cache miss |
| Failed outbox accumulating | > 10 failed rows in 15 min | Medium | Check handler logs; replay after fix |
| No workers | Queue depth growing, no dispatch | Critical | Restart workers |

---

## What observability must NOT do

- **Never expose PII in health responses.** No user data, no workspace names, no record counts.
- **Never expose stack traces in health responses.** Use structured error keys only.
- **Never block requests to `/live` or `/ready` on business logic.** These are infrastructure probes, not API endpoints.
- **Never require auth on any health endpoint.** The load balancer has no JWT.
