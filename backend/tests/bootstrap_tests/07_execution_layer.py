#!/usr/bin/env python3
"""
TEST 06 — Execution Layer: Task Router / Worker / Retry / Requeue
==================================================================
Purpose : Validate the full execution task lifecycle:
          seed -> router dispatch -> worker completion -> failure -> retry -> requeue
Run from: run_test/bootstrap_test_full_build/backend/app/
Requires:
  - .venv active with app dependencies
  - APP_ENV=development
  - postgres + redis running
  - Execution task infrastructure:
      TaskType enum, ExecutionTask model, task_router, worker_base, HANDLER_MAP

State machine assertions:
  OPEN -> PENDING -> COMPLETED
  PENDING + exception + max_try=1 -> FAIL
  PENDING + exception + max_try>1 -> RETRY_SCHEDULED
  RETRY_SCHEDULED (due) -> OPEN via requeue

Known fixes already applied in bootstrap:
  - RECORD_VIEW_START / RECORD_VIEW_END added to TaskType enum
  - Queue mappings added to QUEUE_MAP in task_router.py
  - presence_worker.py added with handlers for both view task types
  - Presence handler signatures corrected to (payload: dict, task_id: str)
  - db.py added to services/infra/execution/ for task_db_session context manager
"""

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from my_app.domain.execution.enums import TaskType
from my_app.models.database import get_db_session, init_db
from my_app.models.tables.execution.execution_task import ExecutionTask
from my_app.services.infra.execution.task_factory import create_instant_task
from my_app.services.infra.execution.task_router import (
    _requeue_retry_scheduled_tasks,
    _route_open_tasks,
)
from my_app.services.infra.execution.worker_base import _process_task
from my_app.services.infra.redis import get_redis_client
from my_app.workers.health import worker_healthcheck
from my_app.workers.notification_worker import HANDLER_MAP
from my_app.config import settings


def _state_value(state) -> str:
    return state.value if hasattr(state, "value") else str(state)


async def _seed_task(task_type: TaskType, payload: dict, max_try: int = 3) -> str:
    async for session in get_db_session():
        task = await create_instant_task(
            session=session,
            task_type=task_type,
            payload=payload,
            max_try=max_try,
        )
        await session.commit()
        return task.client_id
    raise RuntimeError("Unable to seed task")


async def _get_task(task_id: str) -> ExecutionTask:
    async for session in get_db_session():
        row = await session.execute(
            select(ExecutionTask).where(ExecutionTask.client_id == task_id)
        )
        return row.scalar_one()
    raise RuntimeError("Task not found")


async def _set_retry_due_now(task_id: str) -> None:
    async for session in get_db_session():
        await session.execute(
            update(ExecutionTask)
            .where(ExecutionTask.client_id == task_id)
            .values(next_retry_at=datetime.now(timezone.utc) - timedelta(seconds=5))
        )
        await session.commit()
        return


async def run() -> dict:
    await init_db()
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append({"name": name, "ok": ok, "detail": detail})
        status = "PASS" if ok else "FAIL"
        print(f"{status}: {name}{' | ' + detail if detail else ''}")

    # Resolve Redis URL from settings (avoids port mismatch)
    redis = get_redis_client(settings.redis_url)
    redis.delete("queue:notifications")

    # ------------------------------------------------------------------
    # 1: Seed NOTIFICATION task in OPEN
    # ------------------------------------------------------------------
    notif_id = await _seed_task(
        TaskType.NOTIFICATION,
        {
            "notification_type": "info",
            "user_ids": ["usr_user_test"],
            "title": "Execution test",
            "body": "Notification task",
        },
    )
    notif_task = await _get_task(notif_id)
    check(
        "1. seed notification open",
        _state_value(notif_task.state) == "open",
        f"state={_state_value(notif_task.state)}",
    )

    # ------------------------------------------------------------------
    # 2: Seed DELAYED_REMINDER task in OPEN
    # ------------------------------------------------------------------
    reminder_id = await _seed_task(
        TaskType.DELAYED_REMINDER,
        {
            "workspace_id": "ws_workspace_test",
            "user_id": "usr_user_test",
            "entity_client_id": "case_test_exec_001",
            "message": "Reminder from execution test",
        },
    )
    reminder_task = await _get_task(reminder_id)
    check(
        "2. seed delayed reminder open",
        _state_value(reminder_task.state) == "open",
        f"state={_state_value(reminder_task.state)}",
    )

    # ------------------------------------------------------------------
    # 3: Router poll OPEN -> PENDING
    # ------------------------------------------------------------------
    await _route_open_tasks(redis)
    notif_task = await _get_task(notif_id)
    reminder_task = await _get_task(reminder_id)
    check(
        "3. router moves open -> pending",
        _state_value(notif_task.state) == "pending"
        and _state_value(reminder_task.state) == "pending",
        f"notif={_state_value(notif_task.state)} reminder={_state_value(reminder_task.state)}",
    )

    # ------------------------------------------------------------------
    # 4: Worker drain completes NOTIFICATION -> COMPLETED
    # ------------------------------------------------------------------
    await _process_task(notif_id, "test-worker", HANDLER_MAP)
    notif_task = await _get_task(notif_id)
    check(
        "4. worker completes notification (pending -> completed)",
        _state_value(notif_task.state) == "completed",
        f"state={_state_value(notif_task.state)}",
    )

    # ------------------------------------------------------------------
    # 5: Worker drain completes DELAYED_REMINDER -> COMPLETED
    # ------------------------------------------------------------------
    await _process_task(reminder_id, "test-worker", HANDLER_MAP)
    reminder_task = await _get_task(reminder_id)
    check(
        "5. worker completes delayed reminder (pending -> completed)",
        _state_value(reminder_task.state) == "completed",
        f"state={_state_value(reminder_task.state)}",
    )

    # ------------------------------------------------------------------
    # 6: Failure path — max_try=1 -> FAIL
    # ------------------------------------------------------------------
    async def failing_handler(payload: dict, task_id: str) -> None:
        raise RuntimeError("forced test failure")

    failing_map = {TaskType.NOTIFICATION: failing_handler}

    fail_id = await _seed_task(
        TaskType.NOTIFICATION,
        {
            "notification_type": "info",
            "user_ids": ["usr_user_test"],
            "title": "Fail test",
            "body": "max_try_1",
        },
        max_try=1,
    )
    await _route_open_tasks(redis)
    await _process_task(fail_id, "test-worker", failing_map)
    fail_task = await _get_task(fail_id)
    check(
        "6. failure path max_try=1 -> fail",
        _state_value(fail_task.state) == "fail" and fail_task.try_count == 1,
        f"state={_state_value(fail_task.state)} try_count={fail_task.try_count}",
    )

    # ------------------------------------------------------------------
    # 7: Retry path — max_try=3 -> RETRY_SCHEDULED
    # ------------------------------------------------------------------
    retry_id = await _seed_task(
        TaskType.NOTIFICATION,
        {
            "notification_type": "info",
            "user_ids": ["usr_user_test"],
            "title": "Retry test",
            "body": "max_try_3",
        },
        max_try=3,
    )
    await _route_open_tasks(redis)
    await _process_task(retry_id, "test-worker", failing_map)
    retry_task = await _get_task(retry_id)
    check(
        "7. retry path max_try=3 -> retry_scheduled",
        _state_value(retry_task.state) == "retry_scheduled"
        and retry_task.try_count == 1
        and retry_task.next_retry_at is not None,
        f"state={_state_value(retry_task.state)} try_count={retry_task.try_count}",
    )

    # ------------------------------------------------------------------
    # 8: Worker health check — Redis PONG
    # ------------------------------------------------------------------
    health = worker_healthcheck()
    check(
        "8. worker health check redis=ok",
        health.get("redis") == "ok",
        json.dumps(health),
    )

    # ------------------------------------------------------------------
    # 9: Requeue due RETRY_SCHEDULED -> OPEN
    # ------------------------------------------------------------------
    await _set_retry_due_now(retry_id)
    await _requeue_retry_scheduled_tasks()
    retry_task = await _get_task(retry_id)
    check(
        "9. retry requeue (due retry_scheduled -> open)",
        _state_value(retry_task.state) == "open",
        f"state={_state_value(retry_task.state)}",
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    passed = sum(1 for item in results if item["ok"])
    summary = {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "results": results,
    }

    with open("/tmp/execution_layer_test_results.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("\n════════════════════════════════════════════════════════════")
    print(f"TEST 06 RESULT: {passed} Passed, {summary['failed']} Failed")
    print("════════════════════════════════════════════════════════════")
    if summary["failed"] > 0:
        print()
        print("⚠️  Common issues to check:")
        print("   - Redis URL mismatch: ensure settings.redis_url resolves correctly")
        print("   - TaskType enum members missing: NOTIFICATION / DELAYED_REMINDER required")
        print("   - HANDLER_MAP not wired: check workers/notification_worker.py")
        print()
        print("   Record failures in:")
        print("   run_test/bootstrap_test_full_build/issues/YYYY-MM-DD_06_execution_<desc>.md")

    return summary


if __name__ == "__main__":
    out = asyncio.run(run())
    sys.exit(0 if out["failed"] == 0 else 1)
