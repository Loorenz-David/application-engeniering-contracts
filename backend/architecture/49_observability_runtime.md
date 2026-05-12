# 49 - Observability Runtime Contract

## Purpose

Standardize runtime observability behavior across all generated backends so failures are diagnosable, replay paths are traceable, and logs are machine-readable for operators and AI agents.

This contract extends:
- 17_logging.md
- 31_health_observability.md
- 48_presence.md
- 16_background_jobs.md

It does not introduce distributed tracing systems, service mesh, or hidden runtime instrumentation.

---

## Scope

Applies to:
- HTTP request/response path
- startup and shutdown lifecycle
- worker runtime execution path
- health and readiness diagnostics
- replay and operational command paths

Out of scope:
- vendor-specific log pipelines
- managed APM lock-in
- distributed system tracing infrastructure

---

## Required Log Model

All runtime logs must be structured JSON and include deterministic core fields.

Required fields:
- timestamp (UTC ISO 8601)
- level (INFO/WARN/ERROR)
- service
- event_type
- correlation_id
- request_id (HTTP path only)
- execution_id (worker/replay path)
- worker_id (worker path)
- duration_ms (when applicable)
- message

Optional fields:
- method
- path
- status_code
- error
- db_health
- redis_health
- entity_type
- entity_client_id
- retry_attempt
- replay_id

---

## Correlation Propagation Rules

1. Incoming HTTP requests:
- Accept X-Correlation-ID when provided.
- Generate one when missing.
- Bind generated correlation_id to request context.
- Return it in response headers.

2. Internal async execution:
- Preserve correlation_id from enqueueing command into task payload metadata.
- Bind execution_id when worker starts task handling.
- Bind worker_id from worker process identity.

3. Replay flows:
- Replay operation generates replay_id.
- Replay binds correlation_id and execution_id for each replay unit.
- Replay logs include replay_id and source identifier.

4. Context reset:
- Runtime context must be cleared after request/task completion to prevent leakage.

---

## Middleware Expectations

Required middleware behavior:
- Request context middleware binds correlation_id/request_id.
- Request timing middleware logs duration_ms for every request.
- Error middleware logs structured exception event before re-raising/handling.

Prohibited behavior:
- print()-based logs
- free-form text-only logs
- context stored in mutable globals without request/task scoping

---

## Worker Observability Expectations

Each task lifecycle should emit at minimum:
- worker.task.received
- worker.task.started
- worker.task.completed or worker.task.failed

Worker logs must include:
- task_type
- correlation_id
- execution_id
- worker_id
- retry_attempt (if retrying)
- duration_ms

Dead-letter transitions must emit explicit worker.dead_letter events.

---

## Startup, Shutdown, and Health Logging

Startup must log:
- runtime.startup
- loaded environment profile
- critical dependency validation result

Shutdown must log:
- runtime.shutdown
- reason when known

Health/readiness endpoints must log:
- runtime.health
- db_health
- redis_health
- degraded causes when present

---

## Determinism and AI-Debugging Requirements

1. Log schema is stable across environments.
2. Event naming uses deterministic lowercase dotted forms.
3. Context IDs are always present in runtime logs.
4. Failure paths emit complete structured metadata.
5. Replay operations can be reconstructed from logs without inspecting source code.

---

## Good vs Bad Logging Examples

Good structured log:

{
  "timestamp": "2026-05-11T12:45:01.233Z",
  "level": "INFO",
  "service": "orders_api",
  "event_type": "http.request.completed",
  "correlation_id": "corr_a94de0f356314ad5",
  "request_id": "req_afe3431decb24a96",
  "execution_id": null,
  "worker_id": null,
  "duration_ms": 42,
  "method": "POST",
  "path": "/api/v1/orders",
  "status_code": 201,
  "message": "order created"
}

Bad log examples:

- "created order in 42ms"
- "error happened"
- "DB down? maybe"

Why bad:
- not machine-readable
- no correlation context
- no deterministic schema
- cannot support replay tracing

---

## Implementation Notes

Recommended read order for runtime observability implementation:
1. 17_logging.md
2. 31_health_observability.md
3. 49_observability_runtime.md
4. 51_worker_runtime.md
5. 52_replayability.md

When implementing a new runtime capability, add observability before feature-complete status.
