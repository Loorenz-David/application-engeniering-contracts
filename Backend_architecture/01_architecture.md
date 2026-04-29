# 01 — Architecture Contract

## Layer map

```
HTTP Request
     │
     ▼
┌─────────────┐
│   Routers   │  Blueprint handlers — input/output only
└──────┬──────┘
       │  ServiceContext
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
│    Infra    │  Events, Redis, Celery, external HTTP clients
└─────────────┘
```

---

## Hard dependency rules

| Layer | May import | Must NOT import |
|---|---|---|
| `routers/` | `services/context`, `services/run_service`, `services/commands/*`, `services/queries/*`, `errors/`, `routers/http`, `routers/utils` | `models/` directly, `domain/`, `services/infra/` |
| `services/commands/` | `models/`, `domain/`, `services/infra/`, `errors/` | Other commands, `routers/` |
| `services/queries/` | `models/`, `domain/`, `errors/` | `services/infra/` (no side effects), `services/commands/`, `routers/` |
| `domain/` | `errors/` only | `models/`, `services/`, `routers/`, any I/O |
| `models/` | SQLAlchemy, stdlib | Everything in `services/`, `domain/`, `routers/` |
| `services/infra/` | `models/`, `errors/`, external SDKs | `routers/`, `services/commands/`, `services/queries/` |

If a layer needs to call something at a higher layer, the design is wrong. Invert the dependency or introduce an interface.

---

## Folder structure contract

### Top-level layout
```
my_app/
├── __init__.py              # App factory only
├── config/                  # Environment-specific config classes
├── models/                  # ORM definitions and db instance
├── domain/                  # Pure business logic (no I/O)
├── services/
│   ├── context.py
│   ├── outcome.py
│   ├── run_service.py
│   ├── commands/            # Write operations, grouped by domain
│   ├── queries/             # Read operations, grouped by domain
│   └── infra/               # Events, Redis, queues, external adapters
├── routers/
│   ├── http/                # Response builder
│   ├── utils/               # JWT, role decorators, compression
│   └── api_v1/              # Blueprint per domain
├── errors/                  # DomainError hierarchy
└── sockets/                 # Socket.IO handlers
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
