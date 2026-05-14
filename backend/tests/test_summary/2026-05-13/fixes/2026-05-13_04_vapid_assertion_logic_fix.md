# Fix: VAPID assertion logic in test script

Date: 2026-05-13
Issue Ref: issues/script_issues/2026-05-13_04_vapid_assertion_logic.md
Test File: tests/04_vapid.sh

## Root Cause
The script used arithmetic increment in helper functions:
- `((PASSED++))` / `((FAILED++))`

When used in `cond && pass || fail` style checks, arithmetic return semantics could produce false-fail behavior even when condition succeeded.

## Change Applied (current app build)
- Updated helper functions in `tests/04_vapid.sh`:
  - `pass() { ... PASSED=$((PASSED + 1)); return 0; }`
  - `fail() { ... FAILED=$((FAILED + 1)); return 0; }`

## Validation
- APP_URL=http://localhost:8000 bash ../../tests/04_vapid.sh
- Result: TEST 03 RESULT: 6 Passed, 0 Failed

## Handoff
- Current app build fixed: yes
- Main bootstrap/canonical fix: pending (Claude)
