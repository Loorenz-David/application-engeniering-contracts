# Goal Intent Alignment

## Intent

Align the user goal, success criteria, scope boundaries, and implementation
intent before any code changes are proposed.

## Trigger conditions

- Use when user requests planning, architecture direction, or approach design.
- Use when a task can be interpreted in multiple valid ways.
- Use when acceptance criteria are not explicit.

## Required inputs

- User objective in natural language
- Known constraints (deadline, stack, environment, contract limits)

## Contracts to load

- `backend/architecture/01_architecture.md`: layer boundaries
- `backend/architecture/04_context.md`: service seam constraints
- `backend/architecture/21_naming_conventions.md`: consistent naming and intent clarity

## Execution protocol

1. Restate the goal in one sentence.
2. Extract explicit requirements and assumptions.
3. Identify missing decisions that block safe implementation.
4. Ask focused clarification questions for blocking decisions.
5. Propose a bounded implementation plan only after answers are received.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Goal and intent are aligned in explicit language.
- Blocking ambiguities are resolved or listed as unanswered.
- Plan is bounded by agreed scope.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
