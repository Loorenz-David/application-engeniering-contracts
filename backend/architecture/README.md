# Backend Standard Contract

Engineering rules for every FastAPI/SQLAlchemy backend built here. Application-agnostic — works for SaaS platforms, marketplaces, internal tools, field service apps, or API-only services.

**If you are an AI agent:** Do not read all contracts upfront. Read this README first. Use the navigation matrix and feature bundles below to identify the minimum contract set for your task, then read only those files before writing any code.

---

## How this library works

Every contract file has a number prefix. When this README says "read 06, 07, 09" it means `06_commands.md`, `07_queries.md`, `09_routers.md`. The number is the file prefix.

There are two ways to navigate:
- **Single task** → look it up in the [task navigation matrix](#task-navigation-matrix)
- **Full feature or new application** → look it up in the [feature bundles](#feature-bundles)

Always start with the navigation tables, not the contract files. Reading only what the task requires is the correct way to use this library.

---

## Core contracts — always read these first

Before any task, read these eight contracts. They define the invariants every other contract builds on.

| # | File | What it defines |
|---|---|---|
| 01 | [01_architecture.md](01_architecture.md) | Layer map, folder structure, dependency rules |
| 04 | [04_context.md](04_context.md) | `ServiceContext` — the object that flows through every operation |
| 05 | [05_errors.md](05_errors.md) | Error hierarchy and HTTP status mapping |
| 06 | [06_commands.md](06_commands.md) | Write operation structure, transaction boundary, event emission |
| 07 | [07_queries.md](07_queries.md) | Read operation structure, pagination, serialization |
| 09 | [09_routers.md](09_routers.md) | What routers own and must not own |
| 21 | [21_naming_conventions.md](21_naming_conventions.md) | Files, functions, classes, DB, routes, env vars |
| 40 | [40_identity.md](40_identity.md) | `IdentityMixin`, `generate_id()`, ULID prefix registry |
| 41 | [41_user.md](41_user.md) | `User` model, `HistoryRecord` mixin, `UserAppViewRecord`, `UserHistoryRecord` |
| 42 | [42_event.md](42_event.md) | `Event` mixin — operation lifecycle tracking, worker flow integration |
| 48 | [48_presence.md](48_presence.md) | `EntityType` enum, Redis + DB two-layer presence, `RECORD_VIEW_START` / `RECORD_VIEW_END` task handlers |

These eleven define the architecture. Everything else is additive.

---

## Container Runtime Is Foundational (Modular-Monolith First)

Containerization is a first-class infrastructure contract for this architecture.
It is not microservices guidance and does not change the modular-monolith model.

Generated applications should treat these artifacts as standard runtime contract files:
- `Dockerfile`
- `docker-compose.yml`
- `.dockerignore`
- `Makefile`

These files exist to make applications reproducible, deterministic, deployable,
AI-agent friendly, CI-friendly, and operationally observable.

### Runtime environments and intent

| Environment | Intent | Characteristics |
|---|---|---|
| **Validation** | Deterministic bootstrap verification | Dynamic host ports, isolated compose runtime, no dependency on host DB/Redis |
| **Runtime (local/dev)** | Day-to-day feature development | Hybrid or full containerized mode, explicit env contracts |
| **Deployment** | Production execution | Same service boundaries and health/readiness assumptions, stricter secrets and observability |

Dynamic validation ports are intentional. Developers often already run services on
`5432`, `6379`, or `5000`; fixed ports create false failures and non-reproducible
agent runs. Validation must prove runtime behavior independent of host collisions.

### Recommended development modes

| Mode | Backend process | PostgreSQL/Redis | Typical usage |
|---|---|---|---|
| **Hybrid local mode** | Local (`python run.py`) | Local or Docker | Fast inner loop with deterministic infra when needed |
| **Full containerized mode** | Docker Compose | Docker Compose | Repro/debug parity, onboarding, shared troubleshooting |
| **Validation mode** | Isolated validation runtime | Docker Compose with dynamic ports | Bootstrap verification, CI-like checks |
| **CI mode** | Headless deterministic execution | Docker Compose services | Repeatable pipelines and non-interactive checks |

### Container operational principles

- Healthchecks are required for infra and app services.
- Readiness checks are required before tests and validation assertions.
- Services must fail loudly when dependencies are unavailable.
- Startup ordering must be deterministic (`depends_on` + health gates).
- Environment contracts must be explicit (`.env` + compose injection).
- Infrastructure must be reproducible across local, validation, and CI.

### Bootstrap and new-app command baseline

Use these as default operational commands for generated apps:

```bash
make dev-up
make dev-down
docker compose up -d
docker compose down
```

`make dev-up` should default to a developer-friendly mode (usually hybrid infra).
`docker compose --profile app up -d` should be available for full containerized mode
including backend + workers.

---

## Starting point by application type

Use this table before any feature work begins on a new application.

| Application type | Read on day one | Add when needed |
|---|---|---|
| **SaaS platform** (multi-tenant, RBAC, subscriptions) | Core + 02, 03, 08, 10, 24, 28, 29, 30, 38 | 11, 12, 13 (real-time); 16, 37 (jobs); 36 (audit); 35 (GDPR) |
| **Internal tool / backoffice** | Core + 02, 03, 08, 10, 29, 30 | 16 (background jobs); 27 (CLI); 17 (logging) |
| **Marketplace** | Core + 02, 03, 08, 10, 24, 28, 29, 30, 38 | 19 (integrations); 34 (file storage); 43 (images); 44 (cases); 45 (content/mentions); 36 (audit); 32 (concurrency) |
| **Field service app** | Core + 02, 03, 08, 10, 24, 28, 29, 30, 38 | 44 (cases); 45 (content/mentions); 32 (concurrency); 11, 13 (real-time); 16 (background jobs); 43 (images) |
| **API-only service** (headless, no frontend) | Core + 02, 03, 08, 10, 20, 29, 30 | 19 (webhooks); 18 (security); 16 (jobs) |
| **Data pipeline / ETL backend** | Core + 02, 03, 08, 16, 27, 29, 30 | 37 (scheduling); 32 (idempotency); 12 (Redis) |

---

## Task navigation matrix

Minimum read set for a single, scoped task. For compound tasks, combine rows.

### Bootstrapping

| Task | Start here | Then read |
|---|---|---|
| Bootstrap a new application | [29_feature_workflow.md §C](29_feature_workflow.md) | 02, 03, 10, 24, 28, 30, 38 |
| Add container runtime contract to a new app | [33_deployment.md](33_deployment.md) | 02, 12, 16, 31 |
| Add a new domain (model + full CRUD) | [29_feature_workflow.md §A](29_feature_workflow.md) | 03, 06, 07, 08, 09, 15, 30, 38 |
| Add a new endpoint to an existing domain | [29_feature_workflow.md §B](29_feature_workflow.md) | 06 or 07, 09, 15 |

### Models & data

| Task | Start here | Then read |
|---|---|---|
| Design a model | [03_models.md](03_models.md) | 21, 30 |
| Add soft delete to a domain | [25_soft_delete.md](25_soft_delete.md) | 03, 07, 06 |
| Restore a soft-deleted entity | [25_soft_delete.md](25_soft_delete.md) | 06, 07 |
| Write or modify a migration | [30_migrations.md](30_migrations.md) | 03 |
| Add a new state transition | [08_domain.md](08_domain.md) | 06, 03 |
| Resolve an entity by `client_id` | [38_identity_resolution.md](38_identity_resolution.md) | 03, 06, 07, 09 |
| Track cascading changes in a complex command | [39_work_context.md](39_work_context.md) | 04, 06, 08, 38 |
| Handle concurrent writes / prevent race conditions | [32_concurrency.md](32_concurrency.md) | 06 |
| Make a command idempotent | [32_concurrency.md](32_concurrency.md) | 06, 16 |
| Add bulk / batch write operations | [39_work_context.md](39_work_context.md) | 06, 11, 13, 32 |

### Auth & permissions

| Task | Start here | Then read |
|---|---|---|
| Add a role or permission | [28_roles_permissions.md](28_roles_permissions.md) | 10, 06 |
| Debug a permission or auth issue | [10_auth.md](10_auth.md) | 28, 04 |
| Add workspace / multi-tenant onboarding | [24_multi_tenancy.md](24_multi_tenancy.md) | 10, 28, 30 |
| Add workspace switching | [24_multi_tenancy.md](24_multi_tenancy.md) | 10 |
| Debug a multi-tenant data leak | [24_multi_tenancy.md](24_multi_tenancy.md) | 07, 18 |

### Reads & performance

| Task | Start here | Then read |
|---|---|---|
| Add pagination to a query | [07_queries.md](07_queries.md) | 22 |
| Add search or filtering to a query | [07_queries.md](07_queries.md) | 22 |
| Fix N+1 queries | [22_performance.md](22_performance.md) | 07, 15 |
| Add query result caching | [07_queries.md](07_queries.md) | 12 |
| Add Redis caching (non-query) | [12_infra_redis.md](12_infra_redis.md) | 22 |
| Add rate limiting to an endpoint | [18_security.md](18_security.md) | 12 |
| Add request timeout enforcement | [02_app_factory.md](02_app_factory.md) | — |
| Add data export (CSV, JSON) | [27_cli_scripts.md](27_cli_scripts.md) | 07, 16 |

### Real-time & events

| Task | Start here | Then read |
|---|---|---|
| Add a real-time socket event (single entity) | [13_sockets.md](13_sockets.md) | 11 |
| Add a batch real-time event (bulk operations) | [11_infra_events.md](11_infra_events.md) | 13 |
| Add in-app notifications + browser push (PWA) | [47_notifications.md](47_notifications.md) | 11, 13, 16 |
| Add presence tracking to an entity (notification exclusion + view history) | [48_presence.md](48_presence.md) | 13, 47, 16 |
| Evolve an event schema | [11_infra_events.md](11_infra_events.md) | 16 |
| Replay failed outbox events | [11_infra_events.md](11_infra_events.md) | 27 |

### Background jobs & scheduling

| Task | Start here | Then read |
|---|---|---|
| Add a background job | [16_background_jobs.md](16_background_jobs.md) | 11, 12 |
| Add a scheduled / recurring job | [37_scheduled_jobs.md](37_scheduled_jobs.md) | 16, 27 |
| Add a CLI backfill command | [27_cli_scripts.md](27_cli_scripts.md) | 30 |

### Integrations

| Task | Start here | Then read |
|---|---|---|
| Add an external integration (SMS, email, payment) | [19_integrations.md](19_integrations.md) | 16, 11, 18 |
| Handle a webhook from an external service | [19_integrations.md](19_integrations.md) | 16, 11, 18 |
| Add file upload / attachment support | [34_file_storage.md](34_file_storage.md) | 03, 06, 27 |
| Add a presigned URL for file download | [34_file_storage.md](34_file_storage.md) | 09 |
| Add image upload to any entity | [43_image.md](43_image.md) | 34, 06, 42 |
| Add image annotations (draw, measure, label) | [43_image.md](43_image.md) | 06, 07 |
| Reorder an image gallery | [43_image.md](43_image.md) | 06 |
| Link the same image to multiple entities | [43_image.md](43_image.md) | 03 |
| Create a case and link it to an entity | [44_case.md](44_case.md) | 06, 03 |
| Add participants to a case | [44_case.md](44_case.md) | 06 |
| Add messaging / conversation to a case | [44_case.md](44_case.md) | 06, 07 |
| Link the same entity to multiple cases (polymorphic) | [44_case.md](44_case.md) | 03 |
| Add rich-text content with typed blocks to any field | [45_content.md](45_content.md) | 06, 03 |
| Add @mention tracking to a content field | [45_content.md](45_content.md) | 44, 06, 11 |
| Query all places an entity is mentioned | [45_content.md](45_content.md) | 07 |

### API & transport

| Task | Start here | Then read |
|---|---|---|
| Version a breaking API change | [20_api_versioning.md](20_api_versioning.md) | 09 |
| Configure rate limiting | [18_security.md](18_security.md) | 09 |
| Add input validation / prevent injection | [18_security.md](18_security.md) | 09, 05 |

### Quality & operations

| Task | Start here | Then read |
|---|---|---|
| Write tests for a command or query | [15_testing.md](15_testing.md) | 06 or 07 |
| Add structured logging | [17_logging.md](17_logging.md) | 05 |
| Standardize runtime observability context | [49_observability_runtime.md](49_observability_runtime.md) | 17, 31, 51 |
| Add deterministic worker debugging flow | [51_worker_runtime.md](51_worker_runtime.md) | 16, 12, 49 |
| Add replay tooling for failed async/runtime flows | [52_replayability.md](52_replayability.md) | 11, 16, 49, 53 |
| Add operational diagnostics CLI commands | [53_operational_cli.md](53_operational_cli.md) | 27, 52, 51 |
| Add deterministic CI runtime validation | [54_ci_cd_runtime.md](54_ci_cd_runtime.md) | 33, 31, 30, 49 |
| Build deterministic testing architecture | [50_testing_strategy.md](50_testing_strategy.md) | 15, 30, 51 |
| Wire health checks and monitoring | [31_health_observability.md](31_health_observability.md) | 17 |
| Deploy to production | [33_deployment.md](33_deployment.md) | 30 |
| Roll back a failed deploy | [33_deployment.md](33_deployment.md) | 30 |

### Compliance & audit

| Task | Start here | Then read |
|---|---|---|
| Add an audit trail to a command | [36_audit_log.md](36_audit_log.md) | 06 |
| Implement GDPR erasure (right to be forgotten) | [35_gdpr_erasure.md](35_gdpr_erasure.md) | 25, 34, 36 |
| Implement GDPR data export (subject access request) | [35_gdpr_erasure.md](35_gdpr_erasure.md) | 07, 27 |

---

## Feature bundles

Pre-computed contract sets for building complete features. Read the entire bundle before starting — each contract informs how the others are implemented. Reading order matters: left to right within each bundle.

### 1. Full CRUD domain with real-time updates
A new entity with create/read/update/delete, domain events, and live socket push. The most common thing built.
```
03 → 08 → 06 → 07 → 09 → 38 → 11 → 13 → 30 → 15
```
Optional: `25` (soft delete), `36` (audit trail), `39` (complex cascading writes)

---

### 2. Multi-tenant SaaS bootstrap
Everything needed to stand up a new multi-tenant application with auth, workspaces, and RBAC.
```
02 → 03 → 10 → 24 → 28 → 30 → 38 → 29
```
Read `29_feature_workflow.md §C` to drive the implementation sequence.

---

### 3. Bulk / batch operations
Commands that process many entities at once — bulk update, bulk delete, batch import.
```
06 → 39 → 32 → 11 → 13 → 38
```
`39` (WorkContext) tracks all affected entities. `32` handles concurrency. `11` + `13` emit one batch socket event instead of N individual events.

---

### 4. Background job pipeline
A new job type that runs off the request path: async processing, retries, scheduled runs.
```
16 → 11 → 12 → 37 → 27
```
`11` is the event that triggers the job. `12` is Redis for the queue. `37` if it runs on a schedule. `27` if it needs a CLI trigger or manual replay.

---

### 5. External service integration
Connecting to a third-party API or processing inbound webhooks.
```
19 → 16 → 11 → 18
```
`19` defines the adapter pattern. `16` runs it async. `11` triggers downstream effects. `18` covers webhook validation and secret handling.

---

### 6. File attachment feature
Upload, store, serve, and clean up files attached to an entity.
```
34 → 03 → 06 → 09 → 27
```
`34` covers the full upload/download/delete lifecycle. `03` for the attachment model. `27` for the orphan cleanup CLI command.

---

### 6b. Polymorphic image gallery
Images attached to multiple entity types (items, cases, messages) from a single image table. Includes upload flow, annotations, display ordering, and async post-processing.
```
43 → 34 → 42 → 06 → 07 → 16
```
`43` defines the four-table pattern and foundation services. `34` covers the presigned URL upload/confirm flow. `42` wires the `ImageEvent` to the worker. `16` handles async post-processing (thumbnails, AI, sync). Add `25` if images need soft delete with deferred storage cleanup.

---

### 7. In-app + browser push notification system with presence exclusion
Server-pushed alerts for user-facing status changes, assignments, and mentions — delivered in-app via WebSocket and to the OS notification tray via VAPID/Web Push (PWA). Excludes users who are already viewing the relevant entity.
```
48 → 47 → 11 → 13 → 16 → 12
```
`48` defines the `EntityType` enum and the two-layer presence system (Redis + `UserAppViewRecord`) that powers `exclude_viewing`. `47` defines the full notification architecture: `Notification` model, `PushSubscription` model, VAPID delivery, task handlers, and the `NotificationType` enum. `11` builds the `UserEvent("notification:new")` signal. `13` delivers it via the user room. `16` runs `CREATE_NOTIFICATIONS` and `SEND_PUSH_NOTIFICATION` as durable tasks. `12` is Redis for the queue. Read `48` then `47` first — they integrate all the others.

---

### 8. Audit-required domain
Domains where every write must leave a tamper-evident trail — finance, compliance, HR.
```
03 → 06 → 36 → 39 → 38
```
`36` defines the audit model and write pattern. `39` ensures all touched entities are captured in complex writes. `38` ensures correct entity resolution across all audit entries.

---

### 9. GDPR-compliant domain
Domains that store PII and must support erasure and subject access requests.
```
03 → 25 → 34 → 35 → 36 → 27
```
`35` defines the erasure workflow and PII inventory. `25` soft-delete before hard delete. `34` for file/attachment erasure. `27` for the erasure CLI command and dry-run mode.

---

### 10. Domain with complex state machine
Entities that move through defined states with guards, transitions, and side effects.
```
08 → 03 → 06 → 38 → 11 → 39
```
`08` is the pure domain logic layer. `06` wraps it in a command with transaction and events. `39` tracks cascading state changes across related entities. `11` emits the state change event.

---

### 11. High-concurrency domain
Entities written to concurrently — bookings, inventory, limited slots, financial ledgers.
```
32 → 06 → 03 → 16
```
`32` defines pessimistic vs optimistic locking and idempotency keys. `06` applies locking in the command. `16` for job deduplication when the same operation is enqueued multiple times.

---

### 12. Scheduled reporting / data export
Regular jobs that aggregate, summarize, or export data on a time-based cadence.
```
37 → 16 → 07 → 27
```
`37` defines the scheduler and job catalog. `07` is the query layer that generates the report. `27` for manual CLI trigger and `--dry-run` mode.

---

### 13. Polymorphic case management
A full case system with entity linking, participants, conversation threads, and rich-content messaging. The standard pattern for logistics, support, and field service applications.
```
44 → 45 → 08 → 06 → 07 → 09 → 11 → 13
```
`44` defines the six-model pattern and foundation services. `45` adds the typed content block schema and mention tracking — read it before implementing `send_message` or `edit_message`. `08` encodes state transition guards. `06` wraps writes in commands. `07` handles conversation and message queries with keyset pagination. `11` + `13` push case and message events to connected clients in real time. Add `43` to attach images to cases or messages; add `42` if state changes need async post-processing via events. Add `47` to notify case participants about new messages and state changes.

---

### 14. Case management with notifications
Case system extended with in-app and PWA push notifications to case participants.
```
44 → 45 → 08 → 06 → 07 → 09 → 11 → 13 → 47 → 16
```
Read bundle 13 first, then add `47` for the notification model and task handlers, and `16` for the durable task queue that delivers them. The `send_message` command queues `CREATE_NOTIFICATIONS` after commit; the task resolves participants and dispatches both user-room socket signals and VAPID browser pushes.

---

### 15. Containerized backend runtime (modular-monolith)
Deterministic local, validation, and CI runtime with explicit health/readiness and worker orchestration.
```
02 → 12 → 16 → 31 → 33
```
`02` anchors app startup and env loading. `12` standardizes Redis runtime behavior. `16` wires worker execution. `31` defines health/readiness/observability checks. `33` formalizes deploy and rollback operating model. Implement with generated `Dockerfile`, `docker-compose.yml`, `.dockerignore`, and `Makefile`.

---

### 16. Observability-ready backend
Runtime-first structured logging and deterministic context propagation for HTTP, worker, and replay flows.
```
17 → 31 → 49 → 51
```
`17` defines baseline logging rules. `31` defines health/readiness expectations. `49` standardizes runtime schema, context IDs, and event naming. `51` ensures worker lifecycle logs align with the same context contract.

---

### 17. Replayable async runtime
Operational replay safety for failed events/jobs/webhooks without full event sourcing.
```
11 → 16 → 52 → 49 → 53
```
`11` and `16` define normal event/job execution boundaries. `52` defines replay-safe scope, metadata, and dry-run semantics. `49` ensures replay observability quality. `53` exposes operational replay commands.

---

### 18. Worker-driven backend
Queue-centric operational model with deterministic retries, dead-letter handling, and diagnostics.
```
16 → 12 → 51 → 49 → 54
```
`16` defines job runtime conventions. `12` anchors Redis queue behavior. `51` formalizes worker lifecycle, retry, and dead-letter rules. `49` defines required worker telemetry. `54` validates worker/runtime behavior in CI.

---

### 19. CI-validated backend runtime
Deterministic container validation, migration checks, readiness gates, and reproducible pipeline signals.
```
33 → 31 → 30 → 54 → 49
```
`33` defines deployment model and rollback logic. `31` provides health/readiness checks. `30` enforces migration safety. `54` formalizes CI runtime validation order. `49` ensures machine-readable diagnostics.

---

### 20. Operationally reproducible backend
Operator and AI-agent friendly runtime operations through explicit CLI governance and replay/debug paths.
```
27 → 53 → 52 → 49 → 50
```
`27` defines script conventions. `53` standardizes operational commands and safety flags. `52` provides replay contract. `49` enforces structured telemetry for diagnostics. `50` ensures deterministic testability of operational behavior.

---

## Non-negotiable rules

These are invariants. They cannot be relaxed. If a task seems to require breaking one, the task is being approached incorrectly.

1. **Routers own zero business logic.** Validate input shape, build `ServiceContext`, call `run_service`, return a response. Nothing else.
2. **Commands own all writes, events, and side effects.** One command = one business intent. No command calls another command directly.
3. **Queries own zero writes.** A function that reads must not mutate.
4. **Domain functions are pure Python.** No ORM, no HTTP, no I/O of any kind.
5. **`ServiceContext` carries identity and incoming data only.** It is not a configuration object. Never add boolean flags to it.
6. **`WorkContext` carries operation-local state for complex commands.** Touched entities, emitted events, and warnings live there — not on `ServiceContext`.
7. **Every public function has a return type annotation.** No exceptions.
8. **`DomainError` and its subclasses are the only errors that cross layer boundaries.** Never let `SQLAlchemyError`, `KeyError`, or `AttributeError` bubble to the router.
9. **Never alter the database manually.** All schema changes go through Alembic migrations.
10. **Public APIs and database relations identify resources by `client_id`.** Use the identity resolver (38) for workspace and soft-delete-safe lookup.
11. **Documentation is updated in the same PR as the code.** An endpoint without a shape in `docs/domains/<domain>/api.md` is an incomplete PR. An outdated doc is a bug.
12. **Events use `<domain>:<verb>` naming with a colon separator.** This must match the frontend's `ServerToClientEvents` type exactly — the event type string is the socket event name.

---

## Full contract directory

### Core architecture
| # | File | Covers |
|---|---|---|
| 01 | [01_architecture.md](01_architecture.md) | Layer map, folder structure, dependency rules |
| 02 | [02_app_factory.md](02_app_factory.md) | App factory, config, env loading, middleware |
| 03 | [03_models.md](03_models.md) | ORM model contract, column types, table rules, migration safety |
| 04 | [04_context.md](04_context.md) | `ServiceContext` — what it is, what it must not carry |
| 05 | [05_errors.md](05_errors.md) | Error hierarchy, codes, HTTP status mapping |

### Service layer
| # | File | Covers |
|---|---|---|
| 06 | [06_commands.md](06_commands.md) | Write operations — structure, transaction boundaries, event emission |
| 07 | [07_queries.md](07_queries.md) | Read operations — structure, pagination, serialization, query result caching |
| 08 | [08_domain.md](08_domain.md) | Pure domain logic — guards, state machines, calculations |
| 38 | [38_identity_resolution.md](38_identity_resolution.md) | `client_id` lookup with workspace/soft-delete-safe resolution |
| 39 | [39_work_context.md](39_work_context.md) | `WorkContext` for complex writes — touched entities, cascading changes, response assembly |

### Transport layer
| # | File | Covers |
|---|---|---|
| 09 | [09_routers.md](09_routers.md) | Router layer — what routers do and do not own |
| 10 | [10_auth.md](10_auth.md) | JWT, RBAC decorators, app-scope guards |
| 20 | [20_api_versioning.md](20_api_versioning.md) | When and how to version, backwards compatibility, sunset policy |

### Infrastructure
| # | File | Covers |
|---|---|---|
| 11 | [11_infra_events.md](11_infra_events.md) | Event bus, outbox pattern, batch events, schema versioning, dead letters |
| 12 | [12_infra_redis.md](12_infra_redis.md) | Redis — connection, key conventions, TTL rules |
| 13 | [13_sockets.md](13_sockets.md) | Socket.IO — single, batch, and broadcast real-time push |
| 16 | [16_background_jobs.md](16_background_jobs.md) | Async task workers, queues, retry + jitter, timeouts, observability, stale recovery, NOTIFY opt-in |
| 19 | [19_integrations.md](19_integrations.md) | Adapter pattern, credentials, webhooks, graceful degradation |

### Auth & multi-tenancy
| # | File | Covers |
|---|---|---|
| 24 | [24_multi_tenancy.md](24_multi_tenancy.md) | Workspace architecture, membership join, JWT session, workspace switching |
| 25 | [25_soft_delete.md](25_soft_delete.md) | `is_deleted`/`deleted_at`, query filtering, cascade strategy, restore pattern |
| 28 | [28_roles_permissions.md](28_roles_permissions.md) | RBAC — two-layer model, Permission enum, `require_permission`, custom roles |
| 32 | [32_concurrency.md](32_concurrency.md) | Pessimistic/optimistic locking, idempotency keys, job deduplication |

### Quality & security
| # | File | Covers |
|---|---|---|
| 15 | [15_testing.md](15_testing.md) | Test pyramid, fixtures, unit vs integration, what must be tested |
| 17 | [17_logging.md](17_logging.md) | Log levels, structured fields, security events, what not to log |
| 18 | [18_security.md](18_security.md) | Input validation, CORS, secrets, rate limiting, IDOR prevention |
| 22 | [22_performance.md](22_performance.md) | N+1 prevention, query limits, pagination, caching, connection pool |

### Operations
| # | File | Covers |
|---|---|---|
| 26 | [26_dependency_management.md](26_dependency_management.md) | Package evaluation, pinning, CVE cadence, approved list |
| 27 | [27_cli_scripts.md](27_cli_scripts.md) | Typer CLI scripts, backfill structure, `--dry-run`, seed and report commands |
| 29 | [29_feature_workflow.md](29_feature_workflow.md) | Step-by-step playbook — new domain, new endpoint, new application; definition of done |
| 30 | [30_migrations.md](30_migrations.md) | Alembic — zero-downtime patterns, NOT NULL sequence, concurrent indexes, enum types |
| 33 | [33_deployment.md](33_deployment.md) | Pre-deploy checklist, rollback procedure, feature flags, smoke tests |
| 34 | [34_file_storage.md](34_file_storage.md) | Presigned URL upload/download, multipart upload, MIME validation, orphan cleanup, storage adapter |
| 37 | [37_scheduled_jobs.md](37_scheduled_jobs.md) | Time-based scheduled jobs — scheduler setup, batching, idempotency, job catalog |

### Operational runtime contracts
| # | File | Covers |
|---|---|---|
| 49 | [49_observability_runtime.md](49_observability_runtime.md) | Runtime observability schema, correlation propagation, worker/request/replay telemetry |
| 50 | [50_testing_strategy.md](50_testing_strategy.md) | Deterministic testing architecture, async isolation, DB/Redis test boundaries |
| 51 | [51_worker_runtime.md](51_worker_runtime.md) | Worker lifecycle, queue registry rules, retries, dead-letter behavior, idempotency |
| 52 | [52_replayability.md](52_replayability.md) | Replay-safe operational scope, replay metadata, dry-run, auditability |
| 53 | [53_operational_cli.md](53_operational_cli.md) | Typer operational commands, destructive safeguards, reproducible diagnostics |
| 54 | [54_ci_cd_runtime.md](54_ci_cd_runtime.md) | CI runtime validation flow, dynamic ports, migration/readiness/dependency checks |

### Foundation
| # | File | Covers |
|---|---|---|
| 40 | [40_identity.md](40_identity.md) | `IdentityMixin`, `generate_id()` with ULID, type prefix registry |
| 41 | [41_user.md](41_user.md) | `User` model, `HistoryRecord` mixin, `UserAppViewRecord`, `UserHistoryRecord` |
| 42 | [42_event.md](42_event.md) | `Event` mixin — operation lifecycle tracking, `EventStateEnum`, worker flow |
| 43 | [43_image.md](43_image.md) | Polymorphic image pattern — `Image`, `ImageLink`, `ImageAnnotation`, `ImageEvent`, foundation services |
| 44 | [44_case.md](44_case.md) | Polymorphic case pattern — `Case`, `CaseType`, `CaseLink`, `CaseParticipant`, `CaseConversation`, `CaseConversationMessage`, foundation services |
| 45 | [45_content.md](45_content.md) | Input content block schema — typed blocks (TEXT, MENTION, LABEL, LINK), `ContentMention`, `ContentMentionLink`, `process_content_mentions` utility |
| 46 | [46_serialization.md](46_serialization.md) | Serialization standard — services return typed dataclass instances, domain serializer modules own named views (`compact`, `full`, `flat`), routers call serializers explicitly |
| 47 | [47_notifications.md](47_notifications.md) | Notification system — `Notification` + `PushSubscription` models, VAPID/Web Push delivery, `CREATE_NOTIFICATIONS` + `SEND_PUSH_NOTIFICATION` task handlers, `NotificationType` enum |
| 48 | [48_presence.md](48_presence.md) | Presence & view activity — `EntityType` enum, Redis two-layer model, `UserAppViewRecord` via background tasks, `RECORD_VIEW_START` / `RECORD_VIEW_END` handlers |

### Reference
| # | File | Covers |
|---|---|---|
| 21 | [21_naming_conventions.md](21_naming_conventions.md) | Files, functions, classes, DB, routes, Redis keys, env vars |
| 23 | [23_documentation.md](23_documentation.md) | `docs/` folder structure, living doc templates, maintenance discipline, ADRs |

### Observability & compliance
| # | File | Covers |
|---|---|---|
| 31 | [31_health_observability.md](31_health_observability.md) | `/health`, `/ready`, `/live` endpoints, metrics, alerting rules |
| 35 | [35_gdpr_erasure.md](35_gdpr_erasure.md) | Right to erasure — PII inventory, hard delete vs anonymize, retention holds |
| 36 | [36_audit_log.md](36_audit_log.md) | Tamper-evident audit trail — model, write pattern, event naming, retention policy |

### Recommended read tracks for runtime governance

Use these short tracks when the task is operational rather than feature-oriented.

- Structured runtime logging: 17 -> 31 -> 49 -> 51
- Deterministic testing and validation: 15 -> 50 -> 30 -> 54
- Worker debugging and retry governance: 16 -> 12 -> 51 -> 49 -> 53
- Replay investigation and recovery: 11 -> 16 -> 52 -> 49 -> 53
- CI runtime reproducibility: 33 -> 31 -> 30 -> 54 -> 49

---

## Folder structure

Canonical layout for any application built with this contract set. Each directory is annotated with the contract that governs it.

```
my_app/
├── __init__.py                    # create_app() factory → 02
├── config/                        # → 02
│   ├── default.py
│   ├── development.py
│   ├── testing.py
│   └── production.py
├── models/                        # → 03, 25 (soft delete), 30 (migrations)
│   ├── __init__.py                # db instance + all table imports
│   ├── base/
│   │   ├── identity.py            # IdentityMixin (client_id primary key) → 40
│   │   ├── history_record.py      # HistoryRecord mixin → 41
│   │   └── event.py               # Event mixin → 42
│   └── tables/
│       ├── users/                 # → 41
│       │   ├── user.py
│       │   ├── user_app_view_record.py
│       │   └── user_history_record.py
│       ├── images/                # → 43
│       │   ├── image.py
│       │   ├── image_link.py
│       │   ├── image_annotation.py
│       │   └── image_event.py
│       ├── cases/                 # → 44
│       │   ├── case_type.py
│       │   ├── case.py
│       │   ├── case_link.py
│       │   ├── case_participant.py
│       │   ├── case_conversation.py
│       │   └── case_conversation_message.py
│       ├── content/               # → 45
│       │   ├── content_mention.py
│       │   └── content_mention_link.py
│       ├── notifications/         # → 47
│       │   ├── notification.py
│       │   └── push_subscription.py
│       └── <domain>/
├── domain/                        # → 08 — pure Python, no I/O
│   └── <domain>/
├── services/
│   ├── context.py                 # ServiceContext → 04
│   ├── work_context.py            # WorkContext → 39
│   ├── outcome.py                 # StatusOutcome
│   ├── run_service.py             # run_service()
│   ├── commands/                  # → 06
│   │   └── <domain>/
│   ├── queries/                   # → 07
│   │   └── <domain>/
│   └── infra/
│       ├── identity.py            # generate_id(prefix) → 40
│       ├── events/                # → 11 (bus, builders, emitters, handlers, registry)
│       │   ├── builders/
│       │   ├── emitters/
│       │   ├── handlers/
│       │   └── registry/
│       ├── presence/              # → 47 (cross-process viewer tracking for notification exclusion)
│       │   └── presence.py
│       └── redis/                 # → 12
├── routers/                       # → 09
│   ├── http/
│   │   └── response.py
│   ├── utils/
│   │   ├── jwt_handler.py         # → 10
│   │   └── role_decorator.py      # → 10, 28
│   └── api_v1/
│       └── <domain>.py
├── errors/                        # → 05
│   ├── base.py
│   ├── not_found.py
│   ├── permissions.py
│   └── validation.py
└── sockets/                       # → 13
    └── register.py
```

---

## Scope boundary

This contract covers the **backend application layer**: FastAPI services, database, auth, infrastructure, and operations.

It does not cover:
- **AI agents, MCP servers, LLM orchestration** → see [`AI_Architecture/`](../AI_Architecture/README.md)
- **React frontend, TanStack Query, Zustand, forms, routing** → see [`Frontend_architecture/`](../Frontend_architecture/README.md)

The seam between the backend and AI contract sets is the service layer. Agent tool functions call existing commands and queries — the backend has no knowledge of which triggered the call. A command receives a `ServiceContext` whether it came from an HTTP request or an agent tool call.
