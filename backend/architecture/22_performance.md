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
        "id": record.id,
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
stmt = select(Record.id, Record.state_id).where(
    Record.workspace_id == ctx.workspace_id,
    Record.category_id == category_id,
)
rows = db.session.execute(stmt).all()
```

Only use `db.session.query(Record)` (full model load) when the serializer needs multiple columns or relationships.

---

## Bulk writes

When inserting or updating many records, use bulk operations rather than adding one instance at a time:

```python
# Slow — individual INSERT per item
for item_data in items_data:
    db.session.add(Item(**item_data))

# Fast — single INSERT with many values
db.session.bulk_insert_mappings(Item, items_data)
```

For updates across many rows with the same change, use a single `UPDATE` statement:

```python
from sqlalchemy import update

db.session.execute(
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
| Expensive computation result | 5m | `{prefix}:{domain}:result:{entity_id}` |
| AI tool response for idempotency | 30d | `{prefix}:ai:proposal:{workspace_id}:{client_id}` |

**Never cache:** Authentication data, permission checks, financial transactions, or anything that must be consistent with the DB in real time.

---

## Database connection pool

Configure the pool to match your deployment:

```python
# config/production.py
SQLALCHEMY_ENGINE_OPTIONS = {
    "connect_args": {"options": "-c timezone=UTC"},
    "pool_size": 10,          # base connections
    "max_overflow": 20,       # burst connections beyond pool_size
    "pool_timeout": 30,       # seconds to wait for a connection
    "pool_recycle": 1800,     # recycle connections every 30 minutes
    "pool_pre_ping": True,    # validate connection before use
}
```

`pool_pre_ping=True` prevents `OperationalError: server closed the connection unexpectedly` after idle periods. Always enable in production.

Pool size = (number of gunicorn workers) × (threads per worker) + buffer. Overcommitting the pool causes connection wait timeouts under load.

---

## Slow query threshold

Log any query that takes more than `SLOW_QUERY_THRESHOLD_MS` (default: 500ms). Review all slow queries weekly. A slow query that is not addressed within two sprints must have an index added or the query restructured.

See [17_logging.md](17_logging.md) for the logging pattern.

---

## Indexes

Add an index when:
- A column appears in a `WHERE` clause in a frequently called query
- A column is used in an `ORDER BY` on a large table
- A foreign key column does not have a built-in index (Postgres does not auto-index FKs)

Do not add indexes preemptively. Add them when a slow query is observed. Indexes have a write cost — every `INSERT` and `UPDATE` must maintain them.

Index naming: `ix_{table}_{columns}` — e.g., `ix_records_workspace_id_state_id`.
