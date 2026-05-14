# 07 — Query Contract (Read Operations)

## Definition

A query is an async function that reads data and returns a serialized representation. It performs zero writes, zero mutations, and zero side effects.

---

## File structure

One query = one file:

```
services/queries/<domain>/
├── list_records.py
├── get_record.py
├── get_record_event_history.py
└── utils/
    ├── pagination.py
    ├── find_records.py       # shared filter builder
    └── serialize_record.py   # shared serializer
```

---

## Query signature

```python
from my_app.services.context import ServiceContext


async def list_records(ctx: ServiceContext) -> dict:
    ...


async def get_record(ctx: ServiceContext) -> dict:
    ...
```

**Rules:**
- Return type is always annotated.
- `ctx: ServiceContext` is always the only parameter. Entity IDs from path parameters are injected into `ctx.incoming_data` or `ctx.query_params` by the router before `run_service` is called.
- Queries must not accept write-intent parameters (e.g., `update=True`). If a caller needs to write and read in one step, that is a command that returns the post-write state.

---

## Core query pattern

All DB reads use the `select()` API with `await ctx.session.execute()`:

```python
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from my_app.models.tables.<domain>.record import Record


async def list_records(ctx: ServiceContext) -> dict:
    stmt = (
        select(Record)
        .where(
            Record.workspace_id == ctx.workspace_id,   # mandatory first filter
            Record.deleted_at.is_(None),
        )
        .options(selectinload(Record.line_items))
        .order_by(Record.created_at.desc())
    )
    result = await ctx.session.execute(stmt)
    records = result.scalars().all()
    return {"records": [serialize_record_compact(r) for r in records]}
```

**Result extraction:**

| Method | Use when |
|---|---|
| `result.scalars().all()` | List of ORM instances |
| `result.scalar_one()` | Exactly one instance — raises if zero or many |
| `result.scalar_one_or_none()` | One instance or `None` — raises if many |
| `result.scalars().first()` | First instance or `None` — no error on many |

---

## Workspace scope enforcement

Every query that returns multi-tenant data must filter by `ctx.workspace_id` as the **first** `where()` condition:

```python
stmt = select(Record).where(
    Record.workspace_id == ctx.workspace_id,   # always first
    ...
)
```

Never return records across workspace boundaries. This is not optional.

Single-entity queries resolve public IDs through the domain resolver:

```python
async def get_record(ctx: ServiceContext) -> dict:
    request = parse_get_record_request(ctx.incoming_data)
    record = await resolve_record(ctx, request.ref)   # applies workspace scope + soft-delete filter
    return {"record": serialize_record_full(record)}
```

See [38_identity_resolution.md](38_identity_resolution.md).

---

## Pagination contract

All list queries that can return more than one page must implement cursor-based pagination using opaque cursors.

**Query param names (standard):**

| Param | Type | Description |
|---|---|---|
| `limit` | int | Max records per page (default: 50, max: 200) |
| `after_cursor` | str | Fetch records after this cursor |
| `before_cursor` | str | Fetch records before this cursor |

**Response shape (standard):**

```python
return {
    "<entity_plural>": serialized_list,
    "<entity_plural>_pagination": {
        "has_more":      True,
        "after_cursor":  "base64_encoded_cursor",
        "before_cursor": None,
        "total":         142,   # optional — only if cheap to compute
    },
}
```

**Implementation pattern:**

```python
MAX_LIMIT     = 200
DEFAULT_LIMIT = 50

async def list_records(ctx: ServiceContext) -> dict:
    limit = min(int(ctx.query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)

    stmt  = build_record_query(ctx)
    result = await ctx.session.execute(stmt.limit(limit + 1))
    rows   = result.scalars().all()

    has_more = len(rows) > limit
    page     = rows[:limit]

    return {
        "records": [serialize_record_compact(r) for r in page],
        "records_pagination": build_opaque_pagination(
            instances=page,
            has_more=has_more,
            date_attr="created_at",
            id_attr="client_id",
        ),
    }
```

The pagination cursor uses `client_id` as the stable tie-breaker alongside `created_at`. Cursors are opaque, but they still must not depend on a hidden integer identifier.

---

## Eager loading

Queries declare required relationships via `selectinload()` or `joinedload()` on the `select()` statement. Lazy loading is disabled (`lazy="raise"` on all relationships — see [03_models.md](03_models.md)):

```python
from sqlalchemy.orm import selectinload, joinedload

stmt = (
    select(Record)
    .where(Record.workspace_id == ctx.workspace_id)
    .options(
        selectinload(Record.line_items),
        joinedload(Record.category),
    )
)
result = await ctx.session.execute(stmt)
records = result.scalars().unique().all()   # .unique() required when using joinedload
```

- Use `selectinload` for one-to-many relationships (avoids Cartesian product rows).
- Use `joinedload` for many-to-one or one-to-one relationships.
- Call `.unique()` on the result when using `joinedload` — SQLAlchemy may return duplicate parent rows from the JOIN.

---

## Filter builders

Complex filter logic lives in a dedicated `find_<entity>.py` file:

```python
# services/queries/<domain>/utils/find_records.py
from sqlalchemy import select
from my_app.models.tables.<domain>.record import Record
from my_app.services.context import ServiceContext


def build_record_query(ctx: ServiceContext, params: dict | None = None):
    params = params or ctx.query_params
    stmt = (
        select(Record)
        .where(
            Record.workspace_id == ctx.workspace_id,
            Record.deleted_at.is_(None),
        )
    )

    if state_id := params.get("state_id"):
        stmt = stmt.where(Record.state_id == int(state_id))

    if after := params.get("after_date"):
        stmt = stmt.where(Record.created_at >= parse_date(after))

    if after_cursor := params.get("after_cursor"):
        date, client_id = decode_cursor(after_cursor)
        stmt = stmt.where(
            (Record.created_at < date) |
            ((Record.created_at == date) & (Record.client_id < client_id))
        )

    return stmt.order_by(Record.created_at.desc(), Record.client_id.desc())
```

This isolates filter logic from pagination logic and keeps `list_records` readable.

---

## Serialization

Serializers are pure functions that convert ORM instances to plain dicts. They live in the domain serializer module:

```python
# domain/<domain>/serializers.py

def serialize_record_compact(r: RecordResult) -> dict:
    return {
        "client_id": r.client_id,
        "name":      r.name,
        "status":    r.status,
        "created_at": r.created_at,
    }
```

See [46_serialization.md](46_serialization.md) for the full serialization contract.

**Rules:**
- Serializers are pure functions — they do not query the database.
- Serializers use `isoformat()` for all datetime fields. Never return a raw `datetime` object.
- Never introduce internal DB `id` fields in public API responses. Use `client_id`.
- Never return a raw ORM instance from a query. Always serialize before returning.

---

## Query result caching

Cache query results that are expensive to compute and stable over a known window. The cache lives in Redis; invalidation is triggered by the command that modifies the data.

```python
# services/infra/cache/query_cache.py
import json
from my_app.services.infra.redis.async_client import get_async_redis

_DEFAULT_TTL = 300   # 5 minutes


async def get_cached(cache_key: str) -> dict | None:
    raw = await get_async_redis().get(cache_key)
    return json.loads(raw) if raw else None


async def set_cached(cache_key: str, data: dict, ttl: int = _DEFAULT_TTL) -> None:
    await get_async_redis().set(cache_key, json.dumps(data), ex=ttl)


async def invalidate(cache_key: str) -> None:
    await get_async_redis().delete(cache_key)


async def invalidate_prefix(pattern: str) -> None:
    redis = get_async_redis()
    keys  = await redis.keys(pattern)
    if keys:
        await redis.delete(*keys)
```

**Usage in a query:**

```python
# services/queries/workspace/get_workspace_settings.py
from my_app.services.infra.cache.query_cache import get_cached, set_cached
from my_app.config import settings


async def get_workspace_settings(ctx: ServiceContext) -> dict:
    cache_key = f"{settings.redis_key_prefix}:cache:workspace_settings:{ctx.workspace_id}"

    cached = await get_cached(cache_key)
    if cached is not None:
        return cached

    # ... DB query ...
    result = {"settings": serialize_workspace_settings(ws)}
    await set_cached(cache_key, result, ttl=300)
    return result
```

**Invalidation in the command that writes:**

```python
# services/commands/workspace/update_workspace_settings.py
from my_app.services.infra.cache.query_cache import invalidate
from my_app.config import settings


async def update_workspace_settings(ctx: ServiceContext) -> dict:
    # ... write to DB ...
    cache_key = f"{settings.redis_key_prefix}:cache:workspace_settings:{ctx.workspace_id}"
    await invalidate(cache_key)
    return result
```

**Rules:**
- Cache keys must include `workspace_id`. Never cache data that crosses workspace boundaries.
- Every cached query must have a matching `invalidate()` call in every command that can change the cached data.
- Cacheable queries: workspace settings, role/permission configs, static lookup lists. Not cacheable: user-specific feeds, notification lists, anything that must be consistent in real time.
- TTL is a safety net, not the primary invalidation mechanism. Always invalidate explicitly on write — do not rely solely on TTL expiry.
- `invalidate_prefix(pattern)` uses Redis `KEYS` — safe only for low-frequency operations (admin actions, not hot paths). For hot paths, use exact key invalidation.
- Never cache across workspace boundaries. The cache key must always contain `workspace_id` as a segment.
