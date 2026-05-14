# 2026-05-13 — DB Wipe + Re-run (Seed, Health/Auth, Notifications)

## Scope
- Wipe DB data and rerun foundational tests.
- Validate user creation/sign-in chain and notification flows on current app build.

## Environment
- App URL: http://localhost:8000
- Services reset via Docker volume wipe + migrations + triggers before rerun.

## Commands
- APP_URL=http://localhost:8000 bash ../../tests/01_seed_identity.sh
- APP_URL=http://localhost:8000 bash ../../tests/02_health_auth.sh
- APP_URL=http://localhost:8000 bash ../../tests/03_notifications.sh

## Results
- Test 01 (seed identity): PASS
- Test 02 (health/auth): PASS — 9 Passed, 0 Failed
- Test 03 (notifications): PASS — 21 Passed, 0 Failed

## Fixes validated in current app build
- Permission middleware/admin permission fallback now allows protected endpoint access for admin test sessions.
- Health test pass/fail counter logic fixed to avoid false fail when status is 200.
- Notifications test aligned to actual API contract:
  - push-subscription route names and payload shape
  - mark-read payload key (`notification_client_ids`)
  - SQL seed aligned to notifications schema
  - insert helper corrected for non-select SQL execution

## Notes
- Canonical bootstrap/source updates are tracked separately per handoff.
