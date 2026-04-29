# 16 — Background Jobs Contract

## What background jobs are

A background job is any work that should not block the HTTP response: sending notifications, triggering webhooks, computing analytics snapshots, calling slow external APIs. Jobs run in separate worker processes and are queued via Redis (RQ).

---

## Worker processes

The application runs multiple worker types. Each has a dedicated entry point:

| Worker | Entry point | Purpose |
|---|---|---|
| Default | `redis_worker_default.py` | General-purpose async tasks |
| IO | `redis_worker_io.py` | External HTTP calls (email, SMS, webhooks) |
| Dispatcher | `redis_dispatcher.py` | Domain event dispatch from the outbox |
| Scheduler | `redis_scheduler.py` | Cron-like recurring jobs |

Workers are started by `Procfile.worker`, `Procfile.dispatcher`, `Procfile.scheduler`.

---

## Queue names and selection rule

```python
# services/infra/jobs/queues.py
QUEUE_DEFAULT = "default"
QUEUE_IO = "io"
QUEUE_REALTIME = "realtime"
```

| Queue | Use when |
|---|---|
| `default` | CPU-bound or DB-bound work (analytics, data aggregation) |
| `io` | External HTTP calls with unpredictable latency (email, SMS, webhooks) |
| `realtime` | Push notifications and Socket.IO events — time-sensitive |

Never put external HTTP calls on the `default` queue — they block workers that should be processing fast jobs.

---

## Retry policies

```python
# services/infra/jobs/retries.py
from dataclasses import dataclass
from rq import Retry

@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int
    intervals: tuple[int, ...]  # seconds between retries

    def to_rq_retry(self) -> Retry:
        return Retry(max=self.max_attempts, interval=list(self.intervals))


REALTIME_RETRY_POLICY    = RetryPolicy(max_attempts=2,  intervals=(5,))
MESSAGING_RETRY_POLICY   = RetryPolicy(max_attempts=5,  intervals=(30, 120, 300, 900))
DEFAULT_RETRY_POLICY     = RetryPolicy(max_attempts=3,  intervals=(10, 60))
```

Selection guide:

| Policy | Use when |
|---|---|
| `REALTIME_RETRY_POLICY` | Socket.IO push — fast retry, give up quickly |
| `MESSAGING_RETRY_POLICY` | Email / SMS — aggressive retry, external provider may be slow |
| `DEFAULT_RETRY_POLICY` | Everything else |

---

## Enqueuing jobs

Always use `enqueue_job` from the infra layer. Never call `queue.enqueue()` directly:

```python
# services/infra/jobs/enqueue.py
from typing import Any, Callable
from rq.job import Job
from .queues import get_named_queue
from .retries import DEFAULT_RETRY_POLICY, RetryPolicy


def enqueue_job(
    *,
    queue_key: str,
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    job_id: str | None = None,
    retry_policy: RetryPolicy | None = None,
    description: str | None = None,
    result_ttl: int = 300,
    failure_ttl: int = 7 * 24 * 3600,
) -> Job:
    queue = get_named_queue(queue_key)
    policy = retry_policy or DEFAULT_RETRY_POLICY
    return queue.enqueue_call(
        func=fn,
        args=args,
        kwargs=kwargs or {},
        job_id=job_id,
        retry=policy.to_rq_retry(),
        description=description,
        result_ttl=result_ttl,
        failure_ttl=failure_ttl,
    )
```

Calling from an event handler:

```python
from my_app.services.infra.jobs.enqueue import enqueue_job
from my_app.services.infra.jobs.queues import QUEUE_IO
from my_app.services.infra.jobs.retries import MESSAGING_RETRY_POLICY


def handle_record_created_send_notification(event: dict) -> None:
    enqueue_job(
        queue_key=QUEUE_IO,
        fn=send_record_created_notification,
        kwargs={"record_id": event["payload"]["record_id"], "workspace_id": event["workspace_id"]},
        job_id=f"notification-email-{event['payload']['record_id']}",
        retry_policy=MESSAGING_RETRY_POLICY,
        description="Send record creation notification",
    )
```

---

## Job function contract

```python
# services/infra/jobs/tasks/notifications.py
import logging

logger = logging.getLogger(__name__)


def send_record_created_notification(record_id: int, workspace_id: int) -> None:
    """
    Job functions receive only primitive types.
    They reconstruct context from those primitives.
    """
    from my_app import create_app
    from my_app.models import db

    app = create_app("production")
    with app.app_context():
        record = db.session.get(Record, record_id)
        if record is None:
            logger.warning("Record %s not found — skipping notification.", record_id)
            return
        # ... send notification
```

**Rules:**
- Job functions receive **only primitive types** (`int`, `str`, `bool`, `dict`, `list`). Never pass ORM instances — they are not serializable by RQ.
- Job functions must reconstruct the app context themselves via `create_app()`. They run in a separate process.
- Job functions must be **idempotent**. RQ retries on failure. Sending a notification twice must be prevented via Redis idempotency key or DB flag.
- Job functions must not raise bare `Exception`. Catch known failure modes, log them, and let unexpected exceptions propagate so RQ retries them.
- Job function names describe what they do: `send_record_created_notification`, not `process_event`.

---

## Idempotency

All job functions that cause external side effects must guard against duplicate execution:

```python
def send_record_created_notification(record_id: int, workspace_id: int) -> None:
    from my_app.services.infra.redis import get_redis_client
    from flask import current_app

    redis = get_redis_client(current_app.config["REDIS_URI"])
    idempotency_key = f"{current_app.config['REDIS_KEY_PREFIX']}:notification:sent:{record_id}"

    if redis.get(idempotency_key):
        logger.info("Notification already sent for record %s — skipping.", record_id)
        return

    # ... send notification ...

    redis.set(idempotency_key, "1", ex=60 * 60 * 24 * 7)  # 7 days
```

---

## Job ID convention

Use deterministic job IDs to prevent duplicate enqueuing:

```
{domain}-{operation}-{entity_id}
```

Examples:
```
notification-email-123
webhook-outbound-456
analytics-snapshot-workspace-7
```

RQ silently drops a job if a job with the same ID is already queued. This is the primary deduplication mechanism.

---

## Scheduled jobs

Recurring jobs (daily analytics snapshot, outbox repair, stale data cleanup) use `schedule_job`:

```python
from my_app.services.infra.jobs.enqueue import schedule_job
from my_app.services.infra.jobs.queues import QUEUE_DEFAULT
from datetime import datetime, timezone


def schedule_daily_analytics(workspace_id: int) -> None:
    schedule_job(
        queue_key=QUEUE_DEFAULT,
        fn=compute_daily_analytics_snapshot,
        scheduled_time=datetime.now(timezone.utc),
        kwargs={"workspace_id": workspace_id},
        job_id=f"analytics-daily-{workspace_id}",
        interval=86400,  # repeat every 24 hours
    )
```

Scheduled jobs must also be idempotent — the scheduler may enqueue the same job twice if the worker restarts.

---

## Failure handling

Jobs that exhaust all retries are moved to the RQ `FailedJobRegistry`. They are retained for `failure_ttl` seconds (default: 7 days).

Alert on failed jobs in production via monitoring. A failed messaging job means a notification was not delivered. A failed analytics job means reports are stale.

Do not silently swallow failures. Let them fail loudly so they appear in the failed job registry.
