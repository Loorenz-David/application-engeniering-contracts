#!/usr/bin/env python3
"""
TEST 10 — Sleep Mode
====================
Purpose : Validate that Redis-backed idle sleep mode works correctly end-to-end:
          - ActivityTracker API (enter_sleep / is_sleeping / touch / idle_seconds)
          - HTTP requests wake the system via SleepMiddleware
          - Simulated idle → sleep transition (injected fast threshold, no waiting)
          - Optional: real-threshold test driven by actual _sleep_monitor timing

Run from: run_test/bootstrap_test_full_build/backend/app/
Requires:
  - .venv active, APP_ENV=development
  - postgres + redis running
  - App running on http://localhost:8000
  - .test_token_bootstrap present (run 01_seed_identity.sh first)

Real-threshold optional test additionally requires:
  - Task router running (make task-router) — _sleep_monitor lives there
  - Patience: waits IDLE_SLEEP_THRESHOLD_SECONDS + 120s

Note on architecture:
  FALLBACK_POLL_SECONDS is intentionally 30s — it is a safety net for LISTEN/NOTIFY
  drop, not the routing latency driver. Sleep mode handles idle periods entirely.
  The _sleep_monitor() coroutine (in the task router process) polls every 60s and
  calls ActivityTracker.enter_sleep() when idle_seconds >= threshold.
"""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from urllib import error, request as urllib_request

BASE_URL = "http://localhost:8000"
FAST_IDLE_SECONDS = 3  # injected threshold for the simulation phase


# ── HTTP helper ───────────────────────────────────────────────────────────────

def _http_json(method, path, token=None, body=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib_request.Request(
        f"{BASE_URL}{path}", method=method, headers=headers, data=data
    )
    try:
        with urllib_request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


# ── Interactive prompt with timeout ──────────────────────────────────────────

def _ask_yes_no(prompt, timeout=None):
    """Ask a yes/no question. Returns True for yes. Auto-skips when not a TTY."""
    if timeout is None:
        timeout = int(os.getenv("SLEEP_PROMPT_TIMEOUT_SECONDS", "120"))
    if not sys.stdin.isatty():
        print(f"  (non-interactive — skipping: {prompt})")
        return False
    if timeout > 0:
        print(f"\n  {prompt} (y/N, {timeout}s timeout): ", end="", flush=True)
    else:
        print(f"\n  {prompt} (y/N, wait forever): ", end="", flush=True)
    answer = ["n"]

    def _read():
        try:
            answer[0] = sys.stdin.readline().strip().lower()
        except Exception:
            pass

    t = threading.Thread(target=_read, daemon=True)
    t.start()
    if timeout > 0:
        t.join(timeout=timeout)
    else:
        t.join()
    if timeout > 0 and t.is_alive():
        print("(timed out — skipping)")
    return answer[0] == "y"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    results: list[dict] = []

    def check(name: str, ok: bool, detail: str):
        results.append({"name": name, "ok": ok, "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'}: {name} | {detail}")

    # ── imports ──────────────────────────────────────────────────────────────
    try:
        import redis as redis_lib
        from my_app.config import settings
        from my_app.services.infra.sleep.activity_tracker import ActivityTracker
    except ImportError as exc:
        print(f"FATAL: import failed — {exc}")
        sys.exit(1)

    token = ""
    if os.path.exists(".test_token_bootstrap"):
        token = open(".test_token_bootstrap").read().strip()

    threshold = settings.idle_sleep_threshold_seconds
    sleep_key = f"{settings.redis_key_prefix}:system:sleeping"
    activity_key = f"{settings.redis_key_prefix}:system:last_activity"
    r = redis_lib.from_url(settings.redis_url, decode_responses=True)

    print("════════════════════════════════════════════════════════════")
    print("TEST 10: Sleep Mode")
    print(f"  SLEEP_MODE_ENABLED          : {settings.sleep_mode_enabled}")
    print(f"  IDLE_SLEEP_THRESHOLD_SECONDS: {threshold}s ({threshold / 60:.1f} min)")
    print(f"  REDIS_KEY_PREFIX            : {settings.redis_key_prefix}")
    print("════════════════════════════════════════════════════════════")
    print()

    # always restore to awake on exit
    def _cleanup():
        try:
            ActivityTracker.touch()
        except Exception:
            pass

    try:
        # ── Phase 1: ActivityTracker API ─────────────────────────────────────
        print("Phase 1 — ActivityTracker API")

        ActivityTracker.touch()
        check("1. touch() clears sleep state", not ActivityTracker.is_sleeping(),
              "sleep key absent after touch")

        idle = ActivityTracker.idle_seconds()
        check("2. touch() resets idle timer (idle_seconds < 5)", idle < 5.0,
              f"idle_seconds={idle:.3f}s")

        ActivityTracker.enter_sleep()
        check("3. enter_sleep() sets is_sleeping=True", ActivityTracker.is_sleeping(),
              "sleep key present in Redis")

        ActivityTracker.touch()
        check("4. touch() wakes from sleep (is_sleeping=False)", not ActivityTracker.is_sleeping(),
              "sleep key removed by touch")
        print()

        # ── Phase 2: HTTP request wakes system via SleepMiddleware ───────────
        print("Phase 2 — HTTP wake-up via SleepMiddleware")

        ActivityTracker.enter_sleep()
        check("5. system entered sleep before HTTP request",
              ActivityTracker.is_sleeping(), "sleep key set manually")

        status, body = _http_json("GET", "/health", token=token)
        check("6. GET /health returns 200 while system is sleeping",
              status == 200, f"status={status} — app stays responsive during sleep")

        check("7. SleepMiddleware.touch() woke system after request",
              not ActivityTracker.is_sleeping(),
              "sleep key cleared by SleepMiddleware on inbound request")
        print()

        # ── Phase 3: Simulated idle → sleep transition ───────────────────────
        print(f"Phase 3 — Simulated idle → sleep (injected threshold={FAST_IDLE_SECONDS}s)")
        print(f"  (back-dating last_activity by {FAST_IDLE_SECONDS + 1}s to simulate idle)")

        # Simulate idle by back-dating the activity timestamp in Redis
        r.set(activity_key, str(time.time() - FAST_IDLE_SECONDS - 1), ex=86400)
        r.delete(sleep_key)

        idle_now = ActivityTracker.idle_seconds()
        check("8. idle_seconds() reflects back-dated activity",
              idle_now >= FAST_IDLE_SECONDS,
              f"idle_seconds={idle_now:.2f}s (expected >= {FAST_IDLE_SECONDS}s)")

        # Simulate exactly what _sleep_monitor does
        if idle_now >= FAST_IDLE_SECONDS:
            ActivityTracker.enter_sleep()
        check("9. _sleep_monitor logic: idle >= threshold → enter_sleep",
              ActivityTracker.is_sleeping(),
              f"idle_seconds={idle_now:.2f}s triggered sleep")

        # Simulate inbound request via SleepMiddleware
        ActivityTracker.touch()
        check("10. touch() wakes from simulated idle sleep",
              not ActivityTracker.is_sleeping(),
              "sleep cleared — system back to active state")
        print()

    finally:
        _cleanup()

    # ── Fast phase summary ────────────────────────────────────────────────────
    fast_passed = sum(1 for r in results if r["ok"])
    fast_failed = sum(1 for r in results if not r["ok"])

    print("════════════════════════════════════════════════════════════")
    print(f"Fast sleep tests: {fast_passed} Passed, {fast_failed} Failed")
    print("════════════════════════════════════════════════════════════")
    print()

    # ── Phase 4: Optional real-threshold test ────────────────────────────────
    if fast_failed > 0:
        print("⚠️  Fast tests failed — fix before running real-threshold test.")
        print()
    else:
        wait_seconds = threshold + 120
        print(f"Phase 4 — Real-threshold test (optional)")
        print(f"  Current threshold : {threshold}s ({threshold / 60:.1f} min)")
        print(f"  Wait required     : {wait_seconds}s ({wait_seconds / 60:.1f} min)")
        print(f"  How it works      : waits with no HTTP requests so _sleep_monitor")
        print(f"                      (in the task router process) detects idleness")
        print(f"                      and calls ActivityTracker.enter_sleep() naturally.")
        print(f"  Requires          : task router running (make task-router)")

        if _ask_yes_no("Run real-threshold test?"):
            try:
                ActivityTracker.touch()  # reset timer cleanly before we start waiting
                print(f"\n  Waiting {wait_seconds}s — do not make any HTTP requests...")

                interval = 15
                elapsed = 0
                while elapsed < wait_seconds:
                    remaining = wait_seconds - elapsed
                    print(f"  ...{remaining}s remaining", end="\r", flush=True)
                    time.sleep(min(interval, remaining))
                    elapsed += interval
                print()

                sleeping = ActivityTracker.is_sleeping()
                idle_at_check = ActivityTracker.idle_seconds()
                ok_r1 = sleeping
                results.append({
                    "name": "R1. system entered sleep after real threshold",
                    "ok": ok_r1,
                    "detail": f"is_sleeping={sleeping} idle_seconds={idle_at_check:.0f}s threshold={threshold}s",
                })
                print(f"  {'PASS' if ok_r1 else 'FAIL'}: R1. system entered sleep | "
                      f"is_sleeping={sleeping} idle={idle_at_check:.0f}s")

                if not sleeping:
                    print("  ⚠️  System did not enter sleep — is the task router running?")
                    print("       Start it with: make task-router")

                # Wake via HTTP
                status, body = _http_json("GET", "/health", token=token)
                woke = not ActivityTracker.is_sleeping()
                ok_r2 = status == 200 and woke
                results.append({
                    "name": "R2. HTTP request woke system from real sleep",
                    "ok": ok_r2,
                    "detail": f"status={status} is_sleeping_after={ActivityTracker.is_sleeping()}",
                })
                print(f"  {'PASS' if ok_r2 else 'FAIL'}: R2. HTTP wake-up | "
                      f"status={status} woke={woke}")

            finally:
                _cleanup()
        else:
            print("  Real-threshold test skipped.")
        print()

    # ── Final summary ─────────────────────────────────────────────────────────
    total_passed = sum(1 for r in results if r["ok"])
    total_failed = sum(1 for r in results if not r["ok"])

    summary = {
        "date": datetime.now(timezone.utc).isoformat(),
        "total": len(results),
        "passed": total_passed,
        "failed": total_failed,
        "results": results,
    }
    out_path = "/tmp/sleep_mode_test_10_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    print("════════════════════════════════════════════════════════════")
    print(f"TEST 10 RESULT: {total_passed} Passed, {total_failed} Failed")
    print("════════════════════════════════════════════════════════════")
    if total_failed > 0:
        print()
        print("⚠️  Common fixes:")
        print("   - Redis not running: check settings.redis_url")
        print("   - ActivityTracker import failed: verify phase_01_base generated sleep files")
        print("   - SleepMiddleware not registered: verify phase_05_realtime wiring in __init__.py")
        print()
        print("   Record failures in:")
        print("   run_test/bootstrap_test_full_build/issues/YYYY-MM-DD_10_sleep_mode_<desc>.md")
    print()

    sys.exit(0 if total_failed == 0 else 1)


if __name__ == "__main__":
    main()
