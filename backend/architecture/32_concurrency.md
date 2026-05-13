# 32 — Concurrency & Idempotency Contract

## Why this matters

A single-threaded system with one writer never has concurrency problems. Every production application has multiple workers, retried HTTP requests, and background jobs that can fire twice. This contract defines the patterns that prevent silent data corruption under concurrent load.

Read this contract whenever you are writing a command that:
- Updates a shared counter or balance
- Changes a state machine field
- Is called from a background job that can be retried
- Responds to a webhook that can be delivered more than once

---

## The two locking strategies

### Pessimistic locking — `SELECT FOR UPDATE`

Use when: two concurrent writers would both try to modify the same row and the last-write-wins outcome is wrong.

```python
# services/commands/<domain>/increment_record_counter.py
from my_app.services.identity.records import resolve_record


def increment_record_counter(ctx: ServiceContext) -> dict:
    request = parse_increment_request(ctx.incoming_data)

    with db.session.begin():
        # resolve_record with for_update=True acquires a row-level lock.
        # workspace enforcement and soft-delete filtering are handled by the resolver — see 38_identity_resolution.md
        record = resolve_record(ctx, request.ref, for_update=True)
        record.counter += 1

    return {"counter": record.counter}
```

**When `SELECT FOR UPDATE` is appropriate:**
- Decrementing a stock count (prevent oversell)
- Claiming an item from a shared queue
- Applying a credit or debit to a balance

**When `SELECT FOR UPDATE` is NOT appropriate:**
- Simple reads followed by unrelated writes — it holds the lock unnecessarily
- Reporting queries — use `READ COMMITTED` isolation instead
- Any operation where a retry is acceptable and the window is short

**Lock escalation risk:** Never hold a `FOR UPDATE` lock while calling an external API. The external call may time out and leave the lock held for seconds, blocking all other writers on that row.

```python
# WRONG — external call inside locked transaction
with db.session.begin():
    record = db.session.query(Record).filter(...).with_for_update().first()
    send_sms(record.phone)   # may take 2+ seconds — lock held the whole time

# CORRECT — do the external call outside the transaction
with db.session.begin():
    record = db.session.query(Record).filter(...).with_for_update().first()
    record.status = "notified"
    phone = record.phone    # capture what you need

send_sms(phone)             # lock released before this runs
```

---

### Optimistic locking — version column

Use when: concurrent edits to the same resource happen rarely but must be detected when they do. Optimistic locking avoids a held lock — it detects the conflict at write time.

```python
# models/tables/<domain>/record.py
class Record(db.Model):
    __tablename__ = "records"
    ...
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
```

The command reads the current version, increments it, and updates only if the version matches:

```python
# services/commands/<domain>/update_record.py

def update_record(ctx: ServiceContext) -> dict:
    request = parse_update_record_request(ctx.incoming_data)

    with db.session.begin():
        rows_updated = (
            db.session.query(Record)
            .filter(
                Record.client_id == request.client_id,  # public identifier from the request
                Record.workspace_id == ctx.workspace_id,
                Record.is_deleted == False,
                Record.version == request.version,   # must match what the client read
            )
            .update(
                {"name": request.name, "version": Record.version + 1},
                synchronize_session=False,
            )
        )

        if rows_updated == 0:
            raise ConflictError("Record was modified by another user. Reload and try again.")

    return get_record(ctx)
```

The client must send the `version` field it received when it fetched the record. If another writer committed between the client's read and write, `rows_updated` will be 0 and the command raises `ConflictError` (HTTP 409).

**When optimistic locking is appropriate:**
- UI-driven edits where the user reads a form, fills it out, and submits
- Any resource where concurrent edits are the exception, not the rule

**When pessimistic locking is preferred:**
- High-contention rows (shared counters, queues, balances)
- Operations where the retry cost is high

---

## Idempotency keys for commands

A command is idempotent if calling it twice with the same inputs produces the same result and the same side effects as calling it once. Write commands must be idempotent when they can be called by:
- A client that retries on network timeout
- A background job that can be enqueued twice
- A webhook that can be delivered more than once

### Pattern 1 — natural idempotency via unique constraint

The simplest idempotency: if the data model has a natural unique key, a duplicate insert raises a DB constraint error, which the command converts to a non-fatal conflict response.

```python
# If workspace + external_ref is declared UNIQUE in the model:
try:
    with db.session.begin():
        record = Record(workspace_id=ctx.workspace_id, external_ref=request.external_ref, ...)
        db.session.add(record)
except IntegrityError:
    # Duplicate — return the existing record instead of raising
    existing = (
        db.session.query(Record)
        .filter(Record.workspace_id == ctx.workspace_id, Record.external_ref == request.external_ref)
        .first()
    )
    return serialize_record(existing)
```

### Pattern 2 — explicit idempotency key

For commands where there is no natural unique key, the client sends an `idempotency_key` (a UUID generated client-side before the request):

```python
# models/tables/idempotency_keys.py
class IdempotencyKey(IdentityMixin, db.Model):
    CLIENT_ID_PREFIX = "idk"
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    workspace_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspaces.client_id"), nullable=False)
    command: Mapped[str] = mapped_column(String(128), nullable=False)
    response: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

```python
# services/commands/<domain>/create_record.py

def create_record(ctx: ServiceContext) -> dict:
    request = parse_create_record_request(ctx.incoming_data)

    # Check for a previous response for this idempotency key
    if request.idempotency_key:
        cached = (
            db.session.query(IdempotencyKey)
            .filter(
                IdempotencyKey.key == request.idempotency_key,
                IdempotencyKey.workspace_id == ctx.workspace_id,
            )
            .first()
        )
        if cached:
            return cached.response   # return the original response — do not re-execute

    with db.session.begin():
        record = Record(workspace_id=ctx.workspace_id, ...)
        db.session.add(record)

        if request.idempotency_key:
            result = serialize_record(record)
            db.session.add(IdempotencyKey(
                key=request.idempotency_key,
                workspace_id=ctx.workspace_id,
                command="create_record",
                response=result,
                created_at=datetime.now(timezone.utc),
            ))

    return result
```

**Idempotency key TTL:** Idempotency keys can be pruned after 24 hours. A retry arriving 24 hours late is a programming error, not a normal retry.

For batch commands, resolve and lock all target entities before applying modifications. Use the batch identity resolver with `for_update=True` when concurrent writes can conflict, and use `WorkContext` to track all touched entities and events. See [38_identity_resolution.md](38_identity_resolution.md) and [39_work_context.md](39_work_context.md).

---

## Background job deduplication

Background jobs can be enqueued twice if a command emits an event and the process crashes between the event publish and the acknowledgment. Use a deterministic job ID to prevent duplicate execution:

```python
# services/commands/<domain>/create_record.py — after the commit

from rq import Queue

q = Queue("default", connection=redis_conn)
q.enqueue(
    handle_record_created_send_notification,
    record_id=record.client_id,
    job_id=f"notify-record-created-{record.client_id}",  # deterministic — safe to enqueue twice
    retry=Retry(max=3, interval=[10, 30, 60]),
)
```

If the same `job_id` is enqueued twice, RQ silently ignores the second enqueue. This is correct behavior — the job will run exactly once.

**Deterministic job ID naming:**
```
<action>-<domain>-<entity-client-id>  # notify-record-created-rec_01...
<action>-<domain>-<workspace>-<date>  # analytics-snapshot-workspace-ws_01...-2025-01-15
```

---

## Race condition patterns to avoid

### Double-check locking (broken in Python)

```python
# WRONG — check then act is not atomic; another request can interleave
record = db.session.query(Record).filter(...).first()
if record.status == "draft":        # another worker reads here too
    record.status = "active"        # both workers write — only one should win
    db.session.commit()

# CORRECT — conditional update with a WHERE clause
rows = (
    db.session.query(Record)
    .filter(Record.client_id == record_id, Record.status == "draft")
    .update({"status": "active"}, synchronize_session=False)
)
if rows == 0:
    raise ConflictError("Record is no longer in draft state.")
```

### Aggregate recalculation without locking

```python
# WRONG — read sum, then write; sum is stale by the time you write
total = db.session.query(func.sum(LineItem.amount)).filter(...).scalar()
record.total = total    # another insert may have happened since the SELECT

# CORRECT — compute in a single atomic UPDATE
db.session.execute(
    text("""
        UPDATE records
        SET total = (SELECT COALESCE(SUM(amount), 0) FROM line_items WHERE record_id = :rid)
        WHERE client_id = :rid AND workspace_id = :wid
    """),
    {"rid": record_id, "wid": ctx.workspace_id},
)
```

---

## Concurrency checklist for commands

Before marking a write command complete, verify:

- [ ] If two identical requests arrive simultaneously, does only one succeed?
- [ ] If the command is retried after a network timeout, does it produce duplicate records or side effects?
- [ ] If a background job runs twice, does it send two notifications or charge twice?
- [ ] If the command reads a value and then writes based on it, is the read-write atomic?
- [ ] Are external calls (HTTP, SMS, email) outside the locked transaction?
- [ ] Does the command use a deterministic job ID for any background jobs it enqueues?
