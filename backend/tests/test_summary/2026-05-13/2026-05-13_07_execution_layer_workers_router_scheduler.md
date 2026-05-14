# Execution Layer Tests (Router / Worker / Scheduler)
**Date:** 2026-05-13  
**Session:** 07  
**Test File:** `backend/app/test_execution_layer.py`  
**Scope:** task router dispatcher, notification worker, retry scheduling, Redis health

---

## Summary

| Total | ✅ Pass | ❌ Fail | ⚠️ Warn |
|-------|---------|---------|---------|
| 9     | 9       | 0       | 0       |

Execution layer pipeline validated end-to-end using real PostgreSQL + Redis state transitions.

---

## Coverage

1. Seed NOTIFICATION task in `OPEN`
2. Seed DELAYED_REMINDER task in `OPEN`
3. Router poll moves `OPEN -> PENDING`
4. Worker drain completes NOTIFICATION (`PENDING -> COMPLETED`)
5. Worker drain completes DELAYED_REMINDER (`PENDING -> COMPLETED`)
6. Failure path with `max_try=1` moves to `FAIL`
7. Retry path with `max_try=3` moves to `RETRY_SCHEDULED`
8. Worker health check confirms Redis `PONG`
9. Retry requeue moves due `RETRY_SCHEDULED` task back to `OPEN`

---

## State Machine Assertions Verified

- `OPEN -> PENDING -> COMPLETED`
- `PENDING + exception + max_try=1 -> FAIL`
- `PENDING + exception + max_try>1 -> RETRY_SCHEDULED`
- `RETRY_SCHEDULED (due) -> OPEN` via router requeue

---

## Issues Found & Fixed During This Session

### Classification for Bootstrap Handoff
- Bootstrap fix candidate from this session: presence/view-record task wiring gap (identified in post-run audit)
- Test-harness-only fixes: #1, #2, #3, #4 (all within `test_execution_layer.py` runner/assertion mechanics)

### Additional Audit Finding (Not Covered by Session 07 Runtime Test)
- `user_app_view_records` flow was not exercised by endpoint or execution test scripts.
- Presence socket handlers call:
	- `TaskType.RECORD_VIEW_START`
	- `TaskType.RECORD_VIEW_END`
- But these enum members are currently absent from `my_app/domain/execution/enums.py`.
- Consequence: presence handlers cannot enqueue view start/end tasks, so `user_app_view_records` insert/end lifecycle is effectively disconnected from runtime socket events.
- Related wiring gaps also observed:
	- No queue mapping for view-record tasks in `services/infra/execution/task_router.py` (`QUEUE_MAP`)
	- No worker handler mapping for presence task handlers in existing worker entrypoints

Bootstrap handoff action for Claude:
1. Add `RECORD_VIEW_START` and `RECORD_VIEW_END` to `TaskType` enum generation.
2. Add router queue mappings for these task types.
3. Register presence handlers (`handle_record_view_start`, `handle_record_view_end`) in an appropriate worker map.
4. Add automated test coverage for socket presence -> execution task -> `user_app_view_records` DB effects.

**Fixed in bootstrap:** ✅ 2026-05-13
1. `RECORD_VIEW_START = "record_view_start"` and `RECORD_VIEW_END = "record_view_end"` added to `TaskType` enum in `phase_06_execution.py`
2. `TaskType.RECORD_VIEW_START: "queue:presence"` and `TaskType.RECORD_VIEW_END: "queue:presence"` added to `QUEUE_MAP` in `task_router.py` generation
3. `workers/presence_worker.py` added — registers both handlers against `"queue:presence"` and imports real implementations from `services/tasks/presence/` (stubs in phase 6, upgraded to full DB-writing handlers in phase 7)

### 1. Inline runner formatting collisions in test harness
- **Symptom:** temporary script generation failed with malformed dict literals (`TypeError: unhashable type: 'dict'`)
- **Root cause:** string formatting conflicted with braces in embedded Python code snippets
- **Fix:** switched to token replacement for script template generation (`##BASE_DIR##`, `##CODE##`) and used normal Python dict literals in embedded code

### 2. Noisy SQLAlchemy stdout polluted task-id parsing
- **Symptom:** seeded task id parser captured SQL logs instead of clean `task_*` value
- **Root cause:** engine INFO logs mixed into stdout in subprocess runner
- **Fix:** disabled logging in inline runner and filtered non-data lines before parsing

### 3. Enum state comparisons were case-sensitive to lowercase values
- **Symptom:** valid states like `OPEN`, `PENDING`, `COMPLETED` were treated as failures
- **Root cause:** assertions compared against lowercase literals
- **Fix:** normalized with uppercase comparisons in assertions

### 4. Retry due update via raw SQL was brittle
- **Symptom:** retry requeue check intermittently stayed `RETRY_SCHEDULED`
- **Root cause:** raw SQL string updates for enum/timestamp fields were less robust
- **Fix:** updated retry scheduling fields via ORM update using enum value and timezone-aware datetime

---

## Final Result

Execution orchestration infrastructure is validated for core behavior: task dispatch, worker execution, retry/fail semantics, and retry requeue processing.

Bootstrap conclusion from Session 07: execution layer behavior (router/worker/retry with Redis + Postgres) passed end-to-end after harness corrections; no backend execution-service code patch was required to achieve green results.

Refined conclusion after audit: core execution infrastructure is healthy, but presence-to-execution integration for `user_app_view_records` remains a bootstrap wiring defect and should be treated as unresolved.

Result artifact: `/tmp/execution_layer_test_results.json`

---

## Manual Hotfix Validation (No Full Rebootstrap)

Date: 2026-05-13

Objective: manually patch current test app for presence/view-record execution wiring and verify `user_app_view_records` lifecycle.

### Manual fixes applied in current test app
1. Added missing task enum members in [run_test/bootstrap_test_full_build/backend/app/my_app/domain/execution/enums.py](run_test/bootstrap_test_full_build/backend/app/my_app/domain/execution/enums.py)
	- `RECORD_VIEW_START`
	- `RECORD_VIEW_END`
2. Added router queue mappings in [run_test/bootstrap_test_full_build/backend/app/my_app/services/infra/execution/task_router.py](run_test/bootstrap_test_full_build/backend/app/my_app/services/infra/execution/task_router.py)
3. Added worker handler mappings in [run_test/bootstrap_test_full_build/backend/app/my_app/workers/notification_worker.py](run_test/bootstrap_test_full_build/backend/app/my_app/workers/notification_worker.py)
4. Fixed broken DB session import path in presence handlers:
	- [run_test/bootstrap_test_full_build/backend/app/my_app/services/tasks/presence/record_view_start.py](run_test/bootstrap_test_full_build/backend/app/my_app/services/tasks/presence/record_view_start.py)
	- [run_test/bootstrap_test_full_build/backend/app/my_app/services/tasks/presence/record_view_end.py](run_test/bootstrap_test_full_build/backend/app/my_app/services/tasks/presence/record_view_end.py)
5. Aligned presence handler signatures with worker contract `(payload, task_id)`

**Fixed in bootstrap:** ✅ 2026-05-13
- `services/infra/execution/db.py` added in `phase_06_execution.py` — exports `task_db_session` as an `@asynccontextmanager` backed by the shared session factory; presence handlers import from here
- All presence handler signatures corrected to `(payload: dict, task_id: str)` in both phase_06 stubs and phase_07 real implementations
- `handle_create_notifications` and `handle_send_push_notification` signatures corrected to `(payload: dict, task_id: str)` in `phase_08_notifications.py`
- `notification_worker.py` HANDLER_MAP now wired in phase_08 to use `handle_create_notifications` (replacing the generic `handle_notification` for `CREATE_NOTIFICATIONS`) and adds `SEND_PUSH_NOTIFICATION → handle_send_push_notification`

### Focused rerun
- Test script: [run_test/bootstrap_test_full_build/backend/app/test_user_app_view_records.py](run_test/bootstrap_test_full_build/backend/app/test_user_app_view_records.py)
- Result: PASS
- Verified outcomes:
  - start task created 1 open view record
  - user `last_app_view_record_id` updated to `uavr_*`
  - end task set `ended_at` and closed the same record

Terminal result excerpt:
- `open_view_records=1`
- `ended_view_records=1`
- `PASS user_app_view_records flow is working`
