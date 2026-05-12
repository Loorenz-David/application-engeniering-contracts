# Identity Add Command

## Intent

Implement a new identity-domain write operation using command patterns.

## Trigger conditions

- User requests create/update/delete behavior in identity/user domain.
- Operation includes writes or side effects.

## Required inputs

- Command name and intent
- Input payload contract
- Expected side effects/events

## Contracts to load

- `backend/architecture/06_commands.md`: command orchestration
- `backend/architecture/04_context.md`: `ServiceContext` requirements
- `backend/architecture/05_errors.md`: typed errors
- `backend/architecture/40_identity.md`: identity baseline
- `backend/architecture/41_user.md`: user model baseline

## Optional local extension companions

- `backend/architecture/40_identity_local.md`
- `backend/architecture/41_user_local.md`

## Execution protocol

1. Define command boundary and payload validation.
2. Implement domain/service orchestration in command layer.
3. Persist changes and emit events where required.
4. Map failures to typed domain errors.
5. Add or update tests.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Command is context-aware and side effects are explicit.
- Router/model layers remain thin.
- Tests cover success and failure paths.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
