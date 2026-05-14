# Plan Lifecycle Contract

Use this contract when agents manage implementation plans across multiple stages.

## Plan types

### Implementation plan
Describes HOW to build something. Scoped to one delivery cycle.
Path: `backend/docs/architecture/under_construction/implementation/PLAN_<slug>_<YYYYMMDD>.md`

### Intention plan
Describes WHAT to achieve and WHY. Spans multiple implementation cycles.
Path: `backend/docs/architecture/under_construction/intention/INTENTION_<slug>_<YYYYMMDD>.md`

A single intention plan links to one or more implementation plans.
An implementation plan links back to the intention plan that motivated it.

## Implementation plan states

1. `under_construction`: plan being drafted/reviewed
2. `approved`: plan accepted for implementation
3. `implemented`: code changes completed
4. `summarized`: implementation summary written
5. `archived`: plan archived with references
6. `debugging`: post-implementation defect lifecycle active

## Implementation plan transition rules

- `under_construction` -> `approved` only after explicit review corrections.
- `approved` -> `implemented` only with stable scope and acceptance criteria.
- `implemented` -> `summarized` requires validation evidence.
- `summarized` -> `archived` requires trace links to plan + summary.
- `implemented` -> `debugging` when defects are reported.
- `debugging` loops through plan/review/implement/summary/archive with nested references.

## Intention plan states

1. `active`: goal is being pursued; linked implementation plans are in progress.
2. `paused`: goal is on hold; reason recorded in progress notes.
3. `achieved`: all success criteria are met; links to final implementation plan.
4. `abandoned`: goal dropped; reason and superseding intention recorded.
5. `superseded`: replaced by a newer intention plan; link to successor included.

## Intention plan transition rules

- `active` -> `achieved` only when all measurable success criteria are confirmed met.
- `active` -> `paused` with a stated reason and expected resume condition.
- `active` -> `abandoned` with a stated reason.
- Any state -> `superseded` when a new intention plan replaces this one.

## Nested debug references

Every debug plan must include:

- `parent_plan`
- `parent_summary`
- `issue_reference`
- `debug_iteration`
