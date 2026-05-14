#!/usr/bin/env bash
# =============================================================================
# run_all.sh — Full Bootstrap Test Orchestrator
# Purpose : Run all bootstrap validation tests in sequence.
#           Stops on seed failure. Continues through individual test failures
#           and prints a final summary.
# Run from: <project>/backend/app/
# Requires: .venv active, APP_ENV=development, postgres + redis + app running
# =============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TESTS_DIR="$SCRIPT_DIR"
APP_DIR="$(cd "$SCRIPT_DIR/../../app" && pwd)"
export APP_DIR
BASE_URL="${APP_URL:-http://localhost:8000}"
export APP_URL="$BASE_URL"
cd "$APP_DIR"

if [ -f "$APP_DIR/.venv/bin/activate" ]; then
  # Ensure python/pip resolve from the app venv regardless of caller shell state.
  # shellcheck disable=SC1091
  source "$APP_DIR/.venv/bin/activate"
else
  echo "❌  Missing virtual environment at: $APP_DIR/.venv"
  echo "   Run bootstrap_app.sh first to create and install dependencies."
  exit 1
fi

VENV_PY="$APP_DIR/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  echo "❌  Python executable not found in venv: $VENV_PY"
  exit 1
fi
export PATH="$APP_DIR/.venv/bin:$PATH"
export PYTHONPATH="$APP_DIR${PYTHONPATH:+:$PYTHONPATH}"
export APP_ENV="${APP_ENV:-development}"

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║       BOOTSTRAP FULL-BUILD TEST SUITE                    ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Working dir : $APP_DIR"
echo "Tests dir   : $TESTS_DIR"
echo "App URL     : $BASE_URL"
echo "Date        : $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo ""

TOTAL_SUITES=0
PASSED_SUITES=0
FAILED_SUITES=()
STARTED_API=0
API_PID=""
STARTED_TASK_ROUTER=0
TASK_ROUTER_PID=""
STARTED_WORKER=0
WORKER_PID=""
STARTED_DELAYED_SCHED=0
DELAYED_SCHED_PID=""
STARTED_RECURRING_SCHED=0
RECURRING_SCHED_PID=""

cleanup_started_processes() {
  if [ "$STARTED_RECURRING_SCHED" -eq 1 ] && [ -n "$RECURRING_SCHED_PID" ]; then
    kill "$RECURRING_SCHED_PID" 2>/dev/null || true
  fi
  if [ "$STARTED_DELAYED_SCHED" -eq 1 ] && [ -n "$DELAYED_SCHED_PID" ]; then
    kill "$DELAYED_SCHED_PID" 2>/dev/null || true
  fi
  if [ "$STARTED_WORKER" -eq 1 ] && [ -n "$WORKER_PID" ]; then
    kill "$WORKER_PID" 2>/dev/null || true
  fi
  if [ "$STARTED_TASK_ROUTER" -eq 1 ] && [ -n "$TASK_ROUTER_PID" ]; then
    kill "$TASK_ROUTER_PID" 2>/dev/null || true
  fi
  if [ "$STARTED_API" -eq 1 ] && [ -n "$API_PID" ]; then
    kill "$API_PID" 2>/dev/null || true
  fi
}

trap cleanup_started_processes EXIT

wait_for_health() {
  local max_attempts="${1:-45}"
  local attempt=0
  while ! curl -s --connect-timeout 2 --max-time 3 "$BASE_URL/health" >/dev/null; do
    attempt=$((attempt + 1))
    if [ "$STARTED_API" -eq 1 ] && [ -n "$API_PID" ] && ! kill -0 "$API_PID" 2>/dev/null; then
      echo ""
      echo "❌  API process exited during startup"
      return 1
    fi
    if [ $((attempt % 5)) -eq 0 ]; then
      echo "   ...waiting for API health at $BASE_URL ($attempt/${max_attempts})"
    fi
    if [ "$attempt" -ge "$max_attempts" ]; then
      return 1
    fi
    sleep 1
  done
  return 0
}

detect_api_base_url_from_log() {
  local detected
  detected="$(grep -Eo 'http://(127\.0\.0\.1|0\.0\.0\.0|localhost):[0-9]+' /tmp/bootstrap_test_api.log 2>/dev/null | tail -1 || true)"
  if [ -n "$detected" ]; then
    detected="${detected/0.0.0.0/localhost}"
    BASE_URL="$detected"
    export APP_URL="$BASE_URL"
    echo "   ↳ Detected API URL from logs: $BASE_URL"
  fi
}

start_api_if_needed() {
  if curl -s --connect-timeout 3 --max-time 5 "$BASE_URL/health" >/dev/null; then
    echo "✅  API already reachable at $BASE_URL"
    return 0
  fi

  echo "→ API not reachable at $BASE_URL — starting local app server..."
  APP_ENV=development "$VENV_PY" run.py >/tmp/bootstrap_test_api.log 2>&1 &
  API_PID=$!
  STARTED_API=1
  sleep 1
  detect_api_base_url_from_log

  if wait_for_health 45; then
    echo "✅  API started (pid=$API_PID)"
    echo "✅  Using APP_URL=$BASE_URL"
    return 0
  fi

  detect_api_base_url_from_log
  if wait_for_health 5; then
    echo "✅  API started (pid=$API_PID)"
    echo "✅  Using APP_URL=$BASE_URL"
    return 0
  fi

  echo "❌  API failed to start at $BASE_URL"
  echo "   Last 40 lines: /tmp/bootstrap_test_api.log"
  tail -40 /tmp/bootstrap_test_api.log || true
  return 1
}

start_task_router_if_needed() {
  if [ "${AUTO_START_BACKGROUND_WORKERS:-1}" != "1" ]; then
    return 0
  fi
  if [ "${AUTO_START_TASK_ROUTER:-1}" != "1" ]; then
    return 0
  fi

  if pgrep -f "my_app.workers.task_router_process" >/dev/null 2>&1 || pgrep -f "my_app/workers/task_router_process.py" >/dev/null 2>&1; then
    echo "✅  Task router already running"
    return 0
  fi

  echo "→ Starting task-router worker for sleep-mode coverage..."
  APP_ENV=development "$VENV_PY" -m my_app.workers.task_router_process >/tmp/bootstrap_test_task_router.log 2>&1 &
  TASK_ROUTER_PID=$!
  STARTED_TASK_ROUTER=1
  sleep 2

  if ! kill -0 "$TASK_ROUTER_PID" 2>/dev/null; then
    echo "❌  Task router failed to start"
    echo "   Last 40 lines: /tmp/bootstrap_test_task_router.log"
    tail -40 /tmp/bootstrap_test_task_router.log || true
    return 1
  fi

  echo "✅  Task router started (pid=$TASK_ROUTER_PID)"
}

start_worker_if_needed() {
  if [ "${AUTO_START_BACKGROUND_WORKERS:-1}" != "1" ]; then
    return 0
  fi
  if [ "${AUTO_START_DEFAULT_WORKER:-1}" != "1" ]; then
    return 0
  fi
  if pgrep -f "scripts/worker.py" >/dev/null 2>&1; then
    echo "✅  Default worker already running"
    return 0
  fi

  echo "→ Starting default worker..."
  APP_ENV=development "$VENV_PY" scripts/worker.py >/tmp/bootstrap_test_worker.log 2>&1 &
  WORKER_PID=$!
  STARTED_WORKER=1
  sleep 2

  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    echo "❌  Default worker failed to start"
    echo "   Last 40 lines: /tmp/bootstrap_test_worker.log"
    tail -40 /tmp/bootstrap_test_worker.log || true
    return 1
  fi

  echo "✅  Default worker started (pid=$WORKER_PID)"
}

start_delayed_scheduler_if_needed() {
  if [ "${AUTO_START_BACKGROUND_WORKERS:-1}" != "1" ]; then
    return 0
  fi
  if [ "${AUTO_START_DELAYED_SCHEDULER:-1}" != "1" ]; then
    return 0
  fi
  if pgrep -f "my_app.workers.delayed_scheduler_runner" >/dev/null 2>&1 || pgrep -f "my_app/workers/delayed_scheduler_runner.py" >/dev/null 2>&1; then
    echo "✅  Delayed scheduler already running"
    return 0
  fi

  echo "→ Starting delayed scheduler..."
  APP_ENV=development "$VENV_PY" -m my_app.workers.delayed_scheduler_runner >/tmp/bootstrap_test_delayed_scheduler.log 2>&1 &
  DELAYED_SCHED_PID=$!
  STARTED_DELAYED_SCHED=1
  sleep 2

  if ! kill -0 "$DELAYED_SCHED_PID" 2>/dev/null; then
    echo "❌  Delayed scheduler failed to start"
    echo "   Last 40 lines: /tmp/bootstrap_test_delayed_scheduler.log"
    tail -40 /tmp/bootstrap_test_delayed_scheduler.log || true
    return 1
  fi

  echo "✅  Delayed scheduler started (pid=$DELAYED_SCHED_PID)"
}

start_recurring_scheduler_if_needed() {
  if [ "${AUTO_START_BACKGROUND_WORKERS:-1}" != "1" ]; then
    return 0
  fi
  if [ "${AUTO_START_RECURRING_SCHEDULER:-1}" != "1" ]; then
    return 0
  fi
  if pgrep -f "my_app.workers.recurring_scheduler_runner" >/dev/null 2>&1 || pgrep -f "my_app/workers/recurring_scheduler_runner.py" >/dev/null 2>&1; then
    echo "✅  Recurring scheduler already running"
    return 0
  fi

  echo "→ Starting recurring scheduler..."
  APP_ENV=development "$VENV_PY" -m my_app.workers.recurring_scheduler_runner >/tmp/bootstrap_test_recurring_scheduler.log 2>&1 &
  RECURRING_SCHED_PID=$!
  STARTED_RECURRING_SCHED=1
  sleep 2

  if ! kill -0 "$RECURRING_SCHED_PID" 2>/dev/null; then
    echo "❌  Recurring scheduler failed to start"
    echo "   Last 40 lines: /tmp/bootstrap_test_recurring_scheduler.log"
    tail -40 /tmp/bootstrap_test_recurring_scheduler.log || true
    return 1
  fi

  echo "✅  Recurring scheduler started (pid=$RECURRING_SCHED_PID)"
}

start_background_stack_if_needed() {
  if [ "${AUTO_START_BACKGROUND_WORKERS:-1}" != "1" ]; then
    return 0
  fi

  echo "→ Starting background stack (non-conflict mode) after test 07..."
  start_task_router_if_needed || return 1
  start_worker_if_needed || return 1
  start_delayed_scheduler_if_needed || return 1
  start_recurring_scheduler_if_needed || return 1
}

if ! start_api_if_needed; then
  exit 1
fi

run_suite() {
  local label="$1"
  local cmd="$2"
  ((TOTAL_SUITES++))
  echo "─────────────────────────────────────────────────────────────"
  echo "▶  $label"
  echo "─────────────────────────────────────────────────────────────"
  if eval "$cmd"; then
    ((PASSED_SUITES++))
    echo "✅  $label — PASS"
  else
    FAILED_SUITES+=("$label")
    echo "❌  $label — FAIL"
  fi
  echo ""
}

# ---------------------------------------------------------------------------
# 01 — Seed Identity (must pass for all downstream tests)
# ---------------------------------------------------------------------------
echo "─────────────────────────────────────────────────────────────"
echo "▶  01: Seed Identity"
echo "─────────────────────────────────────────────────────────────"
if ! bash "$TESTS_DIR/01_seed_identity.sh"; then
  echo ""
  echo "❌  01_seed_identity.sh failed — aborting test suite."
  echo "   Fix the seed step before running further tests."
  exit 1
fi
((TOTAL_SUITES++))
((PASSED_SUITES++))
echo "✅  01: Seed Identity — PASS"
echo ""

# ---------------------------------------------------------------------------
# 02 — Health + Auth
# ---------------------------------------------------------------------------
run_suite "02: Health + Auth" "bash '$TESTS_DIR/02_health_auth.sh'"

# ---------------------------------------------------------------------------
# 03 — Notifications
# ---------------------------------------------------------------------------
run_suite "03: Notifications" "bash '$TESTS_DIR/03_notifications.sh'"

# ---------------------------------------------------------------------------
# 04 — VAPID
# ---------------------------------------------------------------------------
run_suite "04: VAPID Public Key" "bash '$TESTS_DIR/04_vapid.sh'"

# ---------------------------------------------------------------------------
# 05 — S3 Images & Files (skips gracefully if .env.s3 missing)
# ---------------------------------------------------------------------------
run_suite "05: S3 Images & Files" "bash '$TESTS_DIR/05_s3_images_files.sh'"

# ---------------------------------------------------------------------------
# 06 — Cases CRUD
# ---------------------------------------------------------------------------
run_suite "06: Cases CRUD" "bash '$TESTS_DIR/06_cases_crud.sh'"

# ---------------------------------------------------------------------------
# 07 — Execution Layer (Python)
# ---------------------------------------------------------------------------
run_suite "07: Execution Layer" "'$VENV_PY' '$TESTS_DIR/07_execution_layer.py'"

# ---------------------------------------------------------------------------
# 08 — Audit Logs
# ---------------------------------------------------------------------------
if ! start_background_stack_if_needed; then
  FAILED_SUITES+=("08+: Background stack bootstrap failed")
fi
run_suite "08: Audit Logs" "bash '$TESTS_DIR/08_audit_logs.sh'"

# ---------------------------------------------------------------------------
# 09 — Scaling Baseline (Python)
# ---------------------------------------------------------------------------
run_suite "09: Scaling Baseline" "'$VENV_PY' '$TESTS_DIR/09_scaling_baseline.py'"

# ---------------------------------------------------------------------------
# 10 — Cases Cache
# ---------------------------------------------------------------------------
run_suite "10: Cases Cache" "bash '$TESTS_DIR/10_cases_cache.sh'"

# ---------------------------------------------------------------------------
# 11 — Sleep Mode (Python)
# ---------------------------------------------------------------------------
run_suite "11: Sleep Mode" "'$VENV_PY' '$TESTS_DIR/11_sleep_mode.py'"

# ---------------------------------------------------------------------------
# Final Summary
# ---------------------------------------------------------------------------
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  FINAL SUMMARY                                           ║"
echo "╠═══════════════════════════════════════════════════════════╣"
printf "║  Total suites : %-43s║\n" "$TOTAL_SUITES"
printf "║  Passed       : %-43s║\n" "$PASSED_SUITES"
printf "║  Failed       : %-43s║\n" "${#FAILED_SUITES[@]}"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""

if [ "${#FAILED_SUITES[@]}" -gt "0" ]; then
  echo "Failed suites:"
  for suite in "${FAILED_SUITES[@]}"; do
    echo "  ❌  $suite"
  done
  echo ""
  echo "Record each failure at:"
  echo "  tests/issues/YYYY-MM-DD_<desc>.md"
  echo ""
  exit 1
else
  echo "🎉  All bootstrap tests passed. Build is valid."
  echo ""
  exit 0
fi
