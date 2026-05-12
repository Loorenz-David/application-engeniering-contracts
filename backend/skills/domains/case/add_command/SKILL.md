# Case Add Command

## Intent

Implement a new case-domain write operation using command patterns.

## Trigger conditions

- User requests create/update/transition behavior in case domain.
- Operation includes writes, transitions, or side effects.

## Required inputs

- Command name and case workflow intent
- Input payload and transition constraints
- Expected side effects/events

## Contracts to load

- `backend/architecture/06_commands.md`: command orchestration
- `backend/architecture/04_context.md`: `ServiceContext` requirements
- `backend/architecture/05_errors.md`: typed errors
- `backend/architecture/44_case.md`: case domain baseline
- `backend/architecture/42_event.md`: event linkage when transitions emit events

## Optional local extension companions

- `backend/architecture/44_case_local.md`
- `backend/architecture/42_event_local.md`

## Execution protocol

1. Define command boundary and workflow transition rules.
2. Implement command orchestration in service layer.
3. Persist state changes and emit relevant events.
4. Map failures to typed domain errors.
5. Add tests for success/failure and illegal transitions.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Command enforces valid case transitions.
- Side effects are explicit and test-covered.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
