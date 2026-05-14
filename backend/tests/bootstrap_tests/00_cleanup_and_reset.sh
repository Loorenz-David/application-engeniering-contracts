#!/bin/bash
set -e

################################################################################
# TEST CLEANUP AND RESET SCRIPT
# 
# Purpose: Clean previous build artifacts, stop/reset Docker containers,
#          drop the test database, and reset test state files.
#
# Run BEFORE: 00_seed_identity.sh
# Exit Code: 0 on success (even if some containers weren't running)
#
# Usage:
#   bash tests/00_cleanup_and_reset.sh
################################################################################

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${APP_DIR:-$(dirname "$(dirname "$TESTS_DIR")")/app}"

echo "================================================================================"
echo "TEST 00: CLEANUP AND RESET"
echo "================================================================================"
echo ""

# ============================================================================
# 1. DROP AND RECREATE DATABASE (while containers are still running)
# ============================================================================

echo "[1/6] Resetting PostgreSQL database..."

# Drop database directly through Postgres container to avoid Python deps.
DB_CONTAINER=""
for candidate in app-postgres-1 test_postgres; do
  if docker ps --quiet --filter "name=^${candidate}$" | grep -q .; then
    DB_CONTAINER="$candidate"
    break
  fi
done

if [ -n "$DB_CONTAINER" ]; then
  if docker exec -i "$DB_CONTAINER" psql -U postgres -d postgres -v ON_ERROR_STOP=1 <<'SQLEOF'
SELECT pg_terminate_backend(pid)
FROM pg_stat_activity
WHERE datname = 'my_app'
AND pid <> pg_backend_pid();

DROP DATABASE IF EXISTS my_app;
SQLEOF
  then
    echo "  ✓ Database dropped via $DB_CONTAINER (bootstrap will recreate via migrations)"
  else
    echo "  ⚠ Database reset skipped (psql command failed)"
  fi
else
  echo "  ⚠ Database reset skipped (no postgres test container running)"
fi

echo ""

# ============================================================================
# 2. STOP AND REMOVE DOCKER CONTAINERS
# ============================================================================

echo "[2/6] Stopping and removing Docker containers..."

# Stop compose stack and remove volumes to prevent stale alembic revisions.
if [ -d "$APP_DIR" ] && [ -f "$APP_DIR/docker-compose.yml" ]; then
  echo "  → Running docker compose down -v in $APP_DIR..."
  (cd "$APP_DIR" && docker compose down -v --remove-orphans) >/dev/null 2>&1 || true
  echo "  ✓ Compose stack cleaned (containers + volumes)"
else
  echo "  ○ docker-compose.yml not found in app dir (skip compose cleanup)"
fi

# Stop and remove postgres container
if docker ps --all --quiet --filter "name=^test_postgres$" | grep -q .; then
  echo "  → Stopping postgres container..."
  docker stop test_postgres 2>/dev/null || true
  echo "  → Removing postgres container..."
  docker rm -f test_postgres 2>/dev/null || true
  echo "  ✓ Postgres container cleaned"
else
  echo "  ○ Postgres container not found (skip)"
fi

# Stop and remove redis container
if docker ps --all --quiet --filter "name=^test_redis$" | grep -q .; then
  echo "  → Stopping redis container..."
  docker stop test_redis 2>/dev/null || true
  echo "  → Removing redis container..."
  docker rm -f test_redis 2>/dev/null || true
  echo "  ✓ Redis container cleaned"
else
  echo "  ○ Redis container not found (skip)"
fi

if docker ps --all --quiet --filter "name=^app-postgres-1$" | grep -q .; then
  echo "  → Removing app-postgres-1 container..."
  docker rm -f app-postgres-1 2>/dev/null || true
fi

if docker ps --all --quiet --filter "name=^app-redis-1$" | grep -q .; then
  echo "  → Removing app-redis-1 container..."
  docker rm -f app-redis-1 2>/dev/null || true
fi

echo ""

# ============================================================================
# 3. REMOVE OLD APP DIRECTORY
# ============================================================================

echo "[3/6] Removing old app directory..."

if [ -d "$APP_DIR" ]; then
  echo "  → Removing $APP_DIR..."
  rm -rf "$APP_DIR"
  echo "  ✓ App directory removed"
else
  echo "  ○ App directory not found (skip)"
fi

echo ""

echo "[4/6] Removing test state files..."

# Remove token file
if [ -f "$APP_DIR/.test_token_bootstrap" ]; then
  rm "$APP_DIR/.test_token_bootstrap"
  echo "  ✓ Removed .test_token_bootstrap"
else
  echo "  ○ .test_token_bootstrap not found"
fi

# Remove S3 credentials file (if present)
if [ -f "$APP_DIR/.env.s3" ]; then
  rm "$APP_DIR/.env.s3"
  echo "  ✓ Removed .env.s3"
else
  echo "  ○ .env.s3 not found"
fi

echo ""

# ============================================================================
# 5. CLEAN UP TEMPORARY TEST FILES
# ============================================================================

echo "[5/6] Cleaning up temporary test files..."

# Remove execution layer test results
if [ -f "/tmp/execution_layer_test_results.json" ]; then
  rm "/tmp/execution_layer_test_results.json"
  echo "  ✓ Removed /tmp/execution_layer_test_results.json"
else
  echo "  ○ /tmp/execution_layer_test_results.json not found"
fi

# Remove scaling test results
if [ -f "/tmp/scaling_performance_test_08_results.json" ]; then
  rm "/tmp/scaling_performance_test_08_results.json"
  echo "  ✓ Removed /tmp/scaling_performance_test_08_results.json"
else
  echo "  ○ /tmp/scaling_performance_test_08_results.json not found"
fi

echo ""

# ============================================================================
# 6. SUMMARY
# ============================================================================

echo "[6/6] Cleanup complete"
echo ""
echo "================================================================================"
echo "CLEANUP SUMMARY"
echo "================================================================================"
echo ""
echo "✓ Docker containers stopped and removed"
echo "✓ App directory removed"
echo "✓ PostgreSQL database dropped (bootstrap recreates via migrations)"
echo "✓ Test state files removed"
echo "✓ Temporary test files cleaned"
echo ""
echo "Next step: Run 01_seed_identity.sh to initialize test data"
echo ""

exit 0
