# Content Add Command

## Intent

Implement a new content-domain write operation using command patterns.

## Trigger conditions

- User requests create/update/publish/archive behavior in content domain.
- Operation includes writes or side effects.

## Required inputs

- Command intent and lifecycle operation
- Payload fields and validation requirements
- Side effect expectations

## Contracts to load

- `backend/architecture/06_commands.md`: command orchestration
- `backend/architecture/04_context.md`: `ServiceContext` requirements
- `backend/architecture/05_errors.md`: typed errors
- `backend/architecture/45_content.md`: content lifecycle baseline
- `backend/architecture/42_event.md`: event linkage when content emits events

## Optional local extension companions

- `backend/architecture/45_content_local.md`
- `backend/architecture/42_event_local.md`

## Execution protocol

1. Define command boundary and lifecycle guards.
2. Implement write orchestration and persistence.
3. Emit events and side effects explicitly.
4. Map failures to typed errors.
5. Add lifecycle tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Content lifecycle invariants are enforced.
- Side effects and failure paths are test-covered.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
