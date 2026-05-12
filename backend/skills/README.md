# Backend Skills System

This folder defines reusable backend skill prompts that map common engineering
tasks directly to backend contracts.

## Purpose

- Speed up repeated backend tasks with pre-bundled contract references.
- Keep agent outputs consistent via shared output and quality gates.
- Reduce ambiguity before coding.

## How skills interact with mapping guides

1. Skills are the default for known, recurring intents.
2. `backend/task_system/backend_contract_goal_mapping_guide.md` is the fallback
   for broad or unknown intents.
3. When the same guide-based path repeats, promote it into a new skill.

## Structure

- `_shared/` common standards used by all backend skills
- `cross_cutting/` workflows spanning multiple domains
- `domains/` domain-scoped skills (identity, events, notifications, presence, case, content, image)

## Authoring rules

- One skill = one outcome.
- Every skill must list exact contracts to load.
- Every skill must use the shared output format and quality gate.
- Skills may extend, never replace, canonical contracts.

## Starting point

For new backend skill authoring, copy `_shared/skill_template.md`.

## Skill selection matrix

Use this matrix to choose a skill before falling back to the mapping guide.

| Intent pattern | Preferred skill | Fallback |
|---|---|---|
| Add or update backend command | `domains/<domain>/add_command/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Add or update backend query | `domains/<domain>/add_query/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Add API endpoint | `domains/<domain>/add_router_endpoint/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Replay or reprocess events | `domains/events/replay_reprocess/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Add notification fanout flow | `domains/notifications/add_notification_flow/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Add realtime presence behavior | `domains/presence/add_presence_feature/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Align goal and implementation intent | `cross_cutting/goal_intent_alignment/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Request is ambiguous and needs clarification | `cross_cutting/ask_clarification_first/SKILL.md` | `cross_cutting/planning_contract_selection/SKILL.md` |
| Manage plan lifecycle across agents | `cross_cutting/plan_lifecycle_orchestrator/SKILL.md` | `cross_cutting/goal_intent_alignment/SKILL.md` |
| Run post-implementation debug loop | `cross_cutting/debugging_nested_plan_loop/SKILL.md` | `cross_cutting/plan_lifecycle_orchestrator/SKILL.md` |
| Backend code review | `cross_cutting/code_review_backend/SKILL.md` | N/A |

When no clear skill matches, use the planning skill first and promote repeated
patterns into a new domain skill.

## Deterministic routing

Use `skill_router.md` for repeatable keyword-based skill selection:

- `backend/skills/skill_router.md`

Core clarification-first skills:

- `backend/skills/cross_cutting/goal_intent_alignment/SKILL.md`
- `backend/skills/cross_cutting/ask_clarification_first/SKILL.md`
- `backend/skills/cross_cutting/plan_lifecycle_orchestrator/SKILL.md`
- `backend/skills/cross_cutting/debugging_nested_plan_loop/SKILL.md`
