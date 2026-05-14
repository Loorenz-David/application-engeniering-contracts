# Issue: VAPID test contains assertion logic anomaly

Date: 2026-05-13
Test File: tests/04_vapid.sh
Run Command: APP_URL=http://localhost:8000 bash ../../tests/04_vapid.sh

## Symptom
- Test result: 6 Passed, 1 Failed.
- Output pattern includes contradictory status handling where HTTP 200 can still be reported as a failed expectation line.

## Root Cause Hypothesis
- Script pass/fail conditional or output handling has a logic bug, not endpoint functionality.

## Functional State
- Endpoint is public and returned HTTP 200.
- Payload shape is present.
- Null `public_key` is acceptable when VAPID keys are unset.

## Status
- Reproduced: yes
- Local fix applied: yes
- Canonical/bootstrap fix: pending
- Re-run result: TEST 03 RESULT: 6 Passed, 0 Failed
