# Issue: Notifications test fails due to auth 403 chain

Date: 2026-05-13
Test File: tests/03_notifications.sh
Run Command: APP_URL=http://localhost:8000 bash ../../tests/03_notifications.sh

## Symptom
- Test result: 3 Passed, 18 Failed.
- Majority of endpoints returned HTTP 403.

## Root Cause Hypothesis
- Token flow is invalid because sign-in path in prior auth test returned HTTP 500.
- Notifications suite depends on .test_token_bootstrap and valid bearer permissions.

## Status
- Reproduced: yes
- Local fix applied: yes
- Canonical/bootstrap fix: pending
- Re-run result: TEST 02 RESULT: 21 Passed, 0 Failed
