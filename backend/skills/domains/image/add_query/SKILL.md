# Image Add Query

## Intent

Implement a new image-domain read operation using query patterns.

## Trigger conditions

- User asks for list/get/search image metadata or retrieval context.
- Request has no write side effects.

## Required inputs

- Query intent and filters
- Output fields and access policy

## Contracts to load

- `backend/architecture/07_queries.md`: query contract
- `backend/architecture/04_context.md`: context and scope
- `backend/architecture/43_image.md`: image data baseline
- `backend/architecture/46_serialization.md`: response shaping

## Optional local extension companions

- `backend/architecture/43_image_local.md`
- `backend/architecture/46_serialization_local.md`

## Execution protocol

1. Define query semantics and access constraints.
2. Implement workspace-safe read path.
3. Serialize output according to contract.
4. Add tests for filters, visibility, and edge cases.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Query is side effect free.
- Output shape is stable and contract-driven.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
