# Image Add Command

## Intent

Implement a new image-domain write operation using command patterns.

## Trigger conditions

- User requests upload/process/link/delete image behavior.
- Operation includes writes or file side effects.

## Required inputs

- Command intent and image lifecycle action
- Payload and storage constraints
- Side effect requirements

## Contracts to load

- `backend/architecture/06_commands.md`: command orchestration
- `backend/architecture/04_context.md`: `ServiceContext` requirements
- `backend/architecture/05_errors.md`: typed errors
- `backend/architecture/43_image.md`: image domain baseline
- `backend/architecture/34_file_storage.md`: storage boundary

## Optional local extension companions

- `backend/architecture/43_image_local.md`

## Execution protocol

1. Define command boundary and image lifecycle guards.
2. Implement command orchestration and storage integration.
3. Persist metadata and propagate side effects explicitly.
4. Map failures to typed errors.
5. Add tests for storage and metadata consistency.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Image write flow is deterministic and recoverable.
- Storage boundary and metadata model are contract-aligned.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
