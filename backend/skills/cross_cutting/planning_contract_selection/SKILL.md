# Planning Contract Selection

## Intent

Select the minimum sufficient backend contracts for a requested task and define
a safe implementation plan before coding.

## Trigger conditions

- Use when task intent is broad or partially ambiguous.
- Use when multiple domains may be involved.
- Do not use when a domain-specific skill already fully matches.

## Required inputs

- User goal statement
- Current backend code context (if available)

## Contracts to load

- `backend/architecture/01_architecture.md`: layer boundaries
- `backend/architecture/04_context.md`: service seam
- `backend/architecture/05_errors.md`: error model
- `backend/architecture/06_commands.md`: write-path rules
- `backend/architecture/07_queries.md`: read-path rules
- `backend/architecture/09_routers.md`: API boundary rules

## Optional local extension companions

- Load `*_local.md` companions for selected canonical contracts when present.

## Execution protocol

1. Identify primary domain and operation type (read/write/realtime/worker).
2. Select core + minimum domain/runtime contracts.
3. Check for local companions and apply local precedence.
4. Produce a concrete implementation sequence with exclusions.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Contract set is explicit and minimal.
- Exclusions are documented.
- Plan is implementation-ready.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
