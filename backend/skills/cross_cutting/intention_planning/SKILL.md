# Intention Planning

## Intent

Create and maintain intention plans — goal-oriented documents that define WHAT
the user wants to achieve and WHY, independent of HOW it will be implemented.
Intention plans are the strategic layer; they link to one or more implementation
plans that deliver toward the goal.

## Trigger conditions

- User describes a goal or outcome without a specific implementation task yet.
- A single goal will span multiple implementation cycles or plans.
- You need to capture WHY before deciding WHAT to build.
- User wants to track progress toward a goal across multiple plans.
- User says "I want to achieve", "my goal is", "the intention is", or similar.

## Distinction from implementation planning

| Intention plan | Implementation plan |
|---|---|
| WHAT and WHY | HOW and WHEN |
| Goal-oriented, stable over time | Task-oriented, scoped to one cycle |
| Links to many implementation plans | Links back to one intention plan |
| `under_construction/intention/` | `under_construction/implementation/` |
| Status: active / achieved / paused | Status: under_construction / approved / implemented |

## Required inputs

- Goal statement in natural language
- Why the goal matters (business or product motivation)
- Known constraints or non-goals
- Any existing implementation plans to link (optional at creation time)

## Contracts to load

- `backend/skills/_shared/plan_lifecycle_contract.md`: lifecycle states

## Execution protocol

1. Ask the user to state the goal in one sentence if it is not already clear.
2. Extract success criteria — measurable, not vague. Ask if missing.
3. Set scope boundary: what is explicitly out of scope or a non-goal.
4. Identify any existing implementation plans to link.
5. Create the intention plan at:
   `backend/docs/architecture/under_construction/intention/INTENTION_<slug>_<YYYYMMDD>.md`
   using `TEMPLATE_INTENTION_PLAN.md` as the base.
6. When a new implementation plan is created for this goal, add it to the
   "Linked implementation plans" table and add a progress note.
7. Update status to `achieved` when all success criteria are met.
8. Update status to `paused` or `abandoned` with a reason if the goal changes.

## Output format

Follow `backend/skills/_shared/output_format.md`.

Saved path: `backend/docs/architecture/under_construction/intention/INTENTION_<slug>_<YYYYMMDD>.md`

## Done criteria

- Goal is stated in one clear sentence.
- Success criteria are measurable (not "improve" or "better").
- Scope boundary is explicit.
- Document saved to the correct path.
- Linked implementation plans table is populated (or marked "none yet").

## Quality gate

Apply `backend/skills/_shared/quality_gate.md`.
