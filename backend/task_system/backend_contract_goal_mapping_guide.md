# Backend Contract Goal Mapping Guide (Local Entrypoint)

Purpose: backend-local routing guide that selects contracts from `../architecture/`.

## Resolution policy

1. Start with backend core contracts.
2. Add one primary goal bundle.
3. Add trigger-based expansions only when the goal explicitly requires them.
4. Keep the selected set minimal and sufficient.

---

## Pattern authority rule

**Read contracts to learn how to write. Read implementation files to learn what already exists.**

This is the core discipline. The two questions have different answers:

| Question | Source |
|---|---|
| How do I structure a command? | `06_commands.md` |
| How do I wire a router handler? | `09_routers.md` |
| How do I write a serializer? | `46_serialization.md` |
| What does this existing endpoint return? | Implementation file |
| What fields does this model have? | Implementation file or model file |
| How does service X connect to router Y? | Implementation file |
| What does an existing command do for context? | Implementation file |

**The test before opening any implementation file:**

> "Am I reading this to understand how to structure my new code — or to understand what this existing code does?"

- If **how to write** → stop, read the contract instead. If the contract feels incomplete, ask for clarification.
- If **what exists** → read it. Understanding existing behavior, return shapes, module connections, and field names is legitimate and expected.

**The specific drift to avoid:**

Reading `services/commands/<other_domain>/some_command.py` to understand the command structure (session.add, flush, error raising) when `06_commands.md` already defines it is a protocol violation — it consumes tokens without adding information the contract doesn't already contain. The same applies to reading an existing router to understand the handler skeleton when `09_routers.md` defines it.

If a contract's pattern feels ambiguous, re-read it carefully or ask for clarification — never open an unrelated implementation file as a substitute.

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
