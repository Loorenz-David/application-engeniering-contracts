# Backend Contract Goal Mapping Guide (Local Entrypoint)

Purpose: backend-local routing guide that selects contracts from `../architecture/`.

## Resolution policy

1. Start with backend core contracts.
2. Add one primary goal bundle.
3. Add trigger-based expansions only when the goal explicitly requires them.
4. Keep the selected set minimal and sufficient.

---

## Pattern authority rule

**Contracts are the sole pattern source. Reading an existing implementation file as a pattern reference is a protocol violation.**

If a contract defines a structure — command signature, request parser shape, serializer structure, router skeleton, transaction boundary — the contract is the authority. No implementation file may be read to "confirm" or "see how it's done" for that pattern.

**Permitted reads:**
- Files being directly created or modified in this task
- Model files for exact field names or column types (factual lookup)
- `__init__.py` files to verify existing import paths

**Forbidden reads (pattern reference):**
- `services/commands/<other_domain>/` — `06_commands.md` is the pattern source
- `services/queries/<other_domain>/` — `07_queries.md` is the pattern source
- `domain/<other_domain>/serializers.py` — `46_serialization.md` is the pattern source
- `routers/api_v1/<existing_router>.py` — `09_routers.md` is the pattern source

If a contract feels ambiguous, the correct response is to re-read that contract carefully or ask for clarification — not to open an implementation file for guidance.

---

## Core contracts (always include)

- `../architecture/01_architecture.md`
- `../architecture/04_context.md`
- `../architecture/05_errors.md`
- `../architecture/06_commands.md`
- `../architecture/07_queries.md`
- `../architecture/09_routers.md`
- `../architecture/21_naming_conventions.md`
- `../architecture/40_identity.md`
- `../architecture/41_user.md`
- `../architecture/42_event.md`
- `../architecture/48_presence.md`

## Goal bundles (starter)

### CRUD + realtime

Add:
- `../architecture/03_models.md`
- `../architecture/08_domain.md`
- `../architecture/11_infra_events.md`
- `../architecture/13_sockets.md`
- `../architecture/30_migrations.md`
- `../architecture/15_testing.md`

### Worker-driven backend

Add:
- `../architecture/16_background_jobs.md`
- `../architecture/12_infra_redis.md`
- `../architecture/51_worker_runtime.md`
- `../architecture/49_observability_runtime.md`
- `../architecture/54_ci_cd_runtime.md`

### Replayable async runtime

Add:
- `../architecture/11_infra_events.md`
- `../architecture/16_background_jobs.md`
- `../architecture/52_replayability.md`
- `../architecture/49_observability_runtime.md`
- `../architecture/53_operational_cli.md`

### CI-validated runtime

Add:
- `../architecture/33_deployment.md`
- `../architecture/31_health_observability.md`
- `../architecture/30_migrations.md`
- `../architecture/54_ci_cd_runtime.md`
- `../architecture/49_observability_runtime.md`

## Trigger expansion map

- "worker", "retry", "dead letter", "dlq", "stale task" -> `16`, `12`, `51`
- "replay", "reprocess", "recover" -> `52`, `11`, `16`, `53`
- "observability", "correlation", "structured logs" -> `49`, `17`, `31`
- "ci", "pipeline", "readiness" -> `54`, `33`, `31`, `30`
- "deterministic testing", "fixture isolation", "n+1" -> `50`, `15`, `30`
- "rate limit", "rate limiting" -> `18`, `12`
- "timeout", "request timeout" -> `02`
- "cache", "query cache", "result cache" -> `07`, `12`
- "bulk insert", "batch write" -> `22`
- "multipart", "large file upload" -> `34`

## Output format (required before coding)

Selected contracts:
- `<file>`: `<reason>`

Added from guide:
- `<file>`: `<trigger + justification>`

Local extensions loaded:
- `<canonical>_local.md`: `<what changed locally>`

Excluded contracts:
- `<file>`: `<why not needed now>`

## Document-only protocol (no resolver)

Use this protocol when the guide is the only entry point and no Python tooling is executed.

1. Build an initial list from:
- Core contracts
- One goal bundle
- Trigger expansion map (only explicit triggers)
2. For each selected canonical contract `../architecture/N_name.md`, check whether a companion `../architecture/N_name_local.md` exists.
3. If companion exists, load both files in this order:
- Canonical first (`N_name.md`)
- Local extension second (`N_name_local.md`)
4. Merge interpretation with precedence:
- Canonical defines baseline rules.
- Local companion may add fields, constraints, and app-specific behavior.
- If canonical and local conflict, local wins for this app, but canonical remains unchanged.
5. Report both baseline and delta explicitly in the plan before coding.

### Required read order block in agent output

Agents should include this section before implementation:

Read order:
- `../architecture/<canonical>.md` (baseline)
- `../architecture/<canonical>_local.md` (app delta, if present)

Applied precedence:
- Local extension overrides baseline only for this app.

---

## Local contract extensions

Canonical contracts in `../architecture/` are **never modified** for app-specific requirements.

When an app extends a canonical contract, use a `*_local.md` companion file in the same folder:

```
../architecture/41_user.md          ← canonical (read-only)
../architecture/41_user_local.md    ← app-specific extensions
```

Every companion must open with:
```md
> Extends: 41_user.md
```

When using tooling, the resolver automatically detects and surfaces `*_local.md` files alongside their canonical counterparts.
When running document-only, agents must apply the protocol above manually.

`run/bootstrap_backend_system.py` scaffolds empty stubs for all domain contracts (40-48) on init.

**Rule:** If a change benefits all apps → update canonical here and re-stamp. If it is app-specific → write it in the `*_local.md` companion.
