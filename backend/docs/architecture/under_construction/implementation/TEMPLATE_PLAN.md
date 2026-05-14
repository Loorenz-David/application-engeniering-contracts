# PLAN_<slug>_<YYYYMMDD>

## Metadata

- Plan ID: `PLAN_<slug>_<YYYYMMDD>`
- Status: `under_construction`
- Owner agent: `<agent_name>`
- Created at (UTC): `<YYYY-MM-DDTHH:MM:SSZ>`
- Last updated at (UTC): `<YYYY-MM-DDTHH:MM:SSZ>`
- Related issue/ticket: `<id_or_link>`
- Intention plan: `backend/docs/architecture/under_construction/intention/INTENTION_<slug>_<YYYYMMDD>.md`

## Goal and intent

- Goal:
- Business/user intent:
- Non-goals:

## Scope

- In scope:
- Out of scope:
- Assumptions:

## Clarifications required

- [ ] `<question>` — why this blocks safe implementation
- [ ] `<question>` — why this blocks safe implementation

## Acceptance criteria

1. `<measurable outcome>`
2. `<measurable outcome>`

## Contracts and skills

### Contracts loaded

- `<backend/architecture/file.md>`: `<reason>`

### Local extensions loaded

- `<backend/architecture/file_local.md>`: `<delta used>`

### File read intent — pattern vs. relational

Before reading any implementation file outside this plan's scope, apply the test:

> "Am I reading this to understand **how to write** my new code — or to understand **what this existing code does**?"

- **How to write** → read the contract instead (`06_commands.md`, `09_routers.md`, etc.)
- **What exists** → reading is legitimate (existing behavior, return shapes, field names, module connections)

Prohibited (pattern reads — contract already covers these):
- Reading another command to understand session.add / flush / error-raising shape → `06_commands.md`
- Reading another router to understand handler wiring → `09_routers.md`
- Reading another serializer to understand output shape → `46_serialization.md`

Permitted (relational reads — understanding what exists):
- Reading an existing endpoint to see what it currently returns
- Reading model files for exact field names and types
- Reading `__init__.py` files to verify import paths
- Reading related domain files to understand how existing logic connects

### Skill selection

- Primary skill: `<path/to/SKILL.md>`
- Router trigger terms: `<term1, term2>`
- Excluded alternatives: `<skill path>` — `<why excluded>`

## Implementation plan

1. `<step>`
2. `<step>`
3. `<step>`

## Risks and mitigations

- Risk: `<risk>`
  Mitigation: `<mitigation>`

## Validation plan

- `<command/check>`: `<expected result>`

## Review log

- `<YYYY-MM-DD>` `<reviewer>`: `<feedback>`
- `<YYYY-MM-DD>` `<owner>`: `<correction applied>`

## Lifecycle transition

- Current state: `under_construction`
- Next state: `<approved | debugging>`
- Transition owner: `<agent_name>`
