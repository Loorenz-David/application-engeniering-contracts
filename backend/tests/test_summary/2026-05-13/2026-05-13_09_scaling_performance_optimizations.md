# Session 09: Scaling & Performance Optimizations Analysis

**Date:** May 13, 2026  
**Status:** Pre-scale architectural review + executed baseline checks  
**Tests Validated:** 63/63 endpoints passing ✅

---

## Execution Update (2026-05-13)

A runnable scaling baseline was executed in the test backend (`test_09_scaling_baseline.py`).

Initial baseline result:
1. 6 passed, 3 failed
2. Failing checks:
   - DB pool tuning (`DB_POOL_SIZE` too low)
   - Redis eviction policy (`noeviction`)
   - Task router poll interval (`2s`)

Applied local test-backend fixes:
1. Set DB pool env in `.env` (`DB_POOL_SIZE=20`, `DB_MAX_OVERFLOW=20`, `DB_POOL_RECYCLE=1800`)
2. Reduced `POLL_INTERVAL_SECONDS` from `2` to `0.5` in task router
3. Set Redis `maxmemory-policy=allkeys-lru` on active runtime Redis instance

Final baseline result:
1. ✅ 9 passed, 0 failed
2. Result artifact: `/tmp/scaling_performance_test_09_results.json`

**Important runtime note:** a Redis port mismatch (6379 vs 6380 assumptions) caused an initial policy false-negative and was corrected during rerun.

---

## Executive Summary

The bootstrap-generated app is **functionally complete** and **architecturally sound** for single-server deployment. However, before scaling to production traffic, 6 high-priority and 12 medium-priority optimizations should be implemented to prevent performance degradation and operational pain.

**Estimated Implementation Effort:** 80–120 developer-hours  
**Recommended Timeline:** Complete high-priority before going production; medium-priority before 100+ concurrent users

---

## 1. Database Layer Optimizations

### 1.1 Add Pagination to All List Endpoints (HIGH PRIORITY)

**Problem:** All GET list endpoints return unbounded result sets. A `GET /cases` request with 50,000 cases in the DB could return 50,000+ rows, consuming memory and bandwidth.

**Impact at Scale:** 
- Response times grow O(n) with total rows
- Memory spikes on large result sets
- Network bandwidth wasted on unneeded data

**Solution:** Add `limit` and `offset` query parameters with sensible defaults.

**Example Implementation:**

```python
# In routes/api_v1/cases.py
from pydantic import BaseModel, Field

class PaginationParams(BaseModel):
    limit: int = Field(50, ge=1, le=1000, description="Max results per page")
    offset: int = Field(0, ge=0, description="Number of results to skip")

@router.get("/cases")
async def list_cases(
    workspace_id: str,
    pagination: PaginationParams = Depends(),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    """List cases with pagination."""
    query = select(Case).where(Case.workspace_id == workspace_id)
    
    # Get total count before limit/offset
    total = await session.scalar(
        select(func.count(Case.id)).where(Case.workspace_id == workspace_id)
    )
    
    cases = await session.scalars(
        query.limit(pagination.limit).offset(pagination.offset)
    )
    
    return {
        "data": [case.to_dict() for case in cases],
        "pagination": {
            "limit": pagination.limit,
            "offset": pagination.offset,
            "total": total,
        }
    }
```

**Affected Endpoints:** 12 list endpoints across all routers
- `GET /cases`
- `GET /cases/{id}/participants`
- `GET /cases/{id}/conversations`
- `GET /cases/{id}/messages`
- `GET /notifications`
- `GET /images` (if implemented)
- `GET /files` (if implemented)
- etc.

**Deployment:** Add to bootstrap template before generating routers

---

### 1.2 Add Database Indexes (HIGH PRIORITY)

**Problem:** Filtering/sorting on frequently used columns (state, entity_type, user_id, workspace_id) without indexes forces full table scans.

**Impact at Scale:**
- Query time grows O(n) instead of O(log n)
- Disk I/O saturates with 100k+ rows
- PostgreSQL CPU spikes on range queries

**Solution:** Add indexes to all commonly filtered/sorted columns.

**Required Indexes:**

```sql
-- Cases table
CREATE INDEX idx_cases_workspace_id ON cases(workspace_id);
CREATE INDEX idx_cases_state ON cases(state);
CREATE INDEX idx_cases_workspace_state ON cases(workspace_id, state);  -- Composite
CREATE INDEX idx_cases_created_at ON cases(created_at DESC);

-- Participants table
CREATE INDEX idx_case_participants_case_id ON case_participants(case_id);
CREATE INDEX idx_case_participants_user_id ON case_participants(user_id);

-- Conversations table
CREATE INDEX idx_conversations_case_id ON conversations(case_id);
CREATE INDEX idx_conversations_created_at ON conversations(created_at DESC);

-- Messages table
CREATE INDEX idx_conversation_messages_conversation_id ON conversation_messages(conversation_id);
CREATE INDEX idx_conversation_messages_created_at ON conversation_messages(created_at DESC);

-- Notifications table
CREATE INDEX idx_notifications_user_id ON notifications(user_id);
CREATE INDEX idx_notifications_workspace_id ON notifications(workspace_id);
CREATE INDEX idx_notifications_read ON notifications(read);

-- Images/Files tables
CREATE INDEX idx_images_workspace_id ON images(workspace_id);
CREATE INDEX idx_images_entity_type_id ON images(entity_type, entity_id);
CREATE INDEX idx_files_workspace_id ON files(workspace_id);
CREATE INDEX idx_files_case_id ON files(case_id);

-- User app view records
CREATE INDEX idx_user_app_view_records_user_id ON user_app_view_records(user_id);
CREATE INDEX idx_user_app_view_records_entity ON user_app_view_records(entity_type, entity_id);
```

**Deployment:** Add as migration file before bootstrapping. Verify index coverage in slow query log.

---

### 1.3 Implement Query Result Caching (MEDIUM PRIORITY)

**Problem:** Every GET request hits the database fresh. Frequently accessed data (cases, users, notifications) is re-queried 100+ times per minute despite not changing.

**Impact at Scale:**
- Database connection pool exhausted
- Unnecessary CPU cycles on read-heavy workloads
- Latency adds up with 1000+ RPS

**Solution:** Add Redis caching layer with TTL.

**Example Implementation:**

```python
# In services/infra/caching/cache.py
from redis.asyncio import Redis
import json
from typing import Optional, Callable, Any

class CacheService:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.TTL = 30  # Default 30 seconds
    
    async def get(self, key: str) -> Optional[dict]:
        """Get value from cache."""
        value = await self.redis.get(key)
        return json.loads(value) if value else None
    
    async def set(self, key: str, value: Any, ttl: int = None):
        """Set value in cache with TTL."""
        await self.redis.setex(
            key,
            ttl or self.TTL,
            json.dumps(value, default=str)
        )
    
    async def delete(self, key: str):
        """Invalidate cache key."""
        await self.redis.delete(key)
    
    async def delete_pattern(self, pattern: str):
        """Invalidate multiple keys by pattern."""
        keys = await self.redis.keys(pattern)
        if keys:
            await self.redis.delete(*keys)

# Usage in routes
@router.get("/cases/{case_id}")
async def get_case(
    case_id: str,
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache_service),
) -> dict:
    """Get case by ID with caching."""
    cache_key = f"case:{workspace_id}:{case_id}"
    
    # Try cache first
    cached = await cache.get(cache_key)
    if cached:
        return cached
    
    # Cache miss: fetch from DB
    case = await session.get(Case, {"id": case_id, "workspace_id": workspace_id})
    if not case:
        raise HTTPException(404, "Case not found")
    
    result = case.to_dict()
    
    # Cache for 30 seconds
    await cache.set(cache_key, result, ttl=30)
    
    return result

# Invalidate on write operations
@router.put("/cases/{case_id}")
async def update_case(
    case_id: str,
    workspace_id: str,
    body: CaseUpdate,
    session: AsyncSession = Depends(get_session),
    cache: CacheService = Depends(get_cache_service),
) -> dict:
    """Update case and invalidate cache."""
    case = await session.get(Case, {"id": case_id, "workspace_id": workspace_id})
    # ... update logic ...
    await session.commit()
    
    # Invalidate related caches
    await cache.delete(f"case:{workspace_id}:{case_id}")
    await cache.delete_pattern(f"cases:{workspace_id}:*")  # List caches
    
    return case.to_dict()
```

**Cache Strategy by Endpoint:**

| Endpoint | TTL | Invalidation Trigger |
|----------|-----|---------------------|
| `GET /cases/{id}` | 30s | PUT/PATCH/DELETE case |
| `GET /cases` (list) | 20s | Create/update/delete case |
| `GET /users/{id}` | 60s | PUT user profile |
| `GET /notifications` | 10s | Mark as read/unread |
| `GET /images/{id}` | 120s | Update metadata |
| `GET /files/{id}` | 120s | Update metadata |

**Deployment:** Add CacheService to dependency injection. Wrap all read-heavy GET endpoints.

---

### 1.4 Enable Slow Query Logging (MEDIUM PRIORITY)

**Problem:** No visibility into slow queries. N+1 patterns and missing indexes only discovered when users report slowness.

**Impact at Scale:**
- Debug production performance issues blindly
- Miss optimization opportunities
- Users experience unexpected latency

**Solution:** Enable PostgreSQL slow query log.

**Configuration (in Docker compose or cloud PostgreSQL):**

```sql
-- Set in postgresql.conf or via SQL
ALTER SYSTEM SET log_min_duration_statement = 500;  -- Log queries > 500ms
ALTER SYSTEM SET log_statement = 'all';  -- Log all statements
ALTER SYSTEM SET log_duration = on;

-- Reload
SELECT pg_reload_conf();
```

**Monitoring:**

```bash
# In backend container, watch slow queries
docker compose logs -f postgres | grep "duration:"
```

**Deployment:** Enable on dev and staging; consider prod based on volume.

---

### 1.5 Debounce Presence Writes (MEDIUM PRIORITY)

**Problem:** `user_app_view_records` table receives a write on every view/app interaction. A single user session generates 100+ rows/hour.

**Impact at Scale:**
- Table grows uncontrollably (millions of rows in weeks)
- Storage costs spike
- Cleanup queries become slow

**Example at 1000 concurrent users:**
- 1000 users × 100 events/hour = 100,000 rows/hour
- 100,000 rows/hour × 24 hours = 2.4M rows/day
- 2.4M rows/day × 30 days = 72M rows/month 🚨

**Solution:** Batch or debounce presence writes.

**Option A: Debounced Writes (Recommended)**

```python
# In services/tasks/presence/record_view_start.py
from datetime import datetime, timedelta

async def record_view_start(
    user_id: str,
    entity_type: str,
    entity_id: str,
    workspace_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Record view start (debounced)."""
    
    # Check if recent record exists
    recent = await session.scalar(
        select(UserAppViewRecord)
        .where(
            UserAppViewRecord.user_id == user_id,
            UserAppViewRecord.entity_type == entity_type,
            UserAppViewRecord.entity_id == entity_id,
            UserAppViewRecord.action == "view_start",
            UserAppViewRecord.created_at >= datetime.utcnow() - timedelta(seconds=30),
        )
    )
    
    # Only write if no recent record (debounce to 30s intervals)
    if not recent:
        record = UserAppViewRecord(
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            action="view_start",
            workspace_id=workspace_id,
        )
        session.add(record)
        await session.commit()
```

**Option B: Batch Writes**

```python
# In infra/execution/task_router.py
# Instead of writing presence immediately, batch every 30 seconds

from redis.asyncio import Redis

async def batch_presence_updates(redis: Redis, session: AsyncSession):
    """Batch and write pending presence records every 30 seconds."""
    pending = await redis.lrange("presence:pending", 0, -1)
    
    for item in pending:
        data = json.loads(item)
        record = UserAppViewRecord(**data)
        session.add(record)
    
    await session.commit()
    await redis.delete("presence:pending")
```

**Expected Reduction:** 100,000 rows/hour → 3,200 rows/hour (97% reduction)

**Deployment:** Add debounce logic to presence handlers. Document retention policy (e.g., delete records > 90 days old).

---

## 2. Async/Task Layer Optimizations

### 2.1 Tune Connection Pool (HIGH PRIORITY)

**Problem:** Default asyncpg connection pool is small (10 connections). Under load, pool exhaustion causes request queuing.

**Impact at Scale:**
- Task queue blocked waiting for connections
- Requests timeout
- Database rejects new connections

**Solution:** Increase pool size based on expected concurrency.

**Configuration:**

```python
# In app/core/database.py or config.py
import os
from sqlalchemy.ext.asyncio import create_async_engine

# Formula: pool_size = (num_workers + 1) + max_overflow
# For 50 concurrent users: pool_size=20, max_overflow=10

SQLALCHEMY_ECHO = os.getenv("SQLALCHEMY_ECHO", "false").lower() == "true"

engine = create_async_engine(
    DATABASE_URL,
    echo=SQLALCHEMY_ECHO,
    pool_size=int(os.getenv("DB_POOL_SIZE", "20")),
    max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
    pool_recycle=3600,  # Recycle connections every hour
    pool_pre_ping=True,  # Verify connection health
)
```

**Environment Variables:**

```bash
# .env
DB_POOL_SIZE=20           # Base connections
DB_MAX_OVERFLOW=10        # Additional overflow connections
DB_POOL_RECYCLE=3600      # Recycle stale connections
```

**Testing:**

```python
# Verify pool settings
from sqlalchemy import inspect
pool = engine.pool
print(f"Pool size: {pool.size()}")
print(f"Max overflow: {pool.max_overflow}")
```

**Deployment:** Tune based on monitoring and load testing. Default 20/10 suits 50–100 concurrent users.

---

### 2.2 Set Redis Eviction Policy (HIGH PRIORITY)

**Problem:** Task queue items stored in Redis. If Redis memory fills, items are silently dropped, causing tasks to disappear.

**Impact at Scale:**
- Unpredictable task loss
- Silent failures (no errors, tasks just vanish)
- Hard to debug

**Solution:** Configure Redis memory policy to `allkeys-lru`.

**Configuration:**

```bash
# In docker-compose.yml Redis service
redis:
  image: redis:7-alpine
  command: redis-server --maxmemory 1gb --maxmemory-policy allkeys-lru
  environment:
    - REDIS_ARGS=--maxmemory 1gb --maxmemory-policy allkeys-lru
```

**Or in running Redis:**

```bash
# Via redis-cli
redis-cli CONFIG SET maxmemory 1gb
redis-cli CONFIG SET maxmemory-policy allkeys-lru
redis-cli CONFIG REWRITE  # Persist
```

**Policy Options:**

| Policy | Behavior |
|--------|----------|
| `volatile-lru` | Evict least-recent keys (only with TTL) |
| `allkeys-lru` | Evict least-recent keys (all keys) ✅ Recommended |
| `volatile-ttl` | Evict keys closest to expiry |
| `noeviction` | Reject writes when full ❌ Causes task loss |

**Monitoring:**

```python
# In monitoring service
redis = Redis.from_url(REDIS_URL)
info = await redis.info()
memory_used = info["used_memory_human"]
memory_limit = info["maxmemory_human"] or "unlimited"
print(f"Redis: {memory_used} / {memory_limit}")
```

**Deployment:** Set immediately before going production.

---

### 2.3 Add Task Timeout Enforcement (MEDIUM PRIORITY)

**Problem:** Long-running tasks (e.g., heavy case processing, file uploads) have no timeout. If a worker crashes mid-task, the task hangs forever.

**Impact at Scale:**
- Workers hang indefinitely
- Task queue backs up
- Manual intervention needed

**Solution:** Add timeout to all task executions.

**Implementation:**

```python
# In domain/execution/task.py
from dataclasses import dataclass
from datetime import timedelta

@dataclass
class ExecutionTask:
    id: str
    type: str
    workspace_id: str
    payload: dict
    state: str  # OPEN, PENDING, COMPLETED, FAILED, RETRY_SCHEDULED
    max_duration_seconds: int = 300  # Default 5 min timeout
    created_at: datetime = None
    started_at: datetime = None
    completed_at: datetime = None

# In workers/base_worker.py
import asyncio

async def execute_task(task: ExecutionTask) -> dict:
    """Execute task with timeout."""
    try:
        result = await asyncio.wait_for(
            self._run_task(task),
            timeout=task.max_duration_seconds
        )
        return result
    except asyncio.TimeoutError:
        # Mark as failed, don't retry
        await mark_task_failed(
            task.id,
            error="Task exceeded max_duration_seconds"
        )
        raise
```

**Per-Task Timeouts:**

```python
# Define different timeouts for different task types
TASK_TIMEOUTS = {
    "send_notification": 30,          # 30 seconds
    "process_case_message": 300,      # 5 minutes
    "process_file_upload": 3600,      # 1 hour
    "generate_report": 600,           # 10 minutes
}

task = ExecutionTask(
    type="process_case_message",
    max_duration_seconds=TASK_TIMEOUTS.get(type, 300)
)
```

**Deployment:** Add max_duration_seconds to ExecutionTask schema. Set sensible defaults per task type.

---

### 2.4 Optimize Task Router Polling (MEDIUM PRIORITY)

**Problem:** Task router polls the database every 2 seconds (`POLL_INTERVAL_SECONDS=2`). Under burst loads, this misses tasks created between polls.

**Impact at Scale:**
- Task latency unpredictable (0–2 seconds + execution time)
- Bursts of 100+ tasks created within 2s window may pile up

**Solution:** Lower poll interval or implement event-driven dispatch.

**Option A: Lower Poll Interval (Quick Fix)**

```python
# In services/infra/execution/task_router.py
POLL_INTERVAL_SECONDS = 0.5  # Poll every 500ms instead of 2s

async def poll_and_dispatch():
    """Poll DB for open tasks and dispatch to queue."""
    while True:
        try:
            open_tasks = await get_open_tasks(session)
            for task in open_tasks:
                await enqueue_task(task, redis)
                await mark_task_pending(task.id, session)
            
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            logger.error(f"Poll error: {e}")
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
```

**Option B: Event-Driven Dispatch (Better Long-Term)**

```python
# Listen on PostgreSQL NOTIFY channel
import asyncpg

async def listen_for_tasks(dsn: str):
    """Listen for task creation events via PostgreSQL NOTIFY."""
    conn = await asyncpg.connect(dsn)
    
    async with conn.transaction():
        await conn.add_listener("task_created", on_task_created)
        
        while True:
            await asyncio.sleep(3600)  # Keep connection alive

async def on_task_created(conn, pid, channel, payload):
    """Dispatch task immediately when created."""
    task_id = payload
    task = await get_task(task_id, session)
    await enqueue_task(task, redis)
```

**In Task Creation:**

```python
# In services/tasks/base_task.py
async def create_task(task_type: str, workspace_id: str, payload: dict):
    """Create task and notify router."""
    task = ExecutionTask(type=task_type, workspace_id=workspace_id, payload=payload)
    session.add(task)
    await session.flush()  # Get ID before commit
    
    # Notify router immediately (PostgreSQL NOTIFY)
    await session.execute(
        text(f"NOTIFY task_created, '{task.id}'")
    )
    await session.commit()
```

**Expected Improvement:** Task dispatch latency from 0–2s → 0–500ms (median)

**Deployment:** Start with Option A (poll every 500ms). Move to Option B if latency is critical.

---

### 2.5 Add Retry Backoff Jitter (MEDIUM PRIORITY)

**Problem:** Retries use fixed delays (30s, 120s, 300s). If 100 tasks fail simultaneously, they all retry at the same moment, causing a thundering herd.

**Impact at Scale:**
- Synchronized retry storms overload database/workers
- Temporary failures become cascading failures

**Solution:** Add exponential backoff with random jitter.

**Implementation:**

```python
# In domain/execution/task.py
import random
import math

class RetryPolicy:
    base_delay = 30      # 30 seconds
    max_delay = 3600     # 1 hour
    backoff_factor = 2   # exponential
    jitter = True
    
    @staticmethod
    def get_retry_delay(attempt: int) -> int:
        """Calculate delay with exponential backoff + jitter."""
        delay = min(
            RetryPolicy.base_delay * (RetryPolicy.backoff_factor ** attempt),
            RetryPolicy.max_delay
        )
        
        # Add ±10% jitter
        if RetryPolicy.jitter:
            jitter_amount = delay * 0.1
            delay = delay + random.uniform(-jitter_amount, jitter_amount)
        
        return max(int(delay), RetryPolicy.base_delay)

# Usage
# Attempt 0: 30s ± 3s
# Attempt 1: 60s ± 6s
# Attempt 2: 120s ± 12s
# Attempt 3: 240s ± 24s
# Attempt 4: 480s ± 48s (capped at 3600s)
```

**In Task Retry Logic:**

```python
async def retry_task(task: ExecutionTask, attempt: int):
    """Schedule task retry with backoff."""
    delay_seconds = RetryPolicy.get_retry_delay(attempt)
    
    task.scheduled_retry_at = datetime.utcnow() + timedelta(seconds=delay_seconds)
    task.retry_attempt = attempt + 1
    task.state = "RETRY_SCHEDULED"
    
    session.add(task)
    await session.commit()
```

**Deployment:** Update task retry handlers. Test with simulated failure scenarios.

---

## 3. WebSocket/Real-Time Layer Optimizations

### 3.1 Add WebSocket Connection Limits (MEDIUM PRIORITY)

**Problem:** No limits on concurrent WebSocket connections. A buggy client could spawn 10,000 connections, exhausting server memory.

**Impact at Scale:**
- Memory exhaustion with rogue clients
- Legitimate users disconnected due to resource limits
- No graceful degradation

**Solution:** Implement per-server and per-user connection limits.

**Configuration:**

```python
# In config.py or app initialization
WEBSOCKET_CONFIG = {
    "max_connections_per_server": 5000,    # Total connections this process
    "max_connections_per_user": 10,        # Max per authenticated user
    "max_rooms": 1000,                     # Max conversation rooms
    "connection_timeout_seconds": 3600,    # Idle timeout
}

# In socketio/connection_manager.py
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list] = {}  # user_id -> connections
        self.connection_count = 0
    
    async def connect(self, sid: str, user_id: str):
        """Add connection with limits."""
        if self.connection_count >= WEBSOCKET_CONFIG["max_connections_per_server"]:
            raise Exception("Server connection limit reached")
        
        user_connections = self.active_connections.get(user_id, [])
        if len(user_connections) >= WEBSOCKET_CONFIG["max_connections_per_user"]:
            # Force-disconnect oldest connection
            await self.disconnect(user_connections[0])
        
        self.active_connections.setdefault(user_id, []).append(sid)
        self.connection_count += 1
    
    async def disconnect(self, sid: str):
        """Remove connection."""
        # Find and remove
        for user_id, conns in self.active_connections.items():
            if sid in conns:
                conns.remove(sid)
                self.connection_count -= 1
                break
```

**Monitoring:**

```python
@router.get("/health/websocket")
async def websocket_health(manager: ConnectionManager = Depends()):
    """Monitor WebSocket connections."""
    return {
        "total_connections": manager.connection_count,
        "max_connections": WEBSOCKET_CONFIG["max_connections_per_server"],
        "usage_percent": (manager.connection_count / WEBSOCKET_CONFIG["max_connections_per_server"]) * 100,
    }
```

**Deployment:** Add limits before going production. Monitor usage patterns.

---

### 3.2 Add Auto-Disconnect on Idle (MEDIUM PRIORITY)

**Problem:** Long-lived WebSocket connections (browser tab open, but user away) consume memory indefinitely.

**Impact at Scale:**
- Memory grows with number of idle connections
- Zombie connections prevent legitimate users from connecting

**Solution:** Implement ping/pong keepalive with idle timeout.

**Implementation:**

```python
# In socketio event handlers
@sio.event
async def connect(sid, environ):
    """Establish connection."""
    user_id = get_user_from_token(environ)
    await manager.connect(sid, user_id)
    
    # Start idle timeout timer
    await set_idle_timeout(sid, WEBSOCKET_CONFIG["connection_timeout_seconds"])

@sio.event
async def disconnect(sid):
    """Handle disconnect."""
    await manager.disconnect(sid)
    await cancel_idle_timeout(sid)

@sio.on('pong')
async def on_pong(sid):
    """Client responds to ping; reset idle timer."""
    await reset_idle_timeout(sid, WEBSOCKET_CONFIG["connection_timeout_seconds"])

# In background task
async def monitor_idle_connections():
    """Disconnect idle connections."""
    while True:
        now = datetime.utcnow()
        for sid, last_activity in list(connection_timers.items()):
            if (now - last_activity).total_seconds() > WEBSOCKET_CONFIG["connection_timeout_seconds"]:
                await sio.disconnect(sid, skip_sid=True)
                del connection_timers[sid]
        
        await asyncio.sleep(60)  # Check every minute
```

**Client-Side (Frontend):**

```javascript
// Listen for server ping, respond with pong
socket.on('ping', () => {
    socket.emit('pong');
});

// Also send periodic pings
setInterval(() => {
    socket.emit('pong');
}, 30000);  // Every 30 seconds
```

**Deployment:** Implement both server-side timeout and client-side pong handlers.

---

## 4. Storage Layer Optimizations

### 4.1 Batch S3 Operations (MEDIUM PRIORITY)

**Problem:** Presigned URL generation is per-request. Uploading 100 files requires 100 separate S3 API calls.

**Impact at Scale:**
- S3 API rate limits hit quickly
- Latency adds up (100 requests × 100ms = 10s)

**Solution:** Batch presigned URL generation.

**Implementation:**

```python
# In services/s3/presigned_url.py
from typing import List

class S3Service:
    async def generate_presigned_urls(
        self,
        file_specs: List[dict],
        expires_in: int = 3600
    ) -> List[dict]:
        """Batch presigned URL generation."""
        # Instead of looping and calling S3 for each file,
        # construct URLs locally (presigned URL is just a signed request)
        
        urls = []
        for spec in file_specs:
            url = self.s3_client.generate_presigned_url(
                'put_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': spec['key'],
                    'ContentType': spec['content_type'],
                },
                ExpiresIn=expires_in,
            )
            urls.append({
                'key': spec['key'],
                'upload_url': url,
                'expires_at': datetime.utcnow() + timedelta(seconds=expires_in),
            })
        
        return urls

# Usage in route
@router.post("/files/presigned-urls")
async def generate_batch_presigned_urls(
    file_specs: List[FileSpec],
    s3: S3Service = Depends(get_s3_service),
) -> dict:
    """Generate multiple presigned URLs in one request."""
    urls = await s3.generate_presigned_urls(file_specs)
    return {"urls": urls}
```

**Expected Improvement:** 100 files from 100 requests → 1 request (100x reduction)

**Deployment:** Add batch endpoint to file routes. Update frontend to batch uploads.

---

### 4.2 Add Multipart Upload for Large Files (MEDIUM PRIORITY)

**Problem:** Large file uploads (> 100MB) fail or timeout with single PUT request.

**Impact at Scale:**
- Users can't upload large files
- Network interruptions lose entire upload

**Solution:** Implement S3 multipart upload.

**Implementation:**

```python
# In services/s3/multipart.py
class MultipartUploadService:
    async def create_upload(
        self,
        key: str,
        content_type: str,
        workspace_id: str
    ) -> dict:
        """Initiate multipart upload."""
        response = self.s3_client.create_multipart_upload(
            Bucket=self.bucket_name,
            Key=key,
            ContentType=content_type,
        )
        return {
            'upload_id': response['UploadId'],
            'key': key,
        }
    
    async def generate_part_presigned_url(
        self,
        key: str,
        upload_id: str,
        part_number: int,
    ) -> str:
        """Generate presigned URL for a single part."""
        return self.s3_client.generate_presigned_url(
            'upload_part',
            Params={
                'Bucket': self.bucket_name,
                'Key': key,
                'UploadId': upload_id,
                'PartNumber': part_number,
            },
            ExpiresIn=3600,
        )
    
    async def complete_upload(
        self,
        key: str,
        upload_id: str,
        parts: List[dict],  # [{'PartNumber': 1, 'ETag': '...'}, ...]
    ) -> dict:
        """Complete multipart upload."""
        response = self.s3_client.complete_multipart_upload(
            Bucket=self.bucket_name,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts},
        )
        return {'key': key, 'etag': response['ETag']}

# Routes
@router.post("/files/multipart/init")
async def init_multipart_upload(
    key: str,
    content_type: str,
    s3: MultipartUploadService = Depends(get_s3_service),
) -> dict:
    """Start multipart upload."""
    return await s3.create_upload(key, content_type)

@router.get("/files/multipart/{upload_id}/part-url")
async def get_part_presigned_url(
    key: str,
    upload_id: str,
    part_number: int,
    s3: MultipartUploadService = Depends(get_s3_service),
) -> dict:
    """Get presigned URL for uploading a part."""
    url = await s3.generate_part_presigned_url(key, upload_id, part_number)
    return {'upload_url': url, 'part_number': part_number}

@router.post("/files/multipart/{upload_id}/complete")
async def complete_multipart_upload(
    key: str,
    upload_id: str,
    parts: List[dict],
    s3: MultipartUploadService = Depends(get_s3_service),
) -> dict:
    """Complete upload after all parts uploaded."""
    return await s3.complete_upload(key, upload_id, parts)
```

**Client Usage (Frontend):**

```javascript
// Upload large file in 5MB chunks
const FILE_SIZE = 5 * 1024 * 1024;  // 5MB chunks
const totalParts = Math.ceil(file.size / FILE_SIZE);
const uploadId = await initMultipartUpload(key, file.type);

for (let i = 0; i < totalParts; i++) {
    const start = i * FILE_SIZE;
    const end = Math.min(start + FILE_SIZE, file.size);
    const part = file.slice(start, end);
    
    const url = await getPartPresignedUrl(uploadId, i + 1);
    const response = await fetch(url, {method: 'PUT', body: part});
    parts.push({PartNumber: i + 1, ETag: response.headers.get('etag')});
}

await completeMultipartUpload(uploadId, parts);
```

**Deployment:** Add multipart routes and S3 service methods. Update frontend to use for files > 50MB.

---

## 5. Caching & Performance Headers

### 5.1 Add Response Caching Headers (MEDIUM PRIORITY)

**Problem:** Every GET response is marked as non-cacheable. Browsers and CDNs re-fetch identical content repeatedly.

**Impact at Scale:**
- Bandwidth wasted on duplicate content
- User perceived latency increases
- Unnecessary server load

**Solution:** Add Cache-Control headers to GET responses.

**Implementation:**

```python
# In app/middleware/caching.py
from fastapi.responses import Response
from functools import wraps
from datetime import timedelta

def cache_response(max_age_seconds: int = 300):
    """Decorator to add caching headers."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            response = await func(*args, **kwargs)
            
            if isinstance(response, Response):
                response.headers["Cache-Control"] = f"public, max-age={max_age_seconds}"
                response.headers["ETag"] = generate_etag(response.body)
            
            return response
        return wrapper
    return decorator

# Usage
@router.get("/cases/{case_id}")
@cache_response(max_age_seconds=30)
async def get_case(case_id: str):
    """Get case (cached for 30s)."""
    # ...

@router.get("/images/{image_id}")
@cache_response(max_age_seconds=3600)
async def get_image(image_id: str):
    """Get image (cached for 1 hour)."""
    # ...

# Or add globally
@app.middleware("http")
async def add_cache_headers(request: Request, call_next):
    """Add caching headers based on path."""
    response = await call_next(request)
    
    if request.method == "GET" and response.status_code == 200:
        if "/images/" in request.url.path:
            response.headers["Cache-Control"] = "public, max-age=3600"
        elif "/cases/" in request.url.path and "list" not in request.url.path:
            response.headers["Cache-Control"] = "public, max-age=30"
        elif "/notifications/" in request.url.path:
            response.headers["Cache-Control"] = "private, max-age=10"
    
    return response
```

**Cache Strategy:**

| Endpoint | Max-Age | Type | Notes |
|----------|---------|------|-------|
| `GET /cases/{id}` | 30s | public | Case details change infrequently |
| `GET /cases` (list) | 10s | private | User-specific results, short TTL |
| `GET /images/{id}` | 3600s | public | Images immutable once uploaded |
| `GET /files/{id}` | 3600s | public | Files immutable |
| `GET /notifications` | 5s | private | Real-time, must be fresh |

**Deployment:** Add to middleware or route decorators. Test with browser dev tools.

---

### 5.2 Enable Gzip Compression (MEDIUM PRIORITY)

**Problem:** HTTP responses (JSON, HTML) uncompressed. Large responses (100KB+) sent as-is over network.

**Impact at Scale:**
- Bandwidth costs spike
- User perceived latency increases
- Network saturation

**Solution:** Enable gzip compression middleware.

**Implementation:**

```python
# In app/main.py
from fastapi.middleware.gzip import GZipMiddleware

app.add_middleware(GZipMiddleware, minimum_size=1000)  # Compress responses > 1KB
```

**Expected Compression Ratio:**
- JSON: 70–80% reduction
- HTML: 80–90% reduction
- Images: No reduction (already compressed)

**Deployment:** Add GZipMiddleware in app initialization. Clients handle transparently.

---

## 6. Authentication Optimizations

### 6.1 Cache JWT Claims (MEDIUM PRIORITY)

**Problem:** Permission middleware decodes and validates JWT on every request. Heavy crypto operations repeated 1000× per second at scale.

**Impact at Scale:**
- CPU spikes on crypto operations
- Token validation becomes bottleneck

**Solution:** Cache decoded JWT claims locally.

**Implementation:**

```python
# In services/auth/token_cache.py
from redis.asyncio import Redis
import json

class JWTClaimCache:
    def __init__(self, redis: Redis, ttl_seconds: int = 300):
        self.redis = redis
        self.ttl = ttl_seconds
    
    async def get(self, token: str) -> Optional[dict]:
        """Get cached claims."""
        key = f"jwt:{hash(token)}"
        cached = await self.redis.get(key)
        return json.loads(cached) if cached else None
    
    async def set(self, token: str, claims: dict):
        """Cache claims."""
        key = f"jwt:{hash(token)}"
        await self.redis.setex(
            key,
            self.ttl,
            json.dumps(claims, default=str)
        )

# Usage in middleware
@app.middleware("http")
async def cache_jwt_claims(request: Request, call_next):
    """Cache JWT claims to avoid repeated decoding."""
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    
    if token:
        cache = JWTClaimCache(redis, ttl_seconds=300)
        cached_claims = await cache.get(token)
        
        if cached_claims:
            request.state.claims = cached_claims
            request.state.from_cache = True
        else:
            claims = decode_jwt(token)  # Expensive
            await cache.set(token, claims)
            request.state.claims = claims
            request.state.from_cache = False
    
    return await call_next(request)
```

**Expected Improvement:** Token validation from 5ms (full decode) → 0.5ms (cache hit)

**Deployment:** Add JWT claim cache to auth middleware. Invalidate on logout.

---

## 7. Implementation Priority Matrix

| Priority | Component | Effort | Impact | Depends On |
|----------|-----------|--------|--------|-----------|
| **HIGH** | Pagination | 2–4h | Critical (unbounded queries) | None |
| **HIGH** | DB Indexes | 1–2h | Critical (query speed) | None |
| **HIGH** | Redis Eviction | 0.5–1h | Critical (prevent data loss) | None |
| **HIGH** | Connection Pool | 1–2h | High (prevent exhaustion) | None |
| **MEDIUM** | Query Caching | 6–8h | High (reduce DB load) | Pagination |
| **MEDIUM** | Slow Query Logging | 1–2h | Medium (observability) | None |
| **MEDIUM** | Presence Debouncing | 2–3h | Medium (storage growth) | None |
| **MEDIUM** | Task Timeout | 2–3h | Medium (prevent hangs) | None |
| **MEDIUM** | Task Router Polling | 2–4h | Medium (reduce latency) | None |
| **MEDIUM** | Retry Jitter | 1–2h | Medium (prevent thundering herd) | None |
| **MEDIUM** | WebSocket Limits | 2–3h | Medium (prevent abuse) | None |
| **MEDIUM** | Idle Disconnect | 1–2h | Low (save memory) | WebSocket Limits |
| **MEDIUM** | S3 Batching | 3–4h | Medium (reduce API calls) | None |
| **MEDIUM** | Multipart Upload | 4–6h | Medium (large file support) | S3 Batching |
| **MEDIUM** | Cache Headers | 1–2h | Medium (reduce bandwidth) | None |
| **MEDIUM** | Gzip Compression | 0.5–1h | High (reduce bandwidth) | None |
| **LOW** | JWT Claim Cache | 2–3h | Low (crypto optimization) | None |

---

## 8. Testing & Validation

### Load Testing Plan

Before deploying to production, validate optimizations with load testing:

```bash
# Install load test tools
pip install locust pytest-asyncio

# Create test_load.py
from locust import HttpUser, task, between

class CaseUser(HttpUser):
    wait_time = between(1, 3)
    
    @task(3)
    def list_cases(self):
        self.client.get("/cases?limit=50&offset=0")
    
    @task(1)
    def get_case(self):
        self.client.get("/cases/123")
    
    @task(1)
    def update_case(self):
        self.client.put("/cases/123", json={"state": "in_progress"})

# Run test
# locust -f test_load.py --host=http://localhost:8000 --users=100 --spawn-rate=5 --run-time=5m

```

**Benchmarks to Track:**

- Response time p50, p95, p99 (target: < 100ms p95)
- Requests per second (target: 1000+ RPS on 4-core machine)
- Error rate (target: < 0.1%)
- Database connection pool usage (target: < 80%)
- Redis memory usage (target: < 80% of configured max)

---

## 9. Monitoring Checklist

Deploy with these monitoring in place:

- [ ] Slow query logging enabled (log_min_duration_statement = 500ms)
- [ ] Database connection pool metrics (current/max connections)
- [ ] Redis memory usage and eviction rate
- [ ] WebSocket connection count
- [ ] Task queue depth (pending tasks)
- [ ] Task execution latency (time from creation to completion)
- [ ] HTTP request latency (p50, p95, p99)
- [ ] Error rate by endpoint
- [ ] Cache hit rate (for query cache)

---

## 10. Recommendations Summary

**Before Initial Deploy:**
1. ✅ Add pagination to all list endpoints
2. ✅ Add database indexes on foreign keys and filter columns
3. ✅ Set Redis maxmemory-policy to allkeys-lru
4. ✅ Tune asyncpg connection pool (pool_size=20, max_overflow=10)
5. ✅ Enable slow query logging
6. ✅ Add gzip compression middleware

**Before 100 Concurrent Users:**
7. Add query result caching with Redis
8. Debounce presence writes (user_app_view_records)
9. Implement WebSocket connection limits
10. Add task timeout enforcement
11. Implement S3 multipart upload for large files
12. Add response caching headers (Cache-Control)

**As You Scale:**
13. Monitor and optimize based on observability data
14. Implement request timeout strategy
15. Add JWT claim caching if token validation becomes bottleneck
16. Consider CDN for static assets and images
17. Implement audit log batching if audit logging becomes enabled

**Optional (Lower Priority):**
18. Implement event-driven task dispatch (PostgreSQL NOTIFY)
19. Add circuit breaker pattern for external services
20. Implement request throttling/rate limiting

---

## Files to Modify/Create

| File | Purpose | Priority |
|------|---------|----------|
| `config.py` | Add tunable parameters | HIGH |
| `models/*.py` | Add pagination fields | HIGH |
| `routers/api_v1/*.py` | Add pagination, caching | HIGH |
| `migrations/` | Add database indexes | HIGH |
| `docker-compose.yml` | Redis eviction policy | HIGH |
| `services/infra/database.py` | Connection pool tuning | HIGH |
| `services/infra/caching/` | New caching layer | MEDIUM |
| `middleware/caching.py` | Response cache headers | MEDIUM |
| `middleware/gzip.py` | Compression | MEDIUM |
| `services/tasks/presence/` | Debounce presence | MEDIUM |
| `socketio/connection_manager.py` | Connection limits | MEDIUM |
| `workers/base_worker.py` | Task timeouts | MEDIUM |
| `services/s3/multipart.py` | Large file uploads | MEDIUM |

---

## Estimated Total Effort

- **High Priority:** 6–12 hours
- **Medium Priority:** 30–40 hours
- **Testing & Validation:** 10–15 hours
- **Total:** 46–67 developer-hours

**Can be completed incrementally:**
- Phase 1 (before deploy): 8–12h
- Phase 2 (before 100 users): 20–25h
- Phase 3 (ongoing optimization): 10–15h+

---

**End of Session 09 Analysis**

This document captures all proactive performance optimizations that should be included in the bootstrap-generated code. Claude can use this as a specification for improving the application template.

