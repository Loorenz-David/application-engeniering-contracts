# Identity Add Query

## Intent

Implement a new identity-domain read operation using query patterns.

## Trigger conditions

- User asks for list/get/search/read behavior.
- Request has no write side effects.

## Required inputs

- Query name and filters
- Output shape/serialization needs

## Contracts to load

- `backend/architecture/07_queries.md`: query contract
- `backend/architecture/04_context.md`: context + auth scope
- `backend/architecture/41_user.md`: user data baseline
- `backend/architecture/46_serialization.md`: response shaping

## Optional local extension companions

- `backend/architecture/41_user_local.md`
- `backend/architecture/46_serialization_local.md`

## Execution protocol

1. Define query contract and filter semantics.
2. Implement read logic with workspace-safe constraints.
3. Serialize output according to contract.
4. Add tests for filter boundaries and permissions.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Query has no side effects.
- Response is deterministic and contract-aligned.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
