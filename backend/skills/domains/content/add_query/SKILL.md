# Content Add Query

## Intent

Implement a new content-domain read operation using query patterns.

## Trigger conditions

- User asks for list/get/search/filter behavior for content.
- Request has no write side effects.

## Required inputs

- Query intent and filters
- Output shape and visibility requirements

## Contracts to load

- `backend/architecture/07_queries.md`: query contract
- `backend/architecture/04_context.md`: context and scope
- `backend/architecture/45_content.md`: content data baseline
- `backend/architecture/46_serialization.md`: output shaping

## Optional local extension companions

- `backend/architecture/45_content_local.md`
- `backend/architecture/46_serialization_local.md`

## Execution protocol

1. Define query semantics and filter constraints.
2. Implement workspace-safe reads.
3. Serialize output according to contract.
4. Add tests for permissions and pagination/filter edges.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Query path is side effect free.
- Output is stable and contract-aligned.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
