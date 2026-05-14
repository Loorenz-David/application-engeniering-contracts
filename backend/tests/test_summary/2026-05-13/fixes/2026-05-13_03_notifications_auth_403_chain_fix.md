# Fix: Notifications test contract and seed alignment

Date: 2026-05-13
Issue Ref: issues/2026-05-13_03_notifications_auth_403_chain.md
Test File: tests/03_notifications.sh

## Root Cause
- Initial 403 chain was tied to missing protected-route permissions.
- Notifications test had additional contract mismatches:
  - wrong push route (`/push-subscriptions` vs `/push-subscription`)
  - wrong push payload shape (`keys` object vs flat `p256dh`/`auth`)
  - wrong mark-read key (`notification_ids` vs `notification_client_ids`)
  - SQL seed used non-existent notifications columns and wrong insert helper for DML.

## Changes Applied (current app build)
- tests/03_notifications.sh
  - Added stable pass/fail counters.
  - Fixed notification seed SQL schema fields.
  - Added `run_sql` helper for non-select inserts.
  - Fixed mark-read payload key.
  - Fixed push subscription routes and payload format.
  - Kept APP_URL support for port flexibility.

## Validation
- APP_URL=http://localhost:8000 bash ../../tests/03_notifications.sh
- Result: TEST 02 RESULT: 21 Passed, 0 Failed

## Handoff
- Current app build fixed: yes
- Canonical bootstrap/source fix: pending (handled separately)
