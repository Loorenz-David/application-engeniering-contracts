# Backend Skill Router

This file defines deterministic routing from task intent to backend skill path.

## Selection algorithm

1. Normalize user intent to lowercase.
2. Detect operation type first:
- write: `create|add|update|delete|archive|publish|assign|transition`
- read: `get|list|search|find|query|filter|view`
- endpoint: `endpoint|route|router|api|http`
- review: `review|audit|inspect|assess`
- replay: `replay|reprocess|recover|dead letter|dlq|retry`
- clarify: `clarify|unclear|ambiguous|not sure|ask|question|confirm|scope`
- intention: `intention|intention plan|outcome|goal planning|strategic plan|aim|objective|why we`
- align: `plan|planning|intent|goal|approach|strategy|shape`
- lifecycle: `workflow|lifecycle|handoff|archive|summary|implement|implementation stage`
- debugloop: `debug|bug|fix|regression|incident|nested`
3. Detect domain:
- identity: `identity|user|member|account|profile|auth`
- case: `case|ticket|workflow|assignment|status`
- content: `content|article|post|publish|draft|version`
- image: `image|media|upload|thumbnail|asset|file`
- notifications: `notification|notify|alert|fanout|delivery`
- presence: `presence|online|heartbeat|status|activity`
- events: `event|stream|replay|reprocess`
4. Route to domain skill if both operation and domain are found.
5. If no precise match, use planning fallback.

## Deterministic routes

| Domain | Operation | Skill path |
|---|---|---|
| identity | write | `backend/skills/domains/identity/add_command/SKILL.md` |
| identity | read | `backend/skills/domains/identity/add_query/SKILL.md` |
| identity | endpoint | `backend/skills/domains/identity/add_router_endpoint/SKILL.md` |
| case | write | `backend/skills/domains/case/add_command/SKILL.md` |
| case | read | `backend/skills/domains/case/add_query/SKILL.md` |
| case | endpoint | `backend/skills/domains/case/add_router_endpoint/SKILL.md` |
| content | write | `backend/skills/domains/content/add_command/SKILL.md` |
| content | read | `backend/skills/domains/content/add_query/SKILL.md` |
| content | endpoint | `backend/skills/domains/content/add_router_endpoint/SKILL.md` |
| image | write | `backend/skills/domains/image/add_command/SKILL.md` |
| image | read | `backend/skills/domains/image/add_query/SKILL.md` |
| image | endpoint | `backend/skills/domains/image/add_router_endpoint/SKILL.md` |
| notifications | write | `backend/skills/domains/notifications/add_notification_flow/SKILL.md` |
| presence | write | `backend/skills/domains/presence/add_presence_feature/SKILL.md` |
| events | replay | `backend/skills/domains/events/replay_reprocess/SKILL.md` |
| any | intention | `backend/skills/cross_cutting/intention_planning/SKILL.md` |
| any | align | `backend/skills/cross_cutting/goal_intent_alignment/SKILL.md` |
| any | clarify | `backend/skills/cross_cutting/ask_clarification_first/SKILL.md` |
| any | lifecycle | `backend/skills/cross_cutting/plan_lifecycle_orchestrator/SKILL.md` |
| any | debugloop | `backend/skills/cross_cutting/debugging_nested_plan_loop/SKILL.md` |
| any | review | `backend/skills/cross_cutting/code_review_backend/SKILL.md` |
| any | unknown | `backend/skills/cross_cutting/planning_contract_selection/SKILL.md` |

## Tie-break rules

When multiple skills match, apply this priority:

1. `review` operation always wins when explicitly requested.
2. `replay` operation wins over generic write/read for events.
3. `clarify` wins when the user explicitly asks for clarification before coding.
4. `debugloop` wins over generic lifecycle when defect language is explicit.
5. `intention` wins over `align` when the user is focused on goals/outcomes rather than approach.
6. Endpoint requests choose `add_router_endpoint` even if write/read keywords also appear.
7. If two domains match, prefer the first domain term mentioned by the user.
8. If still ambiguous, route to `ask_clarification_first` before planning fallback.

## Output contract

Before implementation, include:

1. Selected skill path
2. Trigger terms found
3. Why alternatives were excluded
