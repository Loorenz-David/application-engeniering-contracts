# 12 — Infrastructure: Redis Contract

## Connection

A single Redis connection helper is shared across the application:

```python
# services/infra/redis/client.py
import redis


def get_redis_client(uri: str, decode_responses: bool = True) -> redis.Redis:
    return redis.from_url(uri, decode_responses=decode_responses)


def assert_redis_available(uri: str, decode_responses: bool = True) -> None:
    client = get_redis_client(uri, decode_responses=decode_responses)
    client.ping()


def describe_redis_uri(uri: str) -> str:
    from urllib.parse import urlparse
    p = urlparse(uri)
    return f"{p.hostname}:{p.port}"
```

**Rules:**
- The Redis URI always comes from `app.config["REDIS_URI"]`. Never hardcode.
- Socket.IO's Redis message queue uses `decode_responses=False`. All other Redis clients use `decode_responses=True`.
- Redis is optional in development. If `REDIS_URI` is not set, the application falls back gracefully (Socket.IO falls back to in-process, queues degrade to in-memory or skip).
- Production always requires Redis. The app logs a warning if Redis is unavailable at startup and returns `503` on health check.

---

## Key naming convention

All Redis keys must follow this pattern:

```
{KEY_PREFIX}:{domain}:{entity_type}:{identifier}
```

Where `KEY_PREFIX` is `app.config["REDIS_KEY_PREFIX"]` (e.g., `"myapp"`).

Examples:

```
myapp:entity:state:42
myapp:notification:pending:workspace_7
myapp:session:socket_token:user_99
myapp:ai:proposal:workspace_7:abc-def
myapp:idempotency:create_record:client-abc
```

**Rules:**
- Never use bare keys without a prefix.
- The prefix is always read from config, never hardcoded.
- Keys are documented in the module that owns them. If a module creates a key pattern, it owns the TTL for that key.

---

## TTL rules

Every key that is not permanent must have an explicit TTL. Never `SET` without `EX` or `PX`:

```python
# Good
redis_client.set(key, value, ex=ttl_seconds)
redis_client.setex(key, ttl_seconds, value)

# Bad — no expiry
redis_client.set(key, value)
```

TTL values come from config, not from inline constants:

```python
# config/default.py
REDIS_ENTITY_STATE_TTL_SECONDS = int(os.environ.get("REDIS_ENTITY_STATE_TTL_SECONDS", "60"))
REDIS_NOTIFICATION_TTL_SECONDS = int(os.environ.get("REDIS_NOTIFICATION_TTL_SECONDS", str(60 * 60 * 48)))
```

---

## Common Redis use cases

### Entity state cache (short-lived, high-write)

For frequently updated entity states that are read by real-time clients or background workers:

```python
ENTITY_STATE_KEY = "{prefix}:entity:state:{entity_client_id}"
TTL = app.config["REDIS_ENTITY_STATE_TTL_SECONDS"]

redis_client.set(key, json.dumps({"status": status, "updated_at": ts}), ex=TTL)
```

### Notification store (medium-lived, read-heavy)

```python
NOTIFICATION_KEY = "{prefix}:notification:pending:{workspace_id}"
TTL = app.config["REDIS_NOTIFICATION_TTL_SECONDS"]
```

### Distributed locks / leases (dispatcher deduplication)

```python
LEASE_KEY = "{prefix}:dispatch:lease:{event_id}"
TTL = app.config["REDIS_DISPATCHER_LEASE_SECONDS"]

acquired = redis_client.set(LEASE_KEY, "1", nx=True, ex=TTL)
if not acquired:
    return  # another worker is handling this event
```

Always use `nx=True` (set only if not exists) for leases.

### Idempotency keys

```python
IDEMPOTENCY_KEY = "{prefix}:idempotency:{operation}:{client_id}"
TTL = app.config["REDIS_IDEMPOTENCY_TTL_SECONDS"]

already_processed = redis_client.get(IDEMPOTENCY_KEY)
if already_processed:
    return cached_result

# ... process ...
redis_client.set(IDEMPOTENCY_KEY, json.dumps(result), ex=TTL)
```

### Token blocklist (logout / revocation)

Revoked JWT tokens are stored in Redis so they can be rejected before expiry:

```python
BLOCKLIST_KEY = "{prefix}:auth:blocklist:{jti}"
TTL = app.config["JWT_ACCESS_TOKEN_EXPIRES"]  # match token lifetime

redis_client.set(BLOCKLIST_KEY, "1", ex=TTL)
```

On every request, the JWT decode hook checks for the blocklist entry and rejects the token if found.

---

## Async Redis client

Background workers and async handlers use `redis.asyncio`:

```python
# services/infra/redis/async_client.py
from redis.asyncio import Redis as AsyncRedis
from my_app.config import settings

_async_client: AsyncRedis | None = None


def get_async_redis() -> AsyncRedis:
    global _async_client
    if _async_client is None:
        _async_client = AsyncRedis.from_url(settings.redis_url, decode_responses=True)
    return _async_client
```

Use `get_async_redis()` in async contexts (socket handlers, background tasks). The synchronous `get_redis_client()` is for startup validation and sync contexts only. Never mix sync and async clients in the same call path.

---

## Memory and eviction policy

Production Redis **must** have a memory limit and eviction policy configured. Without this, Redis fills memory and either crashes or rejects writes — silently dropping task queue items.

**Required configuration (set in `docker-compose.yml` or cloud provider):**

```yaml
# docker-compose.yml
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
```

Or via `redis-cli` on a running instance:

```bash
redis-cli CONFIG SET maxmemory 1gb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG REWRITE   # persist to redis.conf
```

**Policy choice — `allkeys-lru` (required):**

| Policy | Behavior | Use |
|---|---|---|
| `allkeys-lru` | Evict least-recently-used keys across all keys | ✅ Required for this system |
| `volatile-lru` | Evict LRU only among keys with TTL | ❌ Leaves keys without TTL immune — task IDs can accumulate |
| `noeviction` | Reject writes when full | ❌ Causes task queue loss and write errors |

`allkeys-lru` is safe here because all critical durable state lives in PostgreSQL — Redis is transport only. LRU eviction of stale task IDs is acceptable; the task router re-publishes OPEN tasks on the next poll.

**Health check:** Expose Redis memory usage in `/health`. Alert when `used_memory` exceeds 80% of `maxmemory`.

---

## What Redis must NOT store

- Raw SQLAlchemy model instances (use serialized dicts)
- Secrets or credentials
- Data that must be durable across Redis restarts (use the database + outbox for that)
- Business state that is the source of truth (Redis is a cache / pub-sub layer, not a database)
