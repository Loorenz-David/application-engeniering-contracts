# Backend Skill Quality Gate

Before concluding a backend skill run, verify all checks below.

## Contract adherence

- Business logic is in commands/queries, not routers/models.
- `ServiceContext` is passed through service entry points.
- Error handling uses typed domain errors.
- Workspace/multi-tenant constraints remain enforced.

## Architecture boundaries

- Routers do not contain orchestration logic.
- Commands and queries are separated correctly.
- No prohibited imports across layers.

## Local extension handling

- Canonical contract files remain unchanged for app-specific behavior.
- Companion `*_local.md` files are used for local overrides/extensions.

## Validation

- Relevant tests/checks were run, or gap is explicitly stated.
- Contract reference checker passes when guide docs are touched.
