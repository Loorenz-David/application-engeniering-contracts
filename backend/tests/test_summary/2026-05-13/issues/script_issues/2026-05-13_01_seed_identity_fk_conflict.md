# Issue: Seed Identity fails with role FK conflict

Date: 2026-05-13
Test Run Folder: 2026-05-13
Test File: tests/01_seed_identity.sh
Step: Step 2 — Seed identity records (idempotent)

## Symptom
Seed test aborts during role upsert.

Observed error:
- sqlalchemy.exc.IntegrityError
- asyncpg.exceptions.ForeignKeyViolationError
- update or delete on table roles violates foreign key constraint workspace_roles_role_id_fkey on table workspace_roles
- DETAIL: Key (client_id)=(role_test_admin) is still referenced from table workspace_roles

## Reproduction
From backend/app:
- APP_URL=http://localhost:8000 bash ../../tests/01_seed_identity.sh

## Root Cause Hypothesis
The role seed statement updates role client_id on name conflict:
- INSERT INTO roles (client_id, name) ... ON CONFLICT (name) DO UPDATE SET client_id = EXCLUDED.client_id

If an ADMIN role already exists and is referenced by workspace_roles, changing role identity keys causes FK breakage.

## Proposed Fix
- Do not mutate role client_id on conflict by name.
- Reuse existing role rows and connect workspace_roles to resolved role id.
- Keep idempotency by upserting child/link rows only.

## Fixed in bootstrap
- Current app build: yes
- Canonical bootstrap source: pending (handled separately)
- File updated (current app build): run_test/bootstrap_test_full_build/tests/01_seed_identity.sh

## Status
- Reproduced: yes
- Root cause confirmed: yes
- Fix applied in test backend: yes
- Fix applied in bootstrap contracts: no
- Re-run passed: yes (APP_URL=http://localhost:8001)
