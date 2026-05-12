# Notifications Add Flow

## Intent

Implement a backend notification flow for a new business event.

## Trigger conditions

- User asks to notify users on domain changes.

## Required inputs

- Trigger event
- Audience rules
- Delivery channel and timing

## Contracts to load

- `backend/architecture/47_notifications.md`: notification model/boundaries
- `backend/architecture/11_infra_events.md`: event trigger wiring
- `backend/architecture/16_background_jobs.md`: async dispatch
- `backend/architecture/49_observability_runtime.md`: tracing and diagnostics

## Optional local extension companions

- `backend/architecture/47_notifications_local.md`

## Execution protocol

1. Define trigger and deduplication semantics.
2. Implement command/event wiring and dispatch path.
3. Add template/payload shaping and delivery handling.
4. Add tests for fanout, retries, and failure modes.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Notification delivery is contract-driven and observable.
- Failure/retry behavior is explicit.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
