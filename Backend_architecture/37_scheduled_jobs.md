# 37 — Scheduled Jobs Contract

## The distinction from background jobs

[16_background_jobs.md](16_background_jobs.md) covers **triggered** jobs — jobs enqueued by a command or event in response to something that just happened (e.g., send a notification after a record is created).

This contract covers **scheduled** jobs — jobs that run on a time-based schedule regardless of application events (e.g., generate a daily report every morning, clean up expired records every hour).

Both use the same RQ worker infrastructure. The difference is in how they are enqueued: triggered jobs are enqueued by commands; scheduled jobs are enqueued by a scheduler.

---

## Scheduler approach

Use `rq-scheduler` or a dedicated cron process to enqueue jobs on a schedule. Do not use `crontab` entries that call `curl` against your own API — that creates an unauthenticated surface and bypasses the service layer.

```python
# scheduler.py — runs as a separate process alongside the worker

from datetime import timedelta
from redis import Redis
from rq_scheduler import Scheduler

from my_app import create_app
from my_app.jobs.scheduled import (
    cleanup_expired_uploads_job,
    generate_daily_workspace_report_job,
    replay_failed_events_job,
    gdpr_deferred_erasure_check_job,
)

redis_conn = Redis.from_url(REDIS_URI)
scheduler = Scheduler(connection=redis_conn)


def register_schedules():
    # Clear existing schedules on restart to avoid duplicates
    for job in scheduler.get_jobs():
        scheduler.cancel(job)

    # Run every hour
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc),
        func=cleanup_expired_uploads_job,
        interval=3600,
        repeat=None,   # None = run forever
        id="scheduled:cleanup-expired-uploads",
    )

    # Run every day at 06:00 UTC
    scheduler.cron(
        "0 6 * * *",
        func=generate_daily_workspace_report_job,
        id="scheduled:daily-workspace-report",
    )

    # Run every 15 minutes — check for stalled erasure requests
    scheduler.schedule(
        scheduled_time=datetime.now(timezone.utc),
        func=gdpr_deferred_erasure_check_job,
        interval=900,
        id="scheduled:gdpr-erasure-check",
    )
```

**Rules:**
- Every scheduled job has a deterministic `id`. This prevents duplicate registrations on scheduler restart.
- `register_schedules()` always cancels existing jobs before re-registering. Running `register_schedules()` twice must be idempotent.
- Schedules are defined in one place: `scheduler.py`. Never scatter `scheduler.schedule()` calls across the codebase.

---

## Scheduled job file structure

```
my_app/
└── jobs/
    ├── triggered/           # enqueued by commands and event handlers
    │   └── <domain>/
    └── scheduled/          # enqueued by the scheduler
        ├── __init__.py      # exports all scheduled job functions
        ├── cleanup.py       # cleanup_expired_uploads_job, etc.
        ├── reports.py       # generate_daily_workspace_report_job, etc.
        └── privacy.py       # gdpr_deferred_erasure_check_job, etc.
```

---

## Scheduled job function contract

A scheduled job is a module-level function with no parameters. It creates its own application context, iterates over all relevant workspaces or records, and delegates to existing service commands or queries:

```python
# jobs/scheduled/cleanup.py
import logging
from datetime import datetime, timezone, timedelta

from my_app import create_app
from my_app.models import db
from my_app.models.tables.files.pending_upload import PendingUpload
from my_app.services.infra.storage import get_storage_client

logger = logging.getLogger(__name__)


def cleanup_expired_uploads_job() -> None:
    """
    Delete PendingUpload rows that have been pending for more than 1 hour.
    Safe to run multiple times — idempotent.
    """
    app = create_app("production")
    with app.app_context():
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        expired = (
            db.session.query(PendingUpload)
            .filter(PendingUpload.status == "pending", PendingUpload.expires_at < cutoff)
            .all()
        )

        client = get_storage_client()
        deleted = 0
        for upload in expired:
            try:
                client.delete_object(upload.storage_key)
                upload.status = "expired"
                deleted += 1
            except Exception:
                logger.exception("Failed to delete storage object | key=%s", upload.storage_key)

        db.session.commit()
        logger.info("cleanup_expired_uploads | deleted=%d", deleted)
```

**Rules:**
- Scheduled jobs take no parameters. All configuration comes from the application config.
- Scheduled jobs create their own `app_context`. They do not assume one exists.
- Scheduled jobs are idempotent — running them twice in a row must produce the same result as running them once.
- Scheduled jobs process records in batches, not all at once. See the batching pattern below.
- Scheduled jobs log a summary line at the end: `job_name | processed=N errors=M duration_ms=X`.
- Scheduled jobs must not call other scheduled jobs. They call service commands, queries, or infra functions.

---

## Batching large datasets

A scheduled job that processes all records in a workspace or all users in the system must not load them all into memory at once. Use cursor-based batching:

```python
def generate_daily_workspace_report_job() -> None:
    app = create_app("production")
    with app.app_context():
        last_id = 0
        batch_size = 100
        processed = 0
        errors = 0
        start = datetime.now(timezone.utc)

        while True:
            workspaces = (
                db.session.query(Workspace)
                .filter(Workspace.id > last_id, Workspace.is_deleted == False)
                .order_by(Workspace.id)
                .limit(batch_size)
                .all()
            )
            if not workspaces:
                break

            for workspace in workspaces:
                try:
                    _generate_report_for_workspace(workspace.id)
                    processed += 1
                except Exception:
                    logger.exception(
                        "Report generation failed | workspace_id=%s", workspace.id
                    )
                    errors += 1

            last_id = workspaces[-1].id
            db.session.expunge_all()   # release ORM objects between batches

        duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
        logger.info(
            "generate_daily_workspace_report | processed=%d errors=%d duration_ms=%d",
            processed, errors, duration_ms,
        )
```

Use `db.session.expunge_all()` between batches to prevent the SQLAlchemy identity map from accumulating every row loaded during the job.

---

## Idempotency for scheduled jobs

A scheduled job can be triggered twice in the same window (scheduler restart, network retry, clock drift). The job must produce the same result regardless.

Patterns for idempotency:

**Status column check:** Only process records in a specific status. Once processed, the status changes and the record is skipped on re-run.

```python
records = (
    db.session.query(Record)
    .filter(Record.report_status == "pending", Record.created_at < yesterday)
    .limit(batch_size)
    .all()
)
for record in records:
    _process_record(record)
    record.report_status = "reported"   # prevents re-processing on retry
```

**Date boundary check:** Use a date field to determine what "today's work" is. The same job running twice on the same day selects the same records but finds them already processed.

**Upsert instead of insert:** For jobs that generate aggregate rows (daily reports), use `INSERT ... ON CONFLICT DO UPDATE` so duplicate runs update rather than fail.

---

## Scheduled job catalog

Every scheduled job must be documented in `docs/scheduled_jobs.md`:

```markdown
## cleanup_expired_uploads_job

**Schedule:** Every hour
**Purpose:** Delete PendingUpload rows that have been pending for over 1 hour and remove the corresponding objects from storage.
**Idempotency:** Yes — processes only `status=pending` rows with `expires_at < now - 1 hour`.
**Failure mode:** Individual object deletions are caught and logged. The job continues. Failures are counted and logged in the summary line.
**Alert if:** Error count > 10 in a single run.
```

---

## Handling job failures

Scheduled jobs should not raise uncaught exceptions — a crash exits the job mid-batch and leaves partial state. Catch exceptions per entity within the batch loop, log them, increment an error counter, and continue:

```python
for entity in batch:
    try:
        _process_entity(entity)
    except Exception:
        logger.exception("Scheduled job entity failed | entity_id=%s", entity.id)
        errors += 1
```

If the error rate exceeds a threshold, log at `ERROR` level with a summary. The `31_health_observability.md` alerting rules should trigger on elevated error log rates from scheduled jobs.

Do not silently swallow exceptions. Every failure must appear in the log.

---

## Scheduled job checklist

Before shipping a new scheduled job:

- [ ] Job function is registered in `scheduler.py` with a deterministic `id`
- [ ] `register_schedules()` is idempotent (cancel-then-register)
- [ ] Job creates its own `app_context`
- [ ] Job processes records in batches, not all at once
- [ ] Job is idempotent — safe to run twice in the same window
- [ ] Job logs a summary line at completion (`processed=N errors=M duration_ms=X`)
- [ ] Job is documented in `docs/scheduled_jobs.md`
- [ ] Alert threshold defined for error rate
