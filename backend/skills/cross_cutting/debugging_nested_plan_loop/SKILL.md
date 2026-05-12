# Debugging Nested Plan Loop

## Intent

Run debug-stage planning as a nested lifecycle linked to an implemented parent plan.

## Trigger conditions

- Defect reported after a plan has already been implemented.
- Regression, incident, or missing acceptance criterion found post-delivery.

## Required inputs

- Parent plan path
- Parent summary path
- Issue/ticket reference
- Observed behavior and expected behavior

## Contracts to load

- `backend/skills/_shared/plan_lifecycle_contract.md`: nested lifecycle transitions
- `backend/architecture/05_errors.md`: error and failure modeling
- `backend/architecture/15_testing.md`: regression test expectations
- `backend/architecture/49_observability_runtime.md`: diagnostics and traceability

## Execution protocol

1. Create debug plan in `backend/docs/debugging/` with parent references.
2. Clarify repro conditions and acceptance criteria for fix.
3. Review debug plan and approve.
4. Implement fix and add regression coverage.
5. Write debug summary under `backend/docs/architecture/implemented_summaries/`.
6. Archive debug plan with links to parent plan and debug summary.

## Output format

Follow `backend/skills/_shared/output_format.md`.

## Done criteria

- Parent-child traceability is complete.
- Fix is validated with regression evidence.
- Debug artifacts are archived with references.

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
