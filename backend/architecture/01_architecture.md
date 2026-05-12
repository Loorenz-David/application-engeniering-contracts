# 01 — Architecture Contract

## Layer map

```
HTTP Request
     │
     ▼
┌─────────────┐
│   Routers   │  FastAPI route handlers — input/output only
└──────┬──────┘
       │  ServiceContext  (identity + incoming data + AsyncSession)
       ▼
┌─────────────┐
│  Commands   │  Write path: DB writes, event emission, side effects
│  Queries    │  Read path: DB reads, serialization
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Domain    │  Pure Python: rules, guards, state machines, calculations
└──────┬──────┘
       │
       ▼
┌─────────────┐
│   Models    │  SQLAlchemy ORM table definitions only
└─────────────┘
       │
       ▼ (separate path)
┌─────────────┐
│    Infra    │  Events, Redis, task queues, external HTTP clients
└─────────────┘
```

---

## Hard dependency rules

| Layer | May import | Must NOT import |
|---|---|---|
| `routers/` | `services/context`, `services/run_service`, `services/commands/*`, `services/queries/*`, `errors/`, `routers/http`, `routers/utils`, `models/database` (for `get_db`) | `models/tables/` directly, `domain/`, `services/infra/` |
| `services/commands/` | `models/`, `domain/`, `services/infra/`, `errors/` | Other commands, `routers/` |
| `services/queries/` | `models/`, `domain/`, `errors/` | `services/infra/` (no side effects), `services/commands/`, `routers/` |
| `domain/` | `errors/`, `domain/<domain>/enums.py` (own layer) | `models/`, `services/`, `routers/`, any I/O |
| `models/` | SQLAlchemy, stdlib, `domain/<domain>/enums.py` | Everything else in `services/`, `domain/`, `routers/` |
| `services/infra/` | `models/`, `errors/`, external SDKs | `routers/`, `services/commands/`, `services/queries/` |

If a layer needs to call something at a higher layer, the design is wrong. Invert the dependency or introduce an interface.

---

## Folder structure contract

### Top-level layout

```
my_app/
├── __init__.py              # App factory only — create_app() + lifespan
├── config.py                # pydantic-settings Settings class
├── models/
│   ├── __init__.py          # Imports all tables — required for Alembic autogenerate
│   ├── base.py              # DeclarativeBase
│   ├── database.py          # Async engine, AsyncSession, get_db dependency
│   └── tables/              # One file per table, grouped by domain
├── domain/                  # Pure business logic (no I/O)
├── services/
│   ├── context.py           # ServiceContext
│   ├── outcome.py           # Outcome(ok/err)
│   ├── run_service.py       # async run_service()
│   ├── commands/            # Write operations, grouped by domain
│   ├── queries/             # Read operations, grouped by domain
│   └── infra/               # Events, Redis, task queues, external adapters
├── routers/
│   ├── http/                # build_ok(), build_err()
│   ├── utils/               # jwt_dep.py, roles.py
│   └── api_v1/              # APIRouter per domain
├── sockets/
│   ├── handlers.py          # @router.websocket("/ws") endpoint
│   ├── manager.py           # ConnectionManager singleton
│   └── pubsub_listener.py   # Redis pub/sub → ConnectionManager dispatcher
├── errors/                  # DomainError hierarchy
└── workers/                 # Standalone worker entry points
```

### Domain grouping rule

All files that belong to the same business domain (e.g., `record`) are grouped under that domain's folder at every layer:

```
services/commands/<domain>/
services/queries/<domain>/
domain/<domain>/
models/tables/<domain>/
routers/api_v1/<domain>.py
```

This makes it trivial to audit a feature: trace `<domain>` across every layer vertically.

### One file, one responsibility

A file's purpose must be nameable in one sentence. If it cannot, split it.

- `create_record.py` — orchestrates the record creation flow
- `record_states.py` — defines valid states and transition rules (domain)
- `list_records.py` — reads and paginates records

Never create `utils.py`, `helpers.py`, or `misc.py`. Give the file a name that states what it does.

---

## What is NOT in scope for this contract

- Frontend code — governed by `Front_end/AGENTS.md`
- Third-party SDK internals
- Infrastructure provisioning (Terraform, Docker, etc.)

---

## Local Contract Extensions

Canonical contracts in `backend/architecture/` are the shared baseline. They are **never modified** for app-specific requirements.

When an app extends a canonical contract (adds fields, overrides behaviour, documents local decisions), it does so via a **companion file** named `<N>_<contract>_local.md` in the same `backend/architecture/` folder:

```
backend/architecture/
  41_user.md            ← canonical (never touched)
  41_user_local.md      ← app-specific extensions
```

### Companion file format

Every `*_local.md` file **must** open with an `Extends:` declaration so both humans and AI agents can trace the relationship immediately:

```md
# <Contract Name> — Local Extensions
> Extends: <N>_<contract>.md

## Added Fields
- `salary: Decimal` — employee compensation, nullable, soft-deleted with user

## Overridden Behaviour
- <describe what changes and why>

## Local Decisions
- <document any app-specific design choices>
```

### Rules

| Change type | Where it goes |
|---|---|
| Core rule / pattern that benefits all apps | Update canonical here in `application_contracts`, re-stamp |
| App-specific field, relation, or behaviour | `*_local.md` companion in the app's `backend/architecture/` |
| Temporary or experimental | Comment inside `*_local.md`, not in canonical |

### How the resolver uses local companions

The resolver (`task_system/resolver.py`) automatically detects `*_local.md` companions alongside every resolved canonical contract and includes them in the execution plan. The AI therefore receives both the canonical baseline and the app-specific delta without any manual wiring.
