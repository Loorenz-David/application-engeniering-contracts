# Fix: Seed Identity FK/Schema Compatibility

Date: 2026-05-13
Issue Ref: issues/2026-05-13_01_seed_identity_fk_conflict.md
Test File: tests/01_seed_identity.sh
Step Fixed: Step 2 seed upserts and schema-aligned inserts

## Summary
Adjusted the current app-build seed script to be FK-safe and schema-compatible with the generated runtime models.

## Root Cause
The seed script was using assumptions from an older schema:
- attempted identity-key mutation on role/workspace name conflicts
- used id-based joins where schema uses client_id FKs
- inserted into non-existent columns (password_hash)
- omitted required non-null fields that rely on ORM defaults in app code

## Files Changed
- run_test/bootstrap_test_full_build/tests/01_seed_identity.sh

## What Changed
- Replaced conflict-prone role/workspace upserts with guarded insert-if-absent logic.
- Updated all FK joins/subqueries to use client_id columns.
- Updated user insert to use password column and include required fields.
- Added required non-null fields for raw SQL inserts:
  - workspaces: time_zone, created_at
  - workspace_roles: is_system
  - workspace_memberships: is_active, joined_at
  - users: created_at, online
- Kept APP_URL override support for non-8000 local runtime.

## Validation
Executed from backend/app:
- APP_URL=http://localhost:8001 bash ../../tests/01_seed_identity.sh

Result:
- TEST 00 RESULT: PASS — Identity seeded and token saved
- Identity verify line: user_test | workspace_test | ADMIN | true

## Bootstrap Handoff
- Current app build fixed: yes
- Canonical/original bootstrap source: pending (handled by Claude per handoff)
