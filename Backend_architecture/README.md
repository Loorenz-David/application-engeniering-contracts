# Backend Standard Contract

This contract defines the engineering rules for every Flask/SQLAlchemy backend application built at this organization. It is application-agnostic and can be used to build any type of product — SaaS platforms, marketplaces, internal tools, or field service applications.

Agents and engineers **must** read this contract before writing any code. Every rule here has a reason. The reason is stated so you can apply judgment to edge cases rather than blindly following the letter.

---

## How this contract is organized

### Core architecture
| File | Covers |
|---|---|
| [01_architecture.md](01_architecture.md) | Layer map, folder structure, dependency rules |
| [02_app_factory.md](02_app_factory.md) | App factory, config, env loading, middleware |
| [03_models.md](03_models.md) | ORM model contract, table rules, migration safety |
| [04_context.md](04_context.md) | `ServiceContext` — what it is, what it must not carry |
| [05_errors.md](05_errors.md) | Error hierarchy, codes, HTTP status mapping |

### Service layer
| File | Covers |
|---|---|
| [06_commands.md](06_commands.md) | Write operations — structure, transaction boundaries, event emission |
| [07_queries.md](07_queries.md) | Read operations — structure, pagination, serialization |
| [08_domain.md](08_domain.md) | Pure domain logic — guards, state machines, calculations |

### Transport layer
| File | Covers |
|---|---|
| [09_routers.md](09_routers.md) | Router layer — what routers do and do not own |
| [10_auth.md](10_auth.md) | JWT, RBAC decorators, app-scope guards |
| [20_api_versioning.md](20_api_versioning.md) | When and how to version, backwards compatibility, sunset policy |

### Infrastructure
| File | Covers |
|---|---|
| [11_infra_events.md](11_infra_events.md) | Event bus, outbox pattern, builders, handlers |
| [12_infra_redis.md](12_infra_redis.md) | Redis — connection, key conventions, TTL rules |
| [13_sockets.md](13_sockets.md) | WebSocket / Socket.IO — real-time event contract |
| [16_background_jobs.md](16_background_jobs.md) | RQ workers, queues, retry policies, job idempotency |
| [19_integrations.md](19_integrations.md) | Adapter pattern, credentials, webhooks, graceful degradation |

### Quality & Security
| File | Covers |
|---|---|
| [15_testing.md](15_testing.md) | Test pyramid, fixtures, unit vs integration, what must be tested |
| [17_logging.md](17_logging.md) | Log levels, structured fields, security events, what not to log |
| [18_security.md](18_security.md) | Input validation, output sanitization, CORS, secrets, rate limiting, IDOR |
| [22_performance.md](22_performance.md) | N+1 prevention, query limits, pagination, caching, connection pool |

### Reference
| File | Covers |
|---|---|
| [21_naming_conventions.md](21_naming_conventions.md) | Files, functions, classes, DB, routes, Redis keys, env vars |
| [23_documentation.md](23_documentation.md) | `docs/` folder structure, living doc templates, maintenance discipline, ADRs |

### Patterns & Operations
| File | Covers |
|---|---|
| [24_multi_tenancy.md](24_multi_tenancy.md) | Workspace architecture — 5-table model, membership join, JWT session, registration flow, workspace switching, `workspace_id` enforcement |
| [25_soft_delete.md](25_soft_delete.md) | `is_deleted`/`deleted_at` columns, query filtering, cascade strategy, restore pattern |
| [26_dependency_management.md](26_dependency_management.md) | Package evaluation, pinning, CVE cadence, approved list, AI agent guidance |
| [27_cli_scripts.md](27_cli_scripts.md) | Flask CLI commands, backfill structure, `--dry-run`, idempotency, seed and report commands |
| [28_roles_permissions.md](28_roles_permissions.md) | RBAC — two-layer model (tier + permissions), Permission enum, JWT embedding, `require_permission`, custom roles, domain constraints |
| [29_feature_workflow.md](29_feature_workflow.md) | Step-by-step playbook — new domain, new endpoint, new application; definition of done checklist |
| [30_migrations.md](30_migrations.md) | Alembic migration contract — zero-downtime patterns, NOT NULL sequence, concurrent indexes, enum types, dangerous operations checklist |
| [32_concurrency.md](32_concurrency.md) | Pessimistic locking (`SELECT FOR UPDATE`), optimistic locking (version column), idempotency keys, job deduplication |
| [33_deployment.md](33_deployment.md) | Migration-to-code ordering, pre-deploy checklist, rollback procedure, feature flags, smoke tests |
| [34_file_storage.md](34_file_storage.md) | Presigned URL upload flow, storage key naming, MIME validation, orphan cleanup, download URLs, storage adapter |
| [37_scheduled_jobs.md](37_scheduled_jobs.md) | Time-based scheduled jobs — scheduler setup, batching, idempotency, job catalog |

### Observability
| File | Covers |
|---|---|
| [31_health_observability.md](31_health_observability.md) | `/health`, `/ready`, `/live` endpoints, metrics, alerting rules, application info endpoint |

### Compliance & Privacy
| File | Covers |
|---|---|
| [35_gdpr_erasure.md](35_gdpr_erasure.md) | Right to erasure — PII inventory, erasure workflow, hard delete vs anonymize, retention holds, storage erasure |
| [36_audit_log.md](36_audit_log.md) | Tamper-evident audit trail — model, write pattern, event naming, tamper-evidence rules, retention policy |

---

## Navigation matrix — what to read for each task

Use this table to find the minimum set of contracts you need to read before starting a task. Start with the workflow contract, then read the domain-specific contracts it references.

| Task | Start here | Then read |
|---|---|---|
| **Bootstrap a new application** | [29_feature_workflow.md §C](29_feature_workflow.md) | 02, 24, 10, 28, 30 |
| **Add a new domain (model + CRUD)** | [29_feature_workflow.md §A](29_feature_workflow.md) | 03, 06, 07, 08, 09, 15, 30 |
| **Add a new endpoint to an existing domain** | [29_feature_workflow.md §B](29_feature_workflow.md) | 06 or 07, 09, 15 |
| **Add a new state transition** | [08_domain.md](08_domain.md) | 06, 03 |
| **Add a role or permission** | [28_roles_permissions.md](28_roles_permissions.md) | 10, 06 |
| **Add an external integration (SMS, email, webhook)** | [19_integrations.md](19_integrations.md) | 16, 11, 18 |
| **Add a background job** | [16_background_jobs.md](16_background_jobs.md) | 11, 12 |
| **Add a real-time Socket.IO event** | [13_sockets.md](13_sockets.md) | 11 |
| **Write or modify a migration** | [30_migrations.md](30_migrations.md) | 03 |
| **Debug a permission or auth issue** | [10_auth.md](10_auth.md) | 28, 04 |
| **Debug a multi-tenant data leak** | [24_multi_tenancy.md](24_multi_tenancy.md) | 07, 18 |
| **Add Redis caching** | [12_infra_redis.md](12_infra_redis.md) | 22 |
| **Improve query performance** | [22_performance.md](22_performance.md) | 07, 12 |
| **Add a CLI backfill command** | [27_cli_scripts.md](27_cli_scripts.md) | 30 |
| **Version a breaking API change** | [20_api_versioning.md](20_api_versioning.md) | 09 |
| **Add structured logging** | [17_logging.md](17_logging.md) | 05 |
| **Write tests for a command or query** | [15_testing.md](15_testing.md) | 06 or 07 |
| **Handle concurrent writes / prevent race conditions** | [32_concurrency.md](32_concurrency.md) | 06 |
| **Make a command idempotent** | [32_concurrency.md](32_concurrency.md) | 06, 16 |
| **Deploy to production** | [33_deployment.md](33_deployment.md) | 30 |
| **Roll back a failed deploy** | [33_deployment.md](33_deployment.md) | 30 |
| **Add file upload / attachment support** | [34_file_storage.md](34_file_storage.md) | 03, 06, 27 |
| **Wire health checks and monitoring** | [31_health_observability.md](31_health_observability.md) | 17 |
| **Evolve an event schema** | [11_infra_events.md](11_infra_events.md) | 16 |
| **Add a scheduled / recurring job** | [37_scheduled_jobs.md](37_scheduled_jobs.md) | 16, 27 |
| **Implement GDPR erasure** | [35_gdpr_erasure.md](35_gdpr_erasure.md) | 25, 34, 36 |
| **Add an audit trail to a command** | [36_audit_log.md](36_audit_log.md) | 06 |

Contract numbers refer to the file prefix (e.g., `06` = `06_commands.md`).

---

## Scope boundary

This contract covers the **backend application layer** only: Flask services, database, auth, infrastructure, and operations. It does not cover agents, MCP servers, LLM orchestration, tool calling, prompt engineering, or multi-agent coordination. Those concerns live in a separate [`AI_Architecture/`](../AI_Architecture/README.md) set.

The seam between the two sets is the service layer. Agent tool functions call existing commands and queries — the backend has no knowledge of the agent calling it. A command receives a `ServiceContext` whether it was triggered by an HTTP request or an agent tool call.

See [`AI_Architecture/`](../AI_Architecture/README.md) for the full agent and MCP contract set.

---

## Non-negotiable rules (memorize these)

1. **Routers own zero business logic.** They validate input shape, build `ServiceContext`, call `run_service`, and return a response. Nothing else.
2. **Commands own all writes, events, and side effects.** One command = one business intent. No command calls another command directly.
3. **Queries own zero writes.** A function that reads must not mutate.
4. **Domain functions are pure Python.** No ORM, no HTTP, no I/O of any kind.
5. **`ServiceContext` carries identity and incoming data only.** It is not a configuration object. Do not add boolean flags to it.
6. **Every public function has a return type annotation.** No exceptions.
7. **`DomainError` and its subclasses are the only errors that cross layer boundaries.** Never let `SQLAlchemyError`, `KeyError`, or `AttributeError` bubble to the router.
8. **Never alter the database manually.** All schema changes go through Alembic migrations.
9. **Documentation is updated in the same PR as the code.** A new endpoint without a shape in `docs/domains/<domain>/api.md` is an incomplete PR. An outdated doc is a bug.

---

## Quick start for a new application

```
my_app/
├── __init__.py              # create_app() factory
├── config/
│   ├── default.py
│   ├── development.py
│   ├── testing.py
│   └── production.py
├── models/
│   ├── __init__.py          # db instance + all table imports
│   └── tables/
│       └── <domain>/
├── domain/                  # Pure Python — no I/O
│   └── <domain>/
├── services/
│   ├── context.py           # ServiceContext
│   ├── outcome.py           # StatusOutcome
│   ├── run_service.py       # run_service()
│   ├── commands/
│   │   └── <domain>/
│   ├── queries/
│   │   └── <domain>/
│   └── infra/
│       ├── events/
│       └── redis/
├── routers/
│   ├── http/
│   │   └── response.py
│   ├── utils/
│   │   ├── jwt_handler.py
│   │   └── role_decorator.py
│   └── api_v1/
│       └── <domain>.py
├── errors/
│   ├── base.py
│   ├── not_found.py
│   ├── permissions.py
│   └── validation.py
└── sockets/
    └── register.py
```
