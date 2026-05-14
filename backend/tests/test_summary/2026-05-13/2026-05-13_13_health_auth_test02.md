# 2026-05-13 — Test 02 Health + Auth

## Command
- APP_URL=http://localhost:8000 bash ../../tests/02_health_auth.sh

## Result
- Status: FAIL
- Summary: 3 Passed, 7 Failed

## Key Findings
- Sign-in endpoint failed with HTTP 500.
- Protected endpoints then returned 403 due to missing/invalid token context.
- Script assertion anomaly observed: output includes a contradictory line like `HTTP 200 (expected 200)` marked as failure despite status 200.

## Impact
- Auth gate is unstable for downstream protected tests.
- Token refresh/save path in this run is not reliable.

## Next Action
- Investigate sign-in 500 in runtime auth service route and DB user compatibility.
- Fix assertion condition in test script where 200 can still be reported as failed.
