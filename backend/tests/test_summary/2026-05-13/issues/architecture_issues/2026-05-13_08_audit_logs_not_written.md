# Architecture Issue: Audit Logs Not Written After case:state-changed

Date: 2026-05-13
Test: 08_audit_logs.sh (internally labeled TEST 07)
Status: OPEN (architecture-level)
Severity: High

## Observed Failure
- Test summary: 6 Passed, 1 Failed
- Failed assertion: No audit records written after state change (total=0)

## Repro Summary
1. Create case via API succeeds.
2. State change via API succeeds using payload:
   - case_client_id
   - new_state=resolving
3. Query audit_logs count before/after event.
4. Count does not increase.

## Why This Is Architectural
- Script now uses correct state-change payload contract.
- Event trigger path is executed via API as required.
- Missing persistence indicates audit pipeline wiring/configuration issue.

## Likely Root Causes
- case:state-changed missing from audit allowlist/registry.
- Audit handler not registered on startup in event bus.
- Event dispatch path does not reach audit handler in runtime bootstrap.
- Handler receives event but fails silently before DB insert.

## Suggested Fix Targets
- backend/app/my_app/services/infra/events/audit_handler.py
- backend/app/my_app/services/infra/events/registry.py (or equivalent)
- backend/app startup event bus registration path
- case state transition command dispatch path

## Acceptance Criteria
- Running 08_audit_logs.sh produces at least one new row in audit_logs after state change.
- Test 08 final summary becomes 7 Passed, 0 Failed.
