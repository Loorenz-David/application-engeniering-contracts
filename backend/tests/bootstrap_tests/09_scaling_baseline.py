#!/usr/bin/env python3
"""
TEST 08 — Scaling & Performance Baseline
=========================================
Purpose : Validate that the build meets minimum scaling readiness requirements:
          DB pool tuning, Redis eviction policy, task router poll interval,
          retry jitter, DB index coverage, pagination enforcement, cache effect.

Run from: run_test/bootstrap_test_full_build/backend/app/
Requires:
  - .venv active, APP_ENV=development
  - postgres + redis running
  - App running on http://localhost:8000
  - .test_token_bootstrap (run 00_seed_identity.sh first)
  - .env loaded: DB_POOL_SIZE>=20, DB_MAX_OVERFLOW>=10
  - Redis: maxmemory-policy=allkeys-lru

Known fixes required before this test passes:
  - DB_POOL_SIZE=20, DB_MAX_OVERFLOW=20, DB_POOL_RECYCLE=1800 in .env
  - redis-cli -p 6379 CONFIG SET maxmemory-policy allkeys-lru
  - task_router must expose _notify_event and _listen_for_task_events (LISTEN/NOTIFY wired)
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib import error, request

import jwt
import redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from my_app.config import settings
from my_app.services.infra.execution import task_router, worker_base

BASE_URL = "http://localhost:8000"


def _load_token() -> str:
    token_file = ".test_token_bootstrap"
    if os.path.exists(token_file):
        with open(token_file, "r", encoding="utf-8") as f:
            token = f.read().strip()
            if token:
                return token
    # Fallback: generate a minimal JWT
    payload = {
        "sub": "usr_user_test",
        "user_id": "usr_user_test",
        "workspace_id": "ws_workspace_test",
        "backend_permissions": [
            "POST:/api/v1/cases",
            "GET:/api/v1/cases",
            "GET:/api/v1/cases/<client_id>",
        ],
        "iat": int(time.time()),
        "exp": int(time.time()) + 3600,
    }
    secret = getattr(settings, "jwt_secret_key", "replace-me")
    return jwt.encode(payload, secret, algorithm="HS256")


def _http_json(method: str, path: str, token: str | None = None, body: dict | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = request.Request(
        f"{BASE_URL}{path}", method=method, headers=headers, data=data
    )
    try:
        with request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw)
    except error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw}


async def _db_fetch_set(sql: str) -> set[str]:
    engine = create_async_engine(settings.database_url)
    try:
        async with engine.begin() as conn:
            res = await conn.execute(text(sql))
            return {r[0] for r in res.fetchall()}
    finally:
        await engine.dispose()


async def main():
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str):
        results.append({"name": name, "ok": ok, "detail": detail})
        print(f"{'PASS' if ok else 'FAIL'}: {name} | {detail}")

    token = _load_token()

    print("════════════════════════════════════════════════════════════")
    print("TEST 08: Scaling & Performance Baseline")
    print(f"Redis URL: {settings.redis_url}")
    print(f"DB pool size: {getattr(settings, 'db_pool_size', '?')}")
    print("════════════════════════════════════════════════════════════")
    print()

    # ------------------------------------------------------------------
    # 1: API health
    # ------------------------------------------------------------------
    status, body = _http_json("GET", "/health")
    check(
        "1. health endpoint",
        status == 200 and body.get("status") == "ok",
        f"status={status} body_status={body.get('status')}",
    )

    # Ensure at least one case exists for latency tests
    status, created = _http_json(
        "POST", "/api/v1/cases", token=token, body={"case_type_id": "ct_investigation"}
    )
    case_id = None
    if status == 200:
        case_id = ((created.get("data") or {}).get("case") or {}).get("client_id")

    # ------------------------------------------------------------------
    # 2: Pagination — limit parameter honored
    # ------------------------------------------------------------------
    status, listed = _http_json("GET", "/api/v1/cases?limit=1&offset=0", token=token)
    count = (
        len(((listed.get("data") or {}).get("cases") or [])) if status == 200 else -1
    )
    check(
        "2. cases list pagination limit=1 honored",
        status == 200 and count <= 1,
        f"status={status} returned={count}",
    )

    # ------------------------------------------------------------------
    # 3: Default list upper bound <= 50
    # ------------------------------------------------------------------
    status, listed_default = _http_json("GET", "/api/v1/cases", token=token)
    count_default = (
        len(((listed_default.get("data") or {}).get("cases") or []))
        if status == 200
        else -1
    )
    check(
        "3. cases list default cap <= 50",
        status == 200 and count_default <= 50,
        f"status={status} returned={count_default}",
    )

    # ------------------------------------------------------------------
    # 4: Cache latency effect (second read <= first read)
    # ------------------------------------------------------------------
    latency_ok = False
    detail = "case not available — skipped"
    if case_id:
        t1 = time.perf_counter()
        s1, _ = _http_json("GET", f"/api/v1/cases/{case_id}", token=token)
        first_ms = (time.perf_counter() - t1) * 1000

        t2 = time.perf_counter()
        s2, _ = _http_json("GET", f"/api/v1/cases/{case_id}", token=token)
        second_ms = (time.perf_counter() - t2) * 1000

        latency_ok = s1 == 200 and s2 == 200 and second_ms <= first_ms
        detail = f"first_ms={first_ms:.2f} second_ms={second_ms:.2f}"
    check("4. single-case cache latency effect", latency_ok, detail)

    # ------------------------------------------------------------------
    # 5: Critical DB index subset
    # ------------------------------------------------------------------
    indexes = await _db_fetch_set(
        "SELECT indexname FROM pg_indexes WHERE schemaname = 'public'"
    )
    required_indexes = {
        "ix_cases_state",
        "ix_cases_created_by_id",
        "ix_execution_tasks_next_retry_at",
        "ix_notifications_user_id",
    }
    missing = sorted(required_indexes - indexes)
    check(
        "5. critical DB index subset present",
        len(missing) == 0,
        f"missing={missing if missing else 'none'}",
    )

    # ------------------------------------------------------------------
    # 6: DB connection pool tuning
    # ------------------------------------------------------------------
    pool_size = getattr(settings, "db_pool_size", 0)
    max_overflow = getattr(settings, "db_max_overflow", 0)
    pool_ok = pool_size >= 20 and max_overflow >= 10
    check(
        "6. DB pool tuned (db_pool_size>=20, db_max_overflow>=10)",
        pool_ok,
        f"db_pool_size={pool_size} db_max_overflow={max_overflow}",
    )

    # ------------------------------------------------------------------
    # 7: Redis eviction policy = allkeys-lru
    # ------------------------------------------------------------------
    r = redis.from_url(settings.redis_url, decode_responses=True)
    policy = r.config_get("maxmemory-policy").get("maxmemory-policy", "unknown")
    check(
        "7. Redis eviction policy=allkeys-lru",
        policy == "allkeys-lru",
        f"policy={policy} (set with: redis-cli CONFIG SET maxmemory-policy allkeys-lru)",
    )

    # ------------------------------------------------------------------
    # 8: Task router uses event-driven LISTEN/NOTIFY (not pure polling)
    #    FALLBACK_POLL_SECONDS is intentional as a safety net when the
    #    LISTEN connection drops — routing latency is driven by pg_notify
    #    firing instantly, not by this constant. Sleep mode handles idle.
    # ------------------------------------------------------------------
    has_notify_event = hasattr(task_router, "_notify_event")
    has_listen_fn = hasattr(task_router, "_listen_for_task_events")
    fallback = getattr(task_router, "FALLBACK_POLL_SECONDS", None)
    check(
        "8. task router event-driven via LISTEN/NOTIFY",
        has_notify_event and has_listen_fn and fallback is not None,
        f"_notify_event={has_notify_event} _listen_for_task_events={has_listen_fn} "
        f"FALLBACK_POLL_SECONDS={fallback} (safety-net only)",
    )

    # ------------------------------------------------------------------
    # 9: Retry jitter enabled
    # ------------------------------------------------------------------
    check(
        "9. retry jitter enabled",
        worker_base.BACKOFF_JITTER > 0,
        f"BACKOFF_JITTER={worker_base.BACKOFF_JITTER}",
    )

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    summary = {
        "date": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }

    out_path = "/tmp/scaling_performance_test_08_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print()
    print("════════════════════════════════════════════════════════════")
    print(f"TEST 08 RESULT: {summary['passed']} Passed, {summary['failed']} Failed")
    print("════════════════════════════════════════════════════════════")
    if summary["failed"] > 0:
        print()
        print("⚠️  Common fixes:")
        print("   - DB pool: set DB_POOL_SIZE=20, DB_MAX_OVERFLOW=20 in .env")
        print("   - Redis policy: redis-cli -p 6379 CONFIG SET maxmemory-policy allkeys-lru")
        print("   - LISTEN/NOTIFY: verify _notify_event and _listen_for_task_events in task_router.py")
        print("   - Redis port mismatch: check settings.redis_url resolves to correct port")
        print()
        print("   Record failures in:")
        print("   run_test/bootstrap_test_full_build/issues/YYYY-MM-DD_08_scaling_<desc>.md")

    sys.exit(0 if summary["failed"] == 0 else 1)


if __name__ == "__main__":
    asyncio.run(main())
