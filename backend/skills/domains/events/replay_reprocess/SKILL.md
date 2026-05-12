# Events Replay/Reprocess

## Intent

Add or update replay/reprocess flows for event-driven backend behavior.

## Trigger conditions

- User asks for replay, reprocess, recovery, or idempotent event handling.

## Required inputs

- Replay scope (entities, time range, event types)
- Safety constraints and observability requirements

## Contracts to load

- `backend/architecture/11_infra_events.md`: event transport/publishing
- `backend/architecture/16_background_jobs.md`: async execution
- `backend/architecture/52_replayability.md`: replay semantics
- `backend/architecture/49_observability_runtime.md`: runtime diagnostics
- `backend/architecture/53_operational_cli.md`: operator entrypoints

## Optional local extension companions

- `backend/architecture/42_event_local.md`

## Execution protocol

1. Define replay selection criteria and idempotency model.
2. Implement worker/job orchestration for replay tasks.
3. Add correlation IDs and replay observability hooks.
4. Provide operator command/docs and tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Replay is deterministic and recoverable.
- Operator visibility is sufficient for incident use.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
