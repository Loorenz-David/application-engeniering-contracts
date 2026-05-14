# Audit Log Bootstrap Plan
Date: 2026-05-13
Session: 08
Scope: What bootstrap must include so audit logging is enabled, reliable, and expandable.

---

## Execution Update (2026-05-13)

**Status:** ✅ Executed and validated in local test backend

Final Session 08 validation outcome:
1. `audit_logs` table presence verified via app DB session.
2. Audited event path verified by triggering `case:state-changed`.
3. Audit write path verified (new rows inserted in `audit_logs`).
4. Audit query path verified (rows readable after write).

**Final test result:** 6 passed, 0 failed.

**Implementation note:** initial false negatives were caused by test harness assumptions (direct `psql` auth path and non-audited event triggers), not by missing audit runtime wiring in the generated app.

---

## Objective

Create a bootstrap baseline where audit logs are:
1. Enabled by configuration, not hard-disabled by default.
2. Driven by an explicit audited-event policy.
3. Easy to expand per domain without breaking old behavior.
4. Verifiable with automated tests.

---

## Current Gap Summary

Observed in current generated app:
1. Audit handler allowlist is empty, so no audit records are written.
2. No clear bootstrap contract for which domain events must be audited.
3. No dedicated query/read service for audit logs.
4. No end-to-end tests proving audit writes occur for audited events.

Result: audit_logs table exists but remains unused in normal runs.

---

## Required Bootstrap Components

### 1) Audited Event Registry (Config-Driven)

Bootstrap should generate a dedicated registry module with:
1. A default set of audited event names.
2. Ability to extend via local domain modules.
3. Optional environment override (comma-separated list).

Suggested file:
- my_app/services/infra/audit/audited_events.py

Required behavior:
1. Returns a set of strings.
2. Merges base defaults + local extensions + env overrides.
3. Deduplicates and validates event name format.

Why:
- Keeps policy out of handler internals.
- Makes expansion cheap and explicit.

### 2) Audit Handler Integration Contract

Bootstrap should keep event-bus handler registration, but generate handler logic that:
1. Loads audited event set from registry (not module-local empty set).
2. Skips fast when event is not audited.
3. Requires workspace_id, with fallback from event.extra.
4. Logs structured warning when skipped due to missing workspace_id.
5. Never crashes caller path if audit write fails.

Suggested file:
- my_app/services/infra/events/handlers/audit_handler.py

### 3) Audit Write Service (Single Boundary)

Bootstrap should keep one write boundary and enforce usage through it.

Suggested file:
- my_app/services/infra/audit/write_audit.py

Required behavior:
1. write_audit_from_event for event-bus use.
2. write_audit for request-context use.
3. Stable schema mapping to audit_logs table.
4. Strict defaults for optional fields (detail defaults to empty dict).

### 4) Domain Event Emission Standards

Bootstrap should define and enforce event payload contract for auditable events:
1. event_name
2. workspace_id (or extra.workspace_id)
3. client_id for affected resource when available
4. extra as JSON-safe context map

Generate helper docs + examples in architecture contracts so service generators emit correct shape.

### 5) Audit Query Surface (Read Use Cases)

To make audit logs operationally useful, bootstrap should include:
1. Query service for list/filter by workspace, actor, event, date range, resource.
2. Pagination and deterministic sorting by created_at descending.
3. Optional API route for admin-only audit reading.

Suggested files:
- my_app/services/queries/audit/list_audit_logs.py
- my_app/routers/api_v1/audit.py

### 6) Retention and Data Controls

Bootstrap should include policy hooks:
1. Configurable retention window.
2. Optional background cleanup task or archival integration.
3. Privacy handling for sensitive detail payloads (masking rules).

---

## Expandability Rules for Claude

Claude should keep these rules while fixing bootstrap:
1. Event names are policy, not hardcoded in handler.
2. New domains add auditable events via registry extension file, not by editing core handler.
3. Audit schema should be additive-only where possible.
4. Query filters must remain backward-compatible.
5. Fail-open for business flow, fail-logged for audit writer.

---

## Test Plan Required in Bootstrap

### Unit Tests

1. audited_events registry merge behavior.
2. audit_handler skip and write branches:
   - skip non-audited event
   - skip missing workspace_id
   - write on valid audited event
3. write_audit and write_audit_from_event field mapping.

### Integration Tests

1. Emit one audited event and assert 1 row in audit_logs.
2. Emit one non-audited event and assert no new row.
3. Emit audited event with missing workspace_id and assert skip warning + no row.
4. Query endpoint/service returns expected rows with filters and pagination.

### Non-Regression Tests

1. Handler failure does not break primary command success.
2. Detail payload remains JSON-serializable and stored correctly.

---

## Suggested Acceptance Criteria

Bootstrap fix is done when all are true:
1. At least one audited event is enabled by default in generated app.
2. Running an audited command produces a row in audit_logs.
3. Running a non-audited command produces no row.
4. Audit read query returns rows with expected filters.
5. Test suite includes explicit audit coverage.

---

## Immediate Implementation Order for Claude

1. ✅ Generate audited event registry and wire audit_handler to it. — Fixed in bootstrap 2026-05-13
2. ✅ Add default audited events for high-risk actions (auth/session revocation, role changes, destructive case/message operations, participant removals). — Fixed in bootstrap 2026-05-13
3. ✅ Add query service (and optional route) for reading audit logs. — Fixed in bootstrap 2026-05-13
4. ✅ Add unit and integration tests. — Fixed in bootstrap 2026-05-13
5. ✅ Add short docs section in architecture contract for extending audited events. — Fixed in bootstrap 2026-05-13

---

## Notes From Current Test Workspace

1. audit_logs table exists and schema is present.
2. audit handler is registered in app startup.
3. audit path currently effectively disabled due to empty audited event set.

This plan focuses bootstrap changes only, so future generated apps have audit logging enabled and maintainable by default.
