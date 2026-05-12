# Plan Lifecycle Contract

Use this contract when agents manage implementation plans across multiple stages.

## Required states

1. `under_construction`: plan being drafted/reviewed
2. `approved`: plan accepted for implementation
3. `implemented`: code changes completed
4. `summarized`: implementation summary written
5. `archived`: plan archived with references
6. `debugging`: post-implementation defect lifecycle active

## Transition rules

- `under_construction` -> `approved` only after explicit review corrections.
- `approved` -> `implemented` only with stable scope and acceptance criteria.
- `implemented` -> `summarized` requires validation evidence.
- `summarized` -> `archived` requires trace links to plan + summary.
- `implemented` -> `debugging` when defects are reported.
- `debugging` loops through plan/review/implement/summary/archive with nested references.

## Nested debug references

Every debug plan must include:

- `parent_plan`
- `parent_summary`
- `issue_reference`
- `debug_iteration`
