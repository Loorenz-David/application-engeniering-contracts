# 29 — Feature Implementation Workflow

## Purpose

This document is the sequential playbook for implementing any new feature end-to-end. It tells you what to read, what to create, in what order, and how to verify the work is complete before calling it done.

Follow the steps in order. Skipping steps is the primary cause of layer violations, missing tests, and undocumented APIs.

---

## Before you write a single line

Read these contracts in full before touching the codebase:

1. [01_architecture.md](01_architecture.md) — understand the layer map
2. [21_naming_conventions.md](21_naming_conventions.md) — learn the file naming rules
3. The contract specific to what you are building (see the navigation matrix in `README.md`)

Then read the existing code for the closest domain to what you are building. Understanding a working example is faster than reading all 30 contracts cold.

---

## Workflow A — New domain (model + full CRUD)

Use this workflow when you are adding a brand new entity that does not yet exist in the application.

---

### Step 1 — Write the model

**File:** `models/tables/<domain>/<entity>.py`

Requirements:
- `IdentityMixin` with `client_id` (String(64), primary key) — the DB and API identifier
- `workspace_id` (String(64), FK to `workspaces.client_id`, non-nullable, indexed)
- `is_deleted` + `deleted_at` — required on all user-facing entities
- `created_at` + `updated_at` — required on all tables
- No business logic in the model. No computed properties that touch the DB.

Register the model in `models/__init__.py` so Alembic detects it.

**Reference:** [03_models.md](03_models.md), [38_identity_resolution.md](38_identity_resolution.md)

---

### Step 2 — Write the domain layer

**File:** `domain/<domain>/guards.py` and/or `domain/<domain>/state_machine.py`

Write pure Python functions that enforce business rules:
- `can_<entity>_be_<action>(entity) -> bool`
- State transition guard: `assert_<entity>_transition(from_state, to_state) -> None` (raises `DomainError` if invalid)
- Any calculation that belongs to the business, not the persistence layer

No ORM imports. No HTTP imports. No `db.session`.

**Reference:** [08_domain.md](08_domain.md)

---

### Step 3 — Write the migration

```bash
# 1. Verify the chain is clean
alembic current
alembic history

# 2. Generate
alembic revision --autogenerate -m "create_<entity>_table"

# 3. Open the generated file and verify the checklist:
#    - Correct table name
#    - nullable/not-null set correctly
#    - Index on workspace_id (mandatory)
#    - client_id is the primary key via IdentityMixin
#    - downgrade() drops the table

# 4. Apply
alembic upgrade head
```

**Reference:** [30_migrations.md](30_migrations.md)

---

### Step 4 — Write the commands

One file per business operation. Minimum for a new entity:

| File | Responsibility |
|---|---|
| `services/commands/<domain>/create_<entity>.py` | Creates the entity |
| `services/commands/<domain>/update_<entity>.py` | Updates mutable fields |
| `services/commands/<domain>/delete_<entity>.py` | Soft-deletes the entity |

Each command:
1. Calls `parse_<operation>_<entity>_request(ctx.incoming_data)` — Pydantic validation
2. Calls `ctx.require_permission(Permission.<RELEVANT>)` — authorization
3. Resolves existing entities through the domain resolver when needed
4. Creates a `WorkContext` when the command has cascading or batch changes
5. Opens a `db.session.begin()` transaction
6. Calls domain guard before mutating state
7. Tracks affected entities/events in `WorkContext` when used
8. Emits events via the event bus after the commit
9. Returns a serialized `dict` including authoritative related changes when needed

**Reference:** [06_commands.md](06_commands.md), [38_identity_resolution.md](38_identity_resolution.md), [39_work_context.md](39_work_context.md)

---

### Step 5 — Write the queries

| File | Responsibility |
|---|---|
| `services/queries/<domain>/get_<entity>.py` | Fetch single entity by `client_id` |
| `services/queries/<domain>/list_<entity>s.py` | Paginated list with filters |

Each query:
1. Always filters `<Entity>.workspace_id == ctx.workspace_id` first
2. Always filters `<Entity>.is_deleted == False`
3. Uses the domain resolver for single-entity `client_id` lookups
4. Uses cursor-based pagination for list endpoints
5. Returns a plain `dict` — no ORM instances

**Reference:** [07_queries.md](07_queries.md)

---

### Step 6 — Write the router

**File:** `routers/api_v1/<domain>.py`

The router is thin. It does exactly this and nothing else:

```python
@router.post("/", status_code=201)
async def create_entity(
    body: CreateEntityRequest,
    ctx: ServiceContext = Depends(build_context),
) -> dict:
    return await run_service(create_entity_command, ctx)
```

Include the router in `routers/api_v1/__init__.py` via `app.include_router(...)`.

**Reference:** [09_routers.md](09_routers.md)

---

### Step 7 — Write the tests

Minimum test coverage for a new domain:

| Test file | What it tests |
|---|---|
| `tests/unit/domain/test_<entity>_guards.py` | Domain guards in isolation — no DB |
| `tests/integration/commands/test_create_<entity>.py` | Command with real DB transaction |
| `tests/integration/commands/test_delete_<entity>.py` | Soft delete, permission check, domain guard |
| `tests/integration/queries/test_list_<entity>s.py` | Pagination, workspace isolation |
| `tests/integration/routers/test_<domain>_router.py` | Auth required, role required, 404 shape |

Every integration test must assert workspace isolation — a resource belonging to workspace A must be invisible to a user from workspace B.

**Reference:** [15_testing.md](15_testing.md)

---

### Step 8 — Write the documentation

**File:** `docs/domains/<domain>/api.md`

Minimum entries:
- Endpoint shape for each route (method, URL, request body, response body)
- State machine diagram (if entity has states)
- Event catalog entries for any events emitted

**Reference:** [23_documentation.md](23_documentation.md)

---

### Step 9 — Verify before marking complete

Run this checklist:

- [ ] Model registered in `models/__init__.py`
- [ ] Migration auto-generated and reviewed (not hand-written)
- [ ] Migration applied (`alembic upgrade head`) without errors
- [ ] All commands call `require_permission` before any writes
- [ ] All queries filter by `workspace_id` as the first condition
- [ ] All queries filter by `is_deleted == False`
- [ ] Router calls no business logic directly
- [ ] Router included via `include_router` in `routers/api_v1/__init__.py`
- [ ] Unit tests pass (`pytest tests/unit/`)
- [ ] Integration tests pass (`pytest tests/integration/`)
- [ ] No orphaned imports
- [ ] `docs/domains/<domain>/api.md` written or updated
- [ ] Event catalog updated if new events were emitted

---

## Workflow B — New endpoint on an existing domain

Use this when the entity already exists and you are adding a new operation (e.g., a new action, a new filter, a new state transition).

---

### Step 1 — Read the existing domain first

Before writing anything, read:
- The existing model file for the entity
- At least one existing command and one existing query in the domain
- The existing router file for the domain

Match the patterns you see. Do not introduce a new pattern unless the existing one is genuinely insufficient.

---

### Step 2 — Determine what layer owns the new code

| What you are adding | Layer |
|---|---|
| A new state transition with business rules | Domain guard first, then command |
| A new write operation | Command |
| A new read with a new filter | Query |
| A new URL for an existing operation | Router only |
| A new permission check | `28_roles_permissions.md` → Permission enum → command |

---

### Step 3 — Write the command or query

Follow the same rules as Workflow A, Step 4 or Step 5.

If the new operation changes the DB schema (new column, new index), go back to Workflow A Step 3 and write the migration first.

---

### Step 4 — Add the route

Add a new `@entity_bp.route(...)` to the existing router file. The blueprint is already registered — do not re-register it.

---

### Step 5 — Write the tests

Add tests to the existing test files where possible. Only create a new test file if there is no sensible place to put the tests.

At minimum:
- One happy-path integration test for the new operation
- One test verifying that the wrong role is rejected
- One test verifying that a resource from another workspace is not accessible

---

### Step 6 — Update the documentation

Add the new endpoint shape to `docs/domains/<domain>/api.md`. If the operation emits a new event, add it to the event catalog.

---

## Workflow C — Bootstrap a new application

Use this workflow when you are starting a new FastAPI application from scratch using this contract as its foundation.

---

### Step 1 — Scaffold the folder structure

Create the folder structure from the quick-start section of the README. The minimum viable structure:

```
my_app/
├── __init__.py           # create_app()
├── config/
├── models/
├── domain/
├── services/
│   ├── context.py
│   ├── outcome.py
│   ├── run_service.py
│   ├── commands/
│   └── queries/
├── routers/
│   ├── http/response.py
│   └── api_v1/
├── errors/
└── migrations/
```

---

### Step 2 — Wire the app factory

Implement `create_app()` following [02_app_factory.md](02_app_factory.md):
- Load config from `.env` via `pydantic-settings`
- Init DB engine, Redis, and Socket.IO inside the `lifespan` context manager
- Register routers via `include_router`
- Add CORS middleware

---

### Step 3 — Implement multi-tenancy foundation

Before writing any domain, implement the 5-table workspace model:

1. `users` table
2. `roles` table + seed data (`role_admin`, `role_member`, `role_field`)
3. `workspaces` table
4. `workspace_roles` table
5. `workspace_memberships` table

Write the migration that creates all five tables and seeds `roles` with `ON CONFLICT DO NOTHING`. Do not add `role_id` or `workspace_id` to `users`; role assignment lives on `workspace_memberships`.

**Reference:** [24_multi_tenancy.md](24_multi_tenancy.md)

---

### Step 4 — Implement auth

1. JWT login/logout/refresh endpoints
2. `@jwt_required()` decorator
3. `@role_required([...])` decorator
4. Redis token blocklist for logout
5. User registration + workspace creation flow

**Reference:** [10_auth.md](10_auth.md)

---

### Step 5 — Implement Permission enum

Define the `Permission` enum for your application's first domain. Start minimal — only the permissions you need now. Adding permissions is cheap; removing them requires a JWT re-issue.

**Reference:** [28_roles_permissions.md](28_roles_permissions.md)

---

### Step 6 — First domain

Apply Workflow A for the first entity. The workspace foundation from Step 3 must exist before any domain entity, because all domain entities carry `workspace_id`.

---

## Common mistakes and how to avoid them

| Mistake | How it manifests | The fix |
|---|---|---|
| Business logic in the router | The router file grows beyond 30 lines | Move the logic to a command or query |
| Skipping `require_permission` | Any authenticated user can mutate any resource | Always call it at the top of write commands |
| Missing `workspace_id` filter | A user from workspace A sees workspace B's data | Make it the first filter in every query |
| Missing `is_deleted == False` filter | Deleted records reappear | Add the filter to the base query function |
| Writing migration SQL by hand | Type mismatches, missing indexes, broken downgrade | Always auto-generate with `alembic revision --autogenerate -m "..."` |
| Calling a command from another command | Nested transactions, tangled event emission | Use a domain function for shared logic instead |
| Returning ORM instances from queries | Lazy-loading N+1 queries outside the session | Always serialize to `dict` before returning |
| Event emitted inside the transaction | Event fires even if the commit rolls back | Emit after `db.session.commit()` — in the same `begin()` block via an outbox or after the block |

---

## Definition of done

A feature is complete when all of the following are true:

1. The migration was auto-generated, reviewed, and applied without errors
2. All commands call `require_permission` before any DB write
3. All queries filter by `workspace_id` as the mandatory first condition
4. Unit tests cover domain guards in isolation
5. Integration tests cover the happy path and the workspace isolation case
6. The router file contains no business logic
7. `docs/domains/<domain>/api.md` reflects the current API shape
8. No Python type-hint violations on public function signatures
9. No orphaned imports in any modified file
