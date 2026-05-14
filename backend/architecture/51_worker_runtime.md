# 51 - Worker Runtime Contract

## Purpose

Standardize async worker runtime behavior for deterministic retries, replay-safe task execution, and operationally observable failures.

This contract extends:
- 16_background_jobs.md
- 12_infra_redis.md
- 42_event.md
- 49_observability_runtime.md

---

## Runtime Model

Architecture remains modular-monolith-first:
- one backend application runtime
- one or more worker processes
- Redis-backed queue runtime

No distributed task orchestration frameworks are introduced.

---

## Worker Lifecycle

Each worker process should implement explicit lifecycle phases:
1. startup validation
2. queue subscription registration
3. task receive
4. task execute
5. task finalize (success/failure/retry/dead-letter)
6. graceful shutdown

Each phase emits structured logs.

---

## Queue Naming and Registration

Queue names are explicit and deterministic.

Recommended baseline:
- default
- critical
- replay
- dead-letter

Rules:
- No runtime auto-discovery of tasks.
- Task types must be explicitly registered in a registry map.
- Unknown task type is a hard failure.

---

## Retry and Dead-Letter Behavior

Retry policy requirements:
- deterministic max_attempts
- deterministic backoff strategy
- retry metadata attached to execution logs

Dead-letter requirements:
- terminal failures are routed to dead-letter queue/path
- dead-letter transition emits explicit structured event
- dead-letter payload must preserve source metadata for replay

---

## Idempotency and Replay Safety

Worker handlers must be idempotent for duplicate delivery.

Requirements:
- handlers guard against duplicate effects
- terminal state checks before side-effecting actions
- replay execution does not corrupt state when re-applied

---

## Worker Healthchecks

Worker runtime must expose health diagnostics covering:
- Redis connectivity
- queue binding readiness
- runtime identity (worker_id)

Health failure must fail loudly and be visible in logs.

---

## Worker Observability

Required structured event types:
- worker.start
- worker.task.received
- worker.task.started
- worker.task.completed
- worker.task.failed
- worker.task.retry_scheduled
- worker.dead_letter
- worker.shutdown

Logs include:
- correlation_id
- execution_id
- worker_id
- task_type
- retry_attempt
- duration_ms

---

## Operational Commands

Worker runtime should integrate with deterministic operational commands:
- make worker
- make worker-dev
- make worker-logs

CLI commands must not hide queue wiring or task registration behavior.

---

## Graceful Shutdown

Workers must handle `SIGTERM` to avoid leaving tasks stuck in `IN_PROGRESS` until stale recovery fires (90 min). On signal receipt, the worker stops accepting new tasks, rescues any in-flight task to `RETRY_SCHEDULED`, and exits cleanly.

```python
import signal
import asyncio

_shutdown_event: asyncio.Event = asyncio.Event()


def _register_shutdown_handler() -> None:
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown_event.set)


async def run_worker(queue_name: str, handler_map: dict) -> None:
    _register_shutdown_handler()
    current_task_id: str | None = None
    try:
        while not _shutdown_event.is_set():
            raw = redis.blpop(queue_name, timeout=2)   # short timeout so shutdown is responsive
            if not raw:
                continue
            current_task_id = raw[1] if isinstance(raw[1], str) else raw[1].decode()
            await _process_task(current_task_id, worker_id, handler_map)
            current_task_id = None
    finally:
        if current_task_id:
            await _rescue_in_flight_task(current_task_id)
        logger.info("worker.shutdown | queue=%s worker_id=%s", queue_name, worker_id)
```

**Rules:**
- `blpop` timeout must be ≤ 2s. A longer timeout delays shutdown detection and leaves the container in a terminating state longer than necessary.
- `_rescue_in_flight_task` runs in a `finally` block — it executes even on unhandled exceptions, not just clean SIGTERM.
- The rescued task enters `RETRY_SCHEDULED` with a short delay (30s), not `OPEN`. This prevents immediate re-claim by another worker before the dying process has fully released its resources.
- Emit a `worker.shutdown` structured log on every exit so ops teams can correlate rescues with deployments.

---

## Anti-Patterns

- implicit task discovery
- retry loops without max bound
- swallowing worker exceptions without structured logs
- non-deterministic retry timing driven by hidden globals
- missing SIGTERM handler — leaves tasks stuck IN_PROGRESS until stale threshold

---

## Recommended Read Order

1. 16_background_jobs.md
2. 12_infra_redis.md
3. 51_worker_runtime.md
4. 49_observability_runtime.md
5. 52_replayability.md
