# <Skill Name>

## Intent

Describe the exact task this skill handles.

## Trigger conditions

- `<when this skill should be selected>`
- `<when this skill should not be selected>`

## Required inputs

- `<input>`

## Contracts to load

- `backend/architecture/<file>.md`: `<reason>`

## Optional local extension companions

- `backend/architecture/<file>_local.md`: `<when to load>`

## Execution protocol

1. Resolve intent and scope.
2. Load contracts and local companions.
3. Produce implementation plan.
4. Implement minimal safe changes.
5. Validate.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- `<measurable criteria>`

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
