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

### Files the agent must not read as pattern references

Any file outside the contracts list and the files being created or modified is off-limits as a pattern source. The governing contract is the reference — not an existing file in the same codebase.

Must NOT read for patterns:
- `services/commands/<any_other_domain>/` — contract `06_commands.md` defines the pattern
- `services/queries/<any_other_domain>/` — contract `07_queries.md` defines the pattern
- `domain/<any_other_domain>/` — contract `08_domain.md` defines the pattern
- `routers/api_v1/<any_existing_router>.py` — contract `09_routers.md` defines the pattern

May read (factual lookup only, not pattern reference):
- Files listed in "Implementation plan" steps as being created or modified
- Model files for exact field names or column types
- `__init__.py` files to verify existing import paths

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
