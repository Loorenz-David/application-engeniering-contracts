#!/usr/bin/env bash
set -eo pipefail

################################################################################
# bootstrap_app.sh
#
# Interactive one-shot bootstrap for a new application built from
# application_contracts. Run this once after pulling the repo to create a
# fully wired backend app with tests, ready to run immediately.
#
# Usage:
#   bash application_contracts/backend/bootstrap_app.sh
#
# What it does:
#   1. Asks where to create the app and what to name it
#   2. Bootstraps the backend umbrella structure (architecture, docs, skills)
#   3. Generates the FastAPI app from canonical contract phases
#   4. Creates a Python venv and installs all dependencies
#   5. Writes .env from .env.example and injects APP_NAME
#   6. Starts Docker services (Postgres + Redis)
#   7. Runs database migrations and applies triggers
#   8. Copies the canonical test suite to the project
#   9. Verifies app health
#
# After completion:
#   cd <project>/backend/tests/bootstrap_tests
#   bash run_all.sh
################################################################################

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTRACTS_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
TASK_SYSTEM_DIR="$CONTRACTS_ROOT/backend/task_system"
CANONICAL_TESTS_DIR="$CONTRACTS_ROOT/backend/tests"

echo ""
echo "╔═══════════════════════════════════════════════════════════╗"
echo "║          APPLICATION CONTRACTS — APP BOOTSTRAP            ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "Contracts root : $CONTRACTS_ROOT"
echo ""

# ============================================================================
# INTERACTIVE CONFIGURATION
# ============================================================================

# --- Output directory ---
if [ -n "${BOOTSTRAP_OUTPUT_DIR:-}" ]; then
  OUTPUT_DIR="$BOOTSTRAP_OUTPUT_DIR"
else
  echo "Where should the app be created?"
  echo "  Enter an absolute path (e.g. /Users/you/projects/my-startup)."
  echo "  The directory will be created if it does not exist."
  echo ""
  printf "Output directory: "
  read -r OUTPUT_DIR
  OUTPUT_DIR="${OUTPUT_DIR/#\~/$HOME}"   # expand leading ~
fi

if [ -z "$OUTPUT_DIR" ]; then
  echo "✗ No output directory provided."
  exit 1
fi

OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR" 2>/dev/null || echo "$OUTPUT_DIR")"
echo ""

# --- App name ---
if [ -n "${BOOTSTRAP_APP_NAME:-}" ]; then
  APP_DISPLAY_NAME="$BOOTSTRAP_APP_NAME"
else
  echo "App name (display name — stored in .env as APP_NAME)."
  echo "  This is what your app calls itself in logs, headers, and responses."
  echo "  Examples: 'My Startup', 'Delivery Platform', 'Marketplace API'"
  echo ""
  printf "App name: "
  read -r APP_DISPLAY_NAME
fi

if [ -z "$APP_DISPLAY_NAME" ]; then
  echo "✗ No app name provided."
  exit 1
fi

echo ""
echo "────────────────────────────────────────────────────────────"
echo "  Output  : $OUTPUT_DIR"
echo "  App name: $APP_DISPLAY_NAME"
echo "────────────────────────────────────────────────────────────"
echo ""

if [ -z "${BOOTSTRAP_SKIP_CONFIRM:-}" ]; then
  printf "Proceed? (y/N): "
  read -r CONFIRM
  if [ "$CONFIRM" != "y" ] && [ "$CONFIRM" != "Y" ]; then
    echo "Aborted."
    exit 0
  fi
  echo ""
fi

# ============================================================================
# DERIVED PATHS
# ============================================================================

BACKEND_DIR="$OUTPUT_DIR/backend"
APP_DIR="$BACKEND_DIR/app"
TESTS_DEST_DIR="$BACKEND_DIR/tests"

# ============================================================================
# 1. BOOTSTRAP BACKEND UMBRELLA STRUCTURE
# ============================================================================

echo "[1/9] Bootstrapping backend umbrella structure..."
mkdir -p "$OUTPUT_DIR"
cd "$TASK_SYSTEM_DIR"
if python3 run/bootstrap_backend_system.py \
  --output-dir "$OUTPUT_DIR" \
  --sync-all \
  --preserve-local; then
  echo "  ✓ Backend umbrella structure created"
else
  echo "  ✗ Backend umbrella bootstrap failed"
  exit 1
fi
echo ""

# ============================================================================
# 2. VERIFY CANONICAL CONTRACTS EXIST
# ============================================================================

echo "[2/9] Verifying canonical contracts..."
if [ ! -f "$TASK_SYSTEM_DIR/run/bootstrap.py" ]; then
  echo "  ✗ bootstrap.py not found at: $TASK_SYSTEM_DIR/run/bootstrap.py"
  exit 1
fi
echo "  ✓ Canonical contracts verified"
echo ""

# ============================================================================
# 3. BOOTSTRAP APP PHASES
# ============================================================================

echo "[3/9] Generating app from contract phases..."
if [ -f "$APP_DIR/app.py" ] || [ -f "$APP_DIR/main.py" ]; then
  echo "  ○ App already bootstrapped (app.py / main.py exists)"
else
  echo "  → Running bootstrap.py (app-name: my_app)..."
  cd "$TASK_SYSTEM_DIR"
  python3 run/bootstrap.py \
    --app-name my_app \
    --target "$APP_DIR" \
    --phase all \
    --force
  echo "  ✓ App phases generated"
fi
echo ""

# ============================================================================
# 4. PYTHON VENV
# ============================================================================

echo "[4/9] Setting up Python environment..."
REQUIRED_PY_MINOR="3.12"
if [ -f "$APP_DIR/.python-version" ]; then
  REQUIRED_PY_MINOR="$(awk -F. 'NR==1{print $1"."$2}' "$APP_DIR/.python-version")"
fi

PYTHON_CMD=""
for cmd in "python${REQUIRED_PY_MINOR}" python3.13 python3.12 python3.11 python3.10; do
  if command -v "$cmd" >/dev/null 2>&1; then
    if "$cmd" -c 'import pyexpat' >/dev/null 2>&1 && "$cmd" -m ensurepip --version >/dev/null 2>&1; then
      PYTHON_CMD="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON_CMD" ]; then
  echo "  ✗ No usable Python found (tried ${REQUIRED_PY_MINOR}, 3.13, 3.12, 3.11, 3.10)."
  echo "    Install/repair Python and rerun bootstrap_app.sh."
  exit 1
fi

SELECTED_PY_MINOR="$($PYTHON_CMD -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "  → Interpreter: $PYTHON_CMD (Python $SELECTED_PY_MINOR)"
if [ "$SELECTED_PY_MINOR" != "$REQUIRED_PY_MINOR" ]; then
  echo "  ⚠ Required $REQUIRED_PY_MINOR — using compatible fallback $SELECTED_PY_MINOR"
fi

if [ -d "$APP_DIR/.venv" ]; then
  VENV_PY_MINOR="$("$APP_DIR/.venv/bin/python" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || echo unknown)"
  if [ "$VENV_PY_MINOR" != "$SELECTED_PY_MINOR" ]; then
    echo "  ○ Existing venv uses Python $VENV_PY_MINOR — recreating..."
    rm -rf "$APP_DIR/.venv"
  else
    echo "  ○ venv already exists (Python $VENV_PY_MINOR)"
  fi
fi

if [ ! -d "$APP_DIR/.venv" ]; then
  echo "  → Creating venv..."
  "$PYTHON_CMD" -m venv "$APP_DIR/.venv"
  echo "  ✓ venv created"
fi

source "$APP_DIR/.venv/bin/activate"
echo "  ✓ venv activated ($(python -c 'import sys; print(sys.version.split()[0])'))"
echo ""

# ============================================================================
# 5. INSTALL DEPENDENCIES
# ============================================================================

echo "[5/9] Installing dependencies..."
cd "$APP_DIR"
if [ -f "requirements.txt" ]; then
  echo "  → Installing requirements.txt..."
  python -m pip install --progress-bar on -r requirements.txt
  echo "  ✓ requirements.txt installed"
else
  echo "  ⚠ requirements.txt not found"
fi
if [ -f "requirements-dev.txt" ]; then
  echo "  → Installing requirements-dev.txt..."
  python -m pip install --progress-bar on -r requirements-dev.txt
  echo "  ✓ requirements-dev.txt installed"
fi
echo ""

# ============================================================================
# 6. CONFIGURE .env
# ============================================================================

echo "[6/9] Configuring environment..."
if [ ! -f "$APP_DIR/.env" ]; then
  if [ -f "$APP_DIR/.env.example" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "  ✓ .env created from .env.example"
  else
    echo "  ⚠ .env.example not found — creating minimal .env"
    touch "$APP_DIR/.env"
  fi
else
  echo "  ○ .env already exists"
fi

# Inject or update APP_NAME
if grep -q "^APP_NAME=" "$APP_DIR/.env" 2>/dev/null; then
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sed -i '' "s|^APP_NAME=.*|APP_NAME=$APP_DISPLAY_NAME|" "$APP_DIR/.env"
  else
    sed -i "s|^APP_NAME=.*|APP_NAME=$APP_DISPLAY_NAME|" "$APP_DIR/.env"
  fi
  echo "  ✓ APP_NAME updated to: $APP_DISPLAY_NAME"
else
  echo "APP_NAME=$APP_DISPLAY_NAME" >> "$APP_DIR/.env"
  echo "  ✓ APP_NAME=$APP_DISPLAY_NAME written to .env"
fi
echo ""

# ============================================================================
# 7. START DOCKER SERVICES + DATABASE SETUP
# ============================================================================

echo "[7/9] Starting Docker services..."
cd "$APP_DIR"
if command -v make &>/dev/null && [ -f "Makefile" ]; then
  echo "  → Running: make dev-up..."
  make dev-up
  echo "  ✓ Docker services started"

  echo "  → Waiting for PostgreSQL to be ready..."
  sleep 5
  max_attempts=30
  attempt=0
  while ! make db-init >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge "$max_attempts" ]; then
      echo "  ✗ PostgreSQL failed to become ready after ${max_attempts}s"
      exit 1
    fi
    sleep 1
  done
  echo "  ✓ PostgreSQL ready"
else
  echo "  ⚠ Makefile not found — start Docker manually, then rerun"
  exit 1
fi
echo ""

# ============================================================================
# 8. MIGRATIONS AND TRIGGERS
# ============================================================================

echo "[8/9] Running migrations and triggers..."
cd "$APP_DIR"
export APP_ENV=development
if command -v make &>/dev/null && [ -f "Makefile" ]; then
  make db-migrate
  echo "  ✓ Migrations complete"
  make db-triggers
  echo "  ✓ Triggers applied"
else
  if command -v alembic &>/dev/null; then
    alembic upgrade head
    PYTHONPATH=. python scripts/apply_db_triggers.py
    echo "  ✓ Migrations and triggers complete"
  else
    echo "  ⚠ alembic not available — run migrations manually"
  fi
fi
echo ""

# ============================================================================
# 9. COPY TEST SUITE + VERIFY HEALTH
# ============================================================================

echo "[9/9] Installing test suite and verifying app..."

# Copy canonical tests into project
if [ -d "$CANONICAL_TESTS_DIR" ]; then
  mkdir -p "$TESTS_DEST_DIR"
  cp -r "$CANONICAL_TESTS_DIR/bootstrap_tests" "$TESTS_DEST_DIR/"
  mkdir -p "$TESTS_DEST_DIR/test_summary"
  echo "  ✓ Test suite installed at $TESTS_DEST_DIR"
else
  echo "  ⚠ Canonical tests not found at $CANONICAL_TESTS_DIR — skipping"
fi

# Health check
echo "  → Starting app to verify health..."
cd "$APP_DIR"
if command -v make &>/dev/null && [ -f "Makefile" ]; then
  make run >/tmp/bootstrap_app_health.log 2>&1 &
else
  python run.py >/tmp/bootstrap_app_health.log 2>&1 &
fi
HEALTH_PID=$!

max_attempts=30
attempt=0
while ! curl -s http://localhost:8000/health >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge "$max_attempts" ]; then
    echo "  ✗ App failed to start — check /tmp/bootstrap_app_health.log"
    tail -20 /tmp/bootstrap_app_health.log
    kill "$HEALTH_PID" 2>/dev/null || true
    exit 1
  fi
  sleep 1
done

HEALTH=$(curl -s http://localhost:8000/health)
if echo "$HEALTH" | grep -q '"status":"ok"'; then
  echo "  ✓ App is healthy: $HEALTH"
else
  echo "  ⚠ Unexpected health response: $HEALTH"
fi

kill "$HEALTH_PID" 2>/dev/null || true
wait "$HEALTH_PID" 2>/dev/null || true
echo ""

# ============================================================================
# DONE
# ============================================================================

echo "╔═══════════════════════════════════════════════════════════╗"
echo "║  BOOTSTRAP COMPLETE                                       ║"
echo "╚═══════════════════════════════════════════════════════════╝"
echo ""
echo "  Project    : $OUTPUT_DIR"
echo "  App name   : $APP_DISPLAY_NAME"
echo "  App module : my_app  (always the Python package name)"
echo "  App dir    : $APP_DIR"
echo "  Tests dir  : $TESTS_DEST_DIR/bootstrap_tests"
echo ""
echo "Run the full test suite:"
echo "  cd $TESTS_DEST_DIR/bootstrap_tests"
echo "  bash run_all.sh"
echo ""
echo "Start the app for development:"
echo "  cd $APP_DIR"
echo "  make run          # API server"
echo "  make task-router  # background task router"
echo ""

exit 0
