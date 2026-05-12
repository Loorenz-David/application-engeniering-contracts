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

## Anti-Patterns

- implicit task discovery
- retry loops without max bound
- swallowing worker exceptions without structured logs
- non-deterministic retry timing driven by hidden globals

---

## Recommended Read Order

1. 16_background_jobs.md
2. 12_infra_redis.md
3. 51_worker_runtime.md
4. 49_observability_runtime.md
5. 52_replayability.md
