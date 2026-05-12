# Case Add Query

## Intent

Implement a new case-domain read operation using query patterns.

## Trigger conditions

- User asks for list/get/search/filter behavior in case domain.
- Request has no write side effects.

## Required inputs

- Query intent and filters
- Output shape requirements

## Contracts to load

- `backend/architecture/07_queries.md`: query contract
- `backend/architecture/04_context.md`: context and scope
- `backend/architecture/44_case.md`: case baseline data model
- `backend/architecture/46_serialization.md`: response shaping

## Optional local extension companions

- `backend/architecture/44_case_local.md`
- `backend/architecture/46_serialization_local.md`

## Execution protocol

1. Define query semantics and constraints.
2. Implement workspace-safe read logic.
3. Serialize output according to contract.
4. Add tests for filters, permissions, and edge cases.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Query has no side effects.
- Output is deterministic and contract-aligned.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
