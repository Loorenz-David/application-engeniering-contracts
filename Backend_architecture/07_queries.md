# 07 — Query Contract (Read Operations)

## Definition

A query is a function that reads data and returns a serialized representation. It performs zero writes, zero mutations, and zero side effects.

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


def list_records(ctx: ServiceContext) -> dict:
    ...


def get_record(ctx: ServiceContext) -> dict:
    ...
```

**Rules:**
- Return type is always annotated.
- `ctx: ServiceContext` is always the only parameter for all queries. Entity IDs from URL path parameters are injected into `ctx.incoming_data` or `ctx.query_params` by the router before `run_service` is called. The query reads them from `ctx` via a request parser or direct key access.
- Queries must not accept write-intent parameters (e.g., `update=True`). If a caller needs to write and read in one step, that is a command that returns the post-write state.

---

## Workspace scope enforcement

Every query that returns multi-tenant data must filter by `ctx.workspace_id`:

```python
def list_records(ctx: ServiceContext) -> dict:
    query = db.session.query(Record).filter(Record.workspace_id == ctx.workspace_id)
    ...
```

Never return records across workspace boundaries. This is not optional.

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
        "has_more": True,
        "after_cursor": "base64_encoded_cursor",
        "before_cursor": None,
        "total": 142,       # optional — only if cheap to compute
    },
}
```

**Implementation pattern:**

```python
MAX_LIMIT = 200
DEFAULT_LIMIT = 50

def list_records(ctx: ServiceContext) -> dict:
    limit = min(int(ctx.query_params.get("limit", DEFAULT_LIMIT)), MAX_LIMIT)

    query = build_record_query(ctx)
    results = query.limit(limit + 1).all()
    has_more = len(results) > limit
    page = results[:limit]

    return {
        "records": serialize_records(page, ctx),
        "records_pagination": build_opaque_pagination(
            instances=page,
            has_more=has_more,
            date_attr="created_at",
            id_attr="id",
        ),
    }
```

---

## Serialization

Serializers are functions that convert ORM instances to plain dicts. They live in `queries/<domain>/utils/serialize_<entity>.py`:

```python
# services/queries/<domain>/utils/serialize_record.py
from my_app.models.tables.<domain>.record import Record


def serialize_record(instance: Record) -> dict:
    return {
        "id": instance.id,
        "client_id": instance.client_id,
        "name": instance.name,
        "status": instance.status,
        "created_at": instance.created_at.isoformat(),
        # ...
    }


def serialize_records(instances: list[Record], ctx) -> list[dict]:
    return [serialize_record(r) for r in instances]
```

**Rules:**
- Serializers are pure functions. They do not query the database.
- Serializers use `isoformat()` for all datetime fields. Never return a raw `datetime` object.
- Serializers never expose internal database IDs without also exposing `client_id`. Prefer `client_id` as the primary identifier in API responses.
- Never return a raw ORM instance from a query. Always serialize before returning.

---

## Eager loading

Queries declare their required relationships via `selectinload` or `joinedload` at the query level, not via relationship `lazy` settings:

```python
from sqlalchemy.orm import selectinload

query = db.session.query(Record).options(
    selectinload(Record.line_items),
    selectinload(Record.attachments),
).filter(Record.workspace_id == ctx.workspace_id)
```

This makes the N+1 problem visible and intentional. Never rely on lazy loading in a list query.

---

## Filter builders

Complex filter logic (sorting, date ranges, search, multi-param filtering) lives in a dedicated `find_<entity>.py` file:

```python
# services/queries/<domain>/utils/find_records.py

def find_records(
    params: dict,
    ctx: ServiceContext,
    query=None,
) -> Query:
    if query is None:
        query = db.session.query(Record).filter(Record.workspace_id == ctx.workspace_id)

    if state_id := params.get("state_id"):
        query = query.filter(Record.state_id == int(state_id))

    if after := params.get("after_date"):
        query = query.filter(Record.created_at >= parse_date(after))

    # cursor pagination
    if after_cursor := params.get("after_cursor"):
        date, id_ = decode_cursor(after_cursor)
        query = query.filter(
            (Record.created_at < date) |
            ((Record.created_at == date) & (Record.id < id_))
        )

    return query.order_by(Record.created_at.desc(), Record.id.desc())
```

This isolates filter logic from pagination logic and keeps `list_records` readable.
