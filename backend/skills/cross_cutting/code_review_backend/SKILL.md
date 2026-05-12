# Backend Code Review

## Intent

Review backend changes for contract violations, regressions, and missing tests.

## Trigger conditions

- Use when user asks for backend review.
- Use after significant backend changes touching service boundaries.

## Required inputs

- Changed files or diff
- Relevant task context

## Contracts to load

- `backend/architecture/01_architecture.md`: boundary checks
- `backend/architecture/04_context.md`: context flow checks
- `backend/architecture/05_errors.md`: error handling checks
- `backend/architecture/06_commands.md`: write-path correctness
- `backend/architecture/07_queries.md`: read-path correctness
- `backend/architecture/15_testing.md`: testing expectations

## Execution protocol

1. Identify touched layers and risky deltas.
2. Report findings ordered by severity.
3. Include missing tests and contract misalignments.
4. Provide concise remediation guidance.

## Output format

Follow `backend/skills/_shared/output_format.md` for contract context.
For findings, list severity, file, and issue first.

## Done criteria

- Critical/high issues are clearly enumerated.
- Testing and residual risk are explicitly covered.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
