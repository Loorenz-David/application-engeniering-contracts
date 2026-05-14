# 22 — Performance Contract

## The core rule

A feature that works correctly at 10 rows must work correctly at 100,000 rows. Performance problems discovered in production are ten times harder to fix than those prevented by design. These rules are enforced at code review, not after the fact.

---

## N+1 prevention

An N+1 problem occurs when code issues one query to load N parent records, then issues N additional queries to load each parent's related data. It is the most common performance bug in SQLAlchemy applications.

**Detection rule:** If a serializer accesses `instance.relationship_name` for items in a list, that relationship must be eagerly loaded in the query.

```python
# N+1 bug — one query for records, then one per record to load children
def serialize_record(record: Record) -> dict:
    return {
        "client_id": record.client_id,
        "items": [serialize_item(i) for i in record.line_items],  # triggers N queries
    }

# Correct — declare the need in the query
def list_records(ctx: ServiceContext) -> dict:
    query = db.session.query(Record).options(
        selectinload(Record.line_items),     # one additional query, not N
        selectinload(Record.attachments),
    ).filter(Record.workspace_id == ctx.workspace_id)
    ...
```

**Rule:** If `selectinload` or `joinedload` is not present on the query, accessing a relationship in the serializer is forbidden.

---

## Query limits — no unbounded queries

Every query that returns a list must have a `LIMIT`. No exceptions:

```python
# Wrong — returns every record for the workspace with no ceiling
results = db.session.query(Record).filter(Record.workspace_id == ctx.workspace_id).all()

# Correct — always bounded
MAX_LIMIT = 200
limit = min(int(ctx.query_params.get("limit", 50)), MAX_LIMIT)
results = query.limit(limit + 1).all()
```

If a feature genuinely requires all records (e.g., a data export), it must:
1. Be a background job, not a synchronous HTTP call
2. Stream or paginate internally using cursor pagination
3. Not hold a DB transaction open while processing

---

## Pagination is mandatory on all list endpoints

Refer to [07_queries.md](07_queries.md) for the pagination implementation. This rule enforces the ceiling:

| Entity | Default limit | Maximum limit |
|---|---|---|
| Standard records | 50 | 200 |
| Child items (on a parent record) | all | 500 |
| Analytics rows | 90 | 365 |
| History / audit log entries | 50 | 200 |

If a use case genuinely requires more, document the justification and raise the cap in config, not hardcoded.

---

## Select only what you need

Avoid `SELECT *` by default. For heavy read paths (analytics, large list queries), use column-level selection:

```python
from sqlalchemy import select

# When you only need IDs and statuses for a bulk operation
stmt = select(Record.client_id, Record.state_id).where(
    Record.workspace_id == ctx.workspace_id,
    Record.category_id == category_id,
)
rows = db.session.execute(stmt).all()
```

Only use `db.session.query(Record)` (full model load) when the serializer needs multiple columns or relationships.

---

## Bulk writes

When inserting or updating many records, use bulk operations rather than adding one instance at a time. **Never use `bulk_insert_mappings()` — it is a legacy synchronous API that bypasses the async engine and blocks the event loop.**

```python
# Slow — individual INSERT per item
for item_data in items_data:
    session.add(Item(**item_data))

# Fast — single INSERT with many values (SQLAlchemy 2.x async)
from sqlalchemy import insert

await session.execute(insert(Item), items_data)
await session.commit()
```

To capture generated `client_id` values from a bulk insert, use `INSERT … RETURNING`:

```python
from sqlalchemy.dialects.postgresql import insert as pg_insert

result = await session.execute(
    pg_insert(Item).returning(Item.client_id),
    items_data,   # list of dicts — one per row
)
ids = result.scalars().all()
```

For updates across many rows with the same change, use a single `UPDATE` statement:

```python
from sqlalchemy import update

await session.execute(
    update(Record)
    .where(Record.category_id == category_id, Record.workspace_id == ctx.workspace_id)
    .values(state_id=new_state_id)
)
```

---

## Transaction duration

Database transactions must be as short as possible. Never hold a transaction open while:
- Waiting for an external HTTP call
- Sleeping or polling
- Doing heavy in-memory computation unrelated to the DB write

Pattern for keeping transactions short:

```python
def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)

    # All preparation before the transaction
    external_result = call_external_api(request.external_ref)  # external call — before transaction

    pending_events = []

    with db.session.begin():                              # transaction opens here
        record = Record(...)
        db.session.add(record)
        db.session.flush()
        pending_events.append(build_record_created_event(record))
                                                          # transaction commits here

    emit_record_events(ctx, pending_events)               # after commit
    return serialize_created_record(record)
```

---

## Caching rules

Cache only what is:
1. **Expensive to compute** (not cheap DB reads)
2. **Stable over a known TTL** (data that changes slowly)
3. **Safe to serve slightly stale** (degraded data is acceptable)

Cache keys always include the workspace ID. Never cache data that crosses workspace boundaries.

| What to cache | TTL | Key pattern |
|---|---|---|
| Pending notifications | 48h | `{prefix}:notification:pending:{workspace_id}` |
| Expensive computation result | 5m | `{prefix}:{domain}:result:{entity_client_id}` |
| AI tool response for idempotency | 30d | `{prefix}:ai:proposal:{workspace_id}:{client_id}` |

**Never cache:** Authentication data, permission checks, financial transactions, or anything that must be consistent with the DB in real time.

---

## Database connection pool

Pool settings come from config, not hardcoded. Add these to `Settings`:

```python
# config.py
db_pool_size:     int = Field(10,   alias="DB_POOL_SIZE")
db_max_overflow:  int = Field(20,   alias="DB_MAX_OVERFLOW")
db_pool_recycle:  int = Field(1800, alias="DB_POOL_RECYCLE")   # seconds
```

Wire them into `init_db()`:

```python
# models/database.py
_engine = create_async_engine(
    settings.database_url,
    connect_args={"server_settings": {"timezone": "UTC"}},
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    pool_timeout=30,       # seconds to wait before raising — keep fixed
    pool_pre_ping=True,    # validate connection before use
    echo=settings.environment == "development",
)
```

`pool_pre_ping=True` prevents `OperationalError: server closed the connection unexpectedly` after idle periods. Always enable in production.

**Sizing rule:** `pool_size` = uvicorn workers × 2 + 2 for a single-node deployment. Default `10 / 20` handles 4-worker setups. Increase via `DB_POOL_SIZE` env var — no code change required.

---

## Slow query logging

Attach a SQLAlchemy event listener to log queries above a threshold. This fires in the same async context as the query — no extra overhead:

```python
# models/database.py — inside init_db(), after engine creation
import time
import logging

_perf_logger = logging.getLogger("sqlalchemy.perf")
_SLOW_QUERY_MS = int(os.environ.get("SLOW_QUERY_THRESHOLD_MS", "500"))


@event.listens_for(_engine.sync_engine, "before_cursor_execute")
def _before(conn, cursor, statement, parameters, context, executemany):
    conn.info.setdefault("_query_start", time.monotonic())


@event.listens_for(_engine.sync_engine, "after_cursor_execute")
def _after(conn, cursor, statement, parameters, context, executemany):
    elapsed_ms = (time.monotonic() - conn.info.pop("_query_start", time.monotonic())) * 1000
    if elapsed_ms >= _SLOW_QUERY_MS:
        _perf_logger.warning(
            "slow_query | elapsed_ms=%.1f | %s",
            elapsed_ms,
            statement[:200],
        )
```

The `event` import is `from sqlalchemy import event`. `_engine.sync_engine` bridges the async engine to the synchronous event API.

**Policy:** Any query that logs as slow for two consecutive weeks must have an index added or the query restructured before the next sprint. Do not acknowledge slow queries without acting on them.

---

## Indexes

**Mandatory indexes (add at table creation, not after observing slowness):**
- Every foreign key column — Postgres does not auto-index FK columns, and FK columns appear in virtually every `JOIN` and `WHERE`.
- `workspace_id` on every domain table — the mandatory first filter in all multi-tenant queries.
- `created_at` on tables where list queries sort by time (most domain tables).
- `state` / `status` enum columns on high-traffic tables that filter by state.

**Composite indexes:** Add when a query commonly filters on two columns together:

```python
from sqlalchemy import Index

class Case(IdentityMixin, Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_workspace_state", "workspace_id", "state"),
    )
```

**Speculative indexes to avoid:** Do not index columns that never appear in `WHERE` or `ORDER BY`. Indexes have a write cost on every `INSERT` and `UPDATE`.

Index naming: `ix_{table}_{columns}` — e.g., `ix_cases_workspace_state`.

---

## Gzip compression

Add `GZipMiddleware` in `create_app()`. This compresses JSON responses above 1 KB — typically 70–80% reduction for API payloads:

```python
# my_app/__init__.py — inside create_app()
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)
```

Place this before the CORS middleware in registration order (middleware executes in reverse registration order — gzip fires last on the response, wrapping the body after CORS headers are set).

---

## HTTP response caching headers

Most API endpoints must not be cached by browsers or CDNs because responses are workspace-scoped and user-specific. Apply `Cache-Control: no-store` by default across the `/api/` prefix:

```python
# routers/middleware/no_cache.py
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


class NoCacheMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response
```

**Opt-in caching for immutable resources:** Public files and images served via presigned URL do not go through the API at all — the storage provider (S3, GCS) handles their caching headers. For any API endpoint that genuinely returns immutable public content, set the header explicitly in the route handler, not as middleware.

---

## Idle sleep mode

Applications with bursty usage (e.g., a business app used 9am–6pm, then idle overnight) can enter sleep mode after a configurable idle window. In sleep mode, the task router pauses its drain loop while the HTTP server and the Postgres LISTEN connection remain fully active. The first incoming request or inbound `pg_notify` wakes the app instantly.

**What pauses during sleep:**
- Task router drain loop (`_route_open_tasks`, `_requeue_retry_scheduled_tasks`, `_cleanup_stale_tasks`)
- Recurring and delayed scheduler runners (poll loops skip when sleeping)

**What stays active during sleep:**
- uvicorn / ASGI — the process keeps accepting connections; wake is a side effect of normal request handling
- Postgres LISTEN connection — external DB writes still trigger immediate wake via `pg_notify`

**Three symmetric wake sources:**
| Source | Mechanism |
|---|---|
| HTTP request | `SleepMiddleware` → `ActivityTracker.touch()` |
| Scheduled job fires | scheduler runner → `ActivityTracker.touch()` before task submit |
| External DB write | Postgres `pg_notify` → LISTEN connection → task router unblocks |

### Config

```python
# config.py
sleep_mode_enabled:            bool = Field(default=True,  alias="SLEEP_MODE_ENABLED")
idle_sleep_threshold_seconds:  int  = Field(default=600,   alias="IDLE_SLEEP_THRESHOLD_SECONDS")
```

### `ActivityTracker`

The task router, schedulers, and HTTP server run as **separate processes**. An in-memory singleton is invisible across process boundaries — the HTTP server's `touch()` would never reach the task router or scheduler processes.

`ActivityTracker` stores state in Redis so all processes share it:

```python
# services/infra/sleep/activity_tracker.py
import logging
import time

from my_app.config import settings
from my_app.services.infra.redis import get_redis_client

logger = logging.getLogger(__name__)

_SLEEP_KEY    = "{prefix}:system:sleeping"
_ACTIVITY_KEY = "{prefix}:system:last_activity"
_ACTIVITY_TTL = 86400  # 24h — prevents stale key if app never restarts


def _key(k: str) -> str:
    return k.replace("{prefix}", settings.redis_key_prefix)


class ActivityTracker:

    @classmethod
    def touch(cls) -> None:
        r = get_redis_client(settings.redis_url)
        was_sleeping = r.exists(_key(_SLEEP_KEY))
        r.delete(_key(_SLEEP_KEY))
        r.set(_key(_ACTIVITY_KEY), str(time.time()), ex=_ACTIVITY_TTL)
        if was_sleeping:
            logger.info("app_wake | activity detected")

    @classmethod
    def is_sleeping(cls) -> bool:
        return bool(get_redis_client(settings.redis_url).exists(_key(_SLEEP_KEY)))

    @classmethod
    def enter_sleep(cls) -> None:
        get_redis_client(settings.redis_url).set(_key(_SLEEP_KEY), "1")
        logger.info("app_sleep | entering sleep mode after idle")

    @classmethod
    def idle_seconds(cls) -> float:
        val = get_redis_client(settings.redis_url).get(_key(_ACTIVITY_KEY))
        return 0.0 if val is None else time.time() - float(val)
```

**Why Redis, not in-memory:**
The HTTP server, task router, and both scheduler runners are separate OS processes. In-memory state is private to each process — an HTTP request waking the HTTP server's `ActivityTracker` has no effect on the task router's copy. Redis is the only shared state layer available to all processes without adding inter-process communication.

**Cost:** one Redis GET per `is_sleeping()` check (every 30s in scheduler sleep loop, every poll cycle in task router). At sub-millisecond latency this is negligible.

### `SleepMiddleware`

Touches the tracker on every HTTP request. If the app was sleeping, `touch()` wakes it before the handler runs:

```python
# routers/middleware/sleep.py
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from my_app.services.infra.sleep.activity_tracker import ActivityTracker


class SleepMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        ActivityTracker.touch()
        return await call_next(request)
```

Register in `create_app()` alongside the other middleware (see [02_app_factory.md](02_app_factory.md)).

### Sleep monitor

A background coroutine started in the task router. Checks idle time every 60 seconds and enters sleep when the threshold is exceeded:

```python
# services/infra/execution/task_router.py
from my_app.services.infra.sleep.activity_tracker import ActivityTracker

async def _sleep_monitor() -> None:
    while True:
        await asyncio.sleep(60)
        if not settings.sleep_mode_enabled:
            continue
        if ActivityTracker.idle_seconds() >= settings.idle_sleep_threshold_seconds:
            if not ActivityTracker.is_sleeping():
                ActivityTracker.enter_sleep()
```

Start it as a task alongside the LISTEN connection:

```python
async def run_task_router() -> None:
    asyncio.create_task(_listen_for_task_events())
    asyncio.create_task(_sleep_monitor())
    while True:
        if ActivityTracker.is_sleeping():
            await asyncio.sleep(30)
            continue
        # ... normal drain loop
```

---

### Scheduler sleep — alarm-clock pattern

A naive sleep guard (`if sleeping: sleep 30s; continue`) creates two reliability gaps:
- A job due at 09:00 will not fire until an unrelated event wakes the app — potentially hours later
- Interval schedulers drift because each late fire pushes the next interval forward

A `MIN()` query every 30s during sleep solves precision but is just polling at a different interval — no real DB saving.

**The correct fix:** cache `next_due_at` at the end of each active cycle and sleep directly to that time. Zero extra DB queries during sleep — the scheduler wakes itself from in-memory state:

```python
# services/infra/execution/recurring_scheduler_runner.py
SCHEDULER_SLEEP_CAP_SECONDS = 300  # 5-min cap — bounds delay for jobs added while sleeping


async def run_recurring_scheduler() -> None:
    next_due_at: datetime | None = None

    while True:
        if ActivityTracker.is_sleeping():
            if next_due_at is not None:
                sleep_for = max(0.0, (next_due_at - datetime.now(timezone.utc)).total_seconds())
                sleep_for = min(sleep_for, SCHEDULER_SLEEP_CAP_SECONDS)
            else:
                sleep_for = SCHEDULER_SLEEP_CAP_SECONDS

            await asyncio.sleep(sleep_for)

            if next_due_at is None or datetime.now(timezone.utc) < next_due_at:
                continue  # still before due time — keep sleeping

            ActivityTracker.touch()  # due time arrived — wake the system

        due_jobs = await _get_due_recurring_jobs()
        for job in due_jobs:
            ActivityTracker.touch()
            await _submit_job(job)

        # Cache when the next job will be due — used as sleep target if we enter sleep mode
        next_due_at = await _get_next_run_at()
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


async def _get_next_run_at() -> datetime | None:
    async with task_db_session() as session:
        result = await session.execute(
            select(func.min(RecurringScheduler.next_run_at))
            .where(RecurringScheduler.is_active == True)
        )
        return result.scalar_one_or_none()
```

The same pattern applies to the delayed scheduler — replace `RecurringScheduler.next_run_at` with `DelayedScheduler.run_at` and filter `is_active == True, run_at > now()`.

**DB query cost:**

| Mode | DB queries |
|---|---|
| Active | One poll per `SCHEDULER_POLL_SECONDS` — unchanged |
| Sleeping, no jobs due | Zero — sleeps on cached `next_due_at` |
| Sleeping, job becomes due | Zero extra — wakes from memory, fires job, resumes normal polling |
| Job added externally while sleeping | Fires within `SCHEDULER_SLEEP_CAP_SECONDS` (5 min default) |

**Reliability comparison:**

| Scenario | Flat sleep (naive) | Cached alarm-clock |
|---|---|---|
| Job due, app sleeping, no users | Fires when next unrelated wake arrives — could be hours | Fires on time — wakes from memory |
| Interval drift over time | Accumulates — each late fire shifts the next | < 1 poll interval maximum |
| DB cost during sleep | None | None — cache eliminates the extra query |
| Externally added job while sleeping | Same as above — missed until wake | Fires within 5 min (cap) |

---

**Rules:**
- `IDLE_SLEEP_THRESHOLD_SECONDS` defaults to 600 (10 minutes). Raise it for apps with long natural gaps between requests (e.g., overnight batch users).
- Set `SLEEP_MODE_ENABLED=false` in environments where background jobs must run continuously regardless of HTTP activity (e.g., dedicated worker-only deployments).
- Never skip the LISTEN connection during sleep. A task created by an external system while the app sleeps must still be picked up immediately when the notify fires.
- Scheduler runners must use the cached alarm-clock pattern. Cache `next_due_at` at the end of each active cycle and sleep to that time — never query the DB during sleep, never use a flat interval. A flat sleep makes target-time schedulers unreliable and causes interval drift. A per-sleep `MIN()` query solves precision but is just polling at a longer interval.
- When a scheduler wakes due to a job firing, it calls `ActivityTracker.touch()` before submitting the task. This wakes the task router drain loop before the task enters the queue.
- Three event sources wake the app symmetrically: HTTP requests (`SleepMiddleware`), scheduled jobs (`ActivityTracker.touch()` in the scheduler runner), and external DB writes (Postgres `pg_notify` → LISTEN connection → task router). All three reset `_last_activity`, restarting the idle countdown.
