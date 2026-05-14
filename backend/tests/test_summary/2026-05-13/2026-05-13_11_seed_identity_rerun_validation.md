# 2026-05-13 — Seed Identity Re-run Validation

## Scope
- Validate first test execution after bootstrap/test harness updates.
- Command executed from backend app root:
  - APP_URL=http://localhost:8000 bash ../../tests/01_seed_identity.sh

## Result
- Status: FAIL
- Failing step: Step 2 — Seed identity records (idempotent)
- Exit code: 1

## Evidence
- sqlalchemy.exc.IntegrityError
- asyncpg.exceptions.ForeignKeyViolationError
- update or delete on table roles violates foreign key constraint workspace_roles_role_id_fkey on table workspace_roles
- DETAIL: Key (client_id)=(role_test_admin) is still referenced from table workspace_roles

## Root Cause (current)
- The seed script attempts to rewrite an existing role client_id via name-conflict upsert:
  - INSERT INTO roles (client_id, name) ... ON CONFLICT (name) DO UPDATE SET client_id = EXCLUDED.client_id
- Existing rows reference the current role id through workspace_roles, so mutating role identity violates foreign key constraints.

## Impact
- First test gate (01_seed_identity.sh) is blocked.
- Downstream tests (02-10) should not be executed until seed is fixed.

## Next Action
- Update role/workspace seeding strategy to avoid changing existing identity keys under FK relationships.
- Candidate strategy:
  - Resolve existing role/workspace by unique natural fields (name), reuse existing ids, and only upsert linking rows.
  - Avoid updating role client_id on name conflict.
