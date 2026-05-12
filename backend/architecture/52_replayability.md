# 52 - Replayability Contract

## Purpose

Define replay-safe architecture and operational replay infrastructure for debugging, recovery, and deterministic failure investigation.

This is not full event sourcing.

This contract extends:
- 11_infra_events.md
- 16_background_jobs.md
- 42_event.md
- 49_observability_runtime.md

---

## Scope

Replay targets:
- failed outbox/event dispatches
- failed worker executions
- failed webhook payload processing
- selected async execution flows

Out of scope:
- rebuilding all state from event history
- complete event sourcing architecture

---

## Replay-Safe Command Rules

Commands that can be replayed must:
- be idempotent for duplicate replay_key execution
- separate validation from side effects where practical
- emit deterministic state transitions
- record replay metadata in logs

---

## Replay Metadata Requirements

Replay operation metadata should include:
- replay_id
- source_type (event/job/webhook)
- source_id
- initiated_by
- started_at
- dry_run
- attempt_index

Each replayed unit must emit structured success/failure records with replay_id.

---

## Replay Logging Requirements

Replay logs must include:
- event_type
- correlation_id
- execution_id
- replay_id
- source_id
- duration_ms
- result (success/skipped/failed)

No free-form replay-only logs are allowed.

---

## Event Replay Rules

- Replay events through the same handler interfaces used by normal runtime.
- Apply idempotency guard before side effects.
- Skip already-terminal items when safe and log skip reason.

---

## Webhook Replay Rules

- Persist canonical webhook payload envelope before handling.
- Replay from persisted envelope, not reconstructed payloads.
- Validate envelope integrity before replay execution.

---

## Worker Replay Rules

- Replay through registered task handlers.
- Preserve task metadata needed for deterministic execution.
- Respect retry/dead-letter semantics during replay.

---

## Dry-Run and Safety

Replay commands should support dry-run mode by default for production environments.

Dry-run behavior:
- executes validation and routing logic
- does not perform mutating side effects
- emits full observability logs for what would execute

---

## Auditability

Every replay run should be inspectable by replay_id with:
- input source list
- per-item result
- failure reasons
- completion summary

---

## Anti-Patterns

- manually re-running ad hoc SQL scripts as replay substitute
- replay code paths different from production handlers
- replay without correlation metadata
- replay that mutates state without idempotency controls

---

## Recommended Read Order

1. 11_infra_events.md
2. 16_background_jobs.md
3. 52_replayability.md
4. 49_observability_runtime.md
5. 53_operational_cli.md
