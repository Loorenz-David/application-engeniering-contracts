#!/usr/bin/env bash
# =============================================================================
# TEST 00 — Seed Identity
# Purpose : Inject the canonical test identity (user + workspace + role +
#           membership) into the database. Idempotent — safe to re-run.
# Run from: <project>/backend/app/
# Requires: .venv active, APP_ENV=development, postgres running
# =============================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"
BASE_URL="${APP_URL:-http://localhost:8000}"

echo "════════════════════════════════════════════════════════════"
echo "TEST 00: Seed Identity (user / workspace / role / membership)"
echo "════════════════════════════════════════════════════════════"
echo ""

# ---------------------------------------------------------------------------
# Helper: run SQL via Python + SQLAlchemy (same DB URL the app uses)
# ---------------------------------------------------------------------------
run_sql() {
  local sql="$1"
  python3 - <<PYEOF
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from my_app.config import settings

SQL = """$sql"""

async def main():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        await conn.execute(text(SQL))
    await engine.dispose()

asyncio.run(main())
PYEOF
}

query_scalar() {
  local sql="$1"
  python3 - <<PYEOF
import asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from my_app.config import settings

SQL = """$sql"""

async def main():
    engine = create_async_engine(settings.database_url)
    async with engine.begin() as conn:
        res = await conn.execute(text(SQL))
        row = res.first()
        print(row[0] if row else "")
    await engine.dispose()

asyncio.run(main())
PYEOF
}

# ---------------------------------------------------------------------------
# Step 1: generate bcrypt hash for Test1234!
# ---------------------------------------------------------------------------
echo "Step 1: Generate bcrypt hash for password Test1234!"
PW_HASH=$(python3 -c "
import bcrypt
print(bcrypt.hashpw('Test1234!'.encode(), bcrypt.gensalt()).decode())
")
echo "   Hash generated (truncated): ${PW_HASH:0:30}..."
echo ""

# ---------------------------------------------------------------------------
# Step 2: Seed role, workspace, workspace_role, user, membership
# ---------------------------------------------------------------------------
echo "Step 2: Seed identity records (idempotent)"

run_sql "
INSERT INTO roles (client_id, name)
SELECT 'role_workspace_test_admin', 'ADMIN'
WHERE NOT EXISTS (
  SELECT 1 FROM roles WHERE client_id = 'role_workspace_test_admin' OR name = 'ADMIN'
);
"

run_sql "
INSERT INTO workspaces (client_id, name, time_zone, created_at)
SELECT 'ws_workspace_test', 'workspace_test', 'UTC', NOW()
WHERE NOT EXISTS (
  SELECT 1 FROM workspaces WHERE client_id = 'ws_workspace_test' OR name = 'workspace_test'
);
"

run_sql "
INSERT INTO workspace_roles (client_id, name, workspace_id, role_id, is_system)
VALUES (
  'wsr_workspace_test_admin',
  'admin',
  (
    SELECT client_id
    FROM workspaces
    WHERE client_id = 'ws_workspace_test' OR name = 'workspace_test'
    ORDER BY CASE WHEN client_id = 'ws_workspace_test' THEN 0 ELSE 1 END
    LIMIT 1
  ),
  (
    SELECT client_id
    FROM roles
    WHERE client_id = 'role_workspace_test_admin' OR name = 'ADMIN'
    ORDER BY CASE WHEN client_id = 'role_workspace_test_admin' THEN 0 ELSE 1 END
    LIMIT 1
  ),
  false
)
ON CONFLICT (client_id) DO UPDATE
  SET name = EXCLUDED.name,
      workspace_id = EXCLUDED.workspace_id,
      role_id = EXCLUDED.role_id,
      is_system = EXCLUDED.is_system;
"

run_sql "
INSERT INTO users (client_id, username, email, password, created_at, online)
VALUES ('usr_user_test', 'user_test', 'user_test@test.local', '$PW_HASH', NOW(), false)
ON CONFLICT (email) DO UPDATE
  SET client_id = EXCLUDED.client_id,
      username = EXCLUDED.username,
      password = EXCLUDED.password;
"

run_sql "
INSERT INTO workspace_memberships (client_id, user_id, workspace_id, workspace_role_id, is_active, joined_at)
VALUES (
  'wsm_user_test',
  (SELECT client_id FROM users WHERE client_id = 'usr_user_test' LIMIT 1),
  (
    SELECT client_id
    FROM workspaces
    WHERE client_id = 'ws_workspace_test' OR name = 'workspace_test'
    ORDER BY CASE WHEN client_id = 'ws_workspace_test' THEN 0 ELSE 1 END
    LIMIT 1
  ),
  (SELECT client_id FROM workspace_roles WHERE client_id = 'wsr_workspace_test_admin' LIMIT 1),
  true,
  NOW()
)
ON CONFLICT (client_id) DO UPDATE
  SET user_id = EXCLUDED.user_id,
      workspace_id = EXCLUDED.workspace_id,
      workspace_role_id = EXCLUDED.workspace_role_id,
      is_active = EXCLUDED.is_active,
      joined_at = EXCLUDED.joined_at;
"

echo "   ✅ Identity records seeded"
echo ""

# ---------------------------------------------------------------------------
# Step 3: Verify
# ---------------------------------------------------------------------------
echo "Step 3: Verify identity join"

RESULT=$(query_scalar "
SELECT u.username || ' | ' || ws.name || ' | ' || r.name || ' | ' || CAST(wsm.is_active AS TEXT)
FROM users u
JOIN workspace_memberships wsm ON wsm.user_id = u.client_id
JOIN workspaces ws ON ws.client_id = wsm.workspace_id
JOIN workspace_roles wr ON wr.client_id = wsm.workspace_role_id
JOIN roles r ON r.client_id = wr.role_id
WHERE u.client_id = 'usr_user_test'
LIMIT 1
")

if [ -n "$RESULT" ]; then
  echo "   ✅ Identity verified: $RESULT"
else
  echo "   ❌ Verification failed — no row returned"
  exit 1
fi
echo ""

# ---------------------------------------------------------------------------
# Step 4: Seed required lookup data (case_types)
# ---------------------------------------------------------------------------
echo "Step 4: Seed case_types lookup"

run_sql "
INSERT INTO case_types (client_id, name, entity_type)
VALUES ('ct_investigation', 'Investigation', 'TASK')
ON CONFLICT (client_id) DO UPDATE SET name = EXCLUDED.name;
" 2>/dev/null || echo "   ⚠️  case_types seed skipped (table may not exist yet — run after migrations)"

run_sql "
INSERT INTO case_types (client_id, name, entity_type)
VALUES ('ct_report', 'Report', 'TASK')
ON CONFLICT (client_id) DO UPDATE SET name = EXCLUDED.name;
" 2>/dev/null || true

echo ""

# ---------------------------------------------------------------------------
# Step 5: Obtain and persist auth token
# ---------------------------------------------------------------------------
echo "Step 5: Obtain auth token via sign-in"

SIGNIN=$(curl -s --connect-timeout 3 --max-time 10 -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/auth/sign-in" \
  -H "Content-Type: application/json" \
  -d '{"email":"user_test@test.local","password":"Test1234!","app_scope":"admin"}')

STATUS=$(echo "$SIGNIN" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$SIGNIN" | sed '/_STATUS_:/d')

if [ "$STATUS" = "200" ]; then
  TOKEN=$(echo "$BODY" | python3 -c "import sys, json; d=json.load(sys.stdin); print(d['data']['access_token'])" 2>/dev/null)
  if [ -n "$TOKEN" ]; then
    echo "$TOKEN" > .test_token_bootstrap
    echo "   ✅ Token obtained and saved to .test_token_bootstrap"
  else
    echo "   ❌ Token field missing in response"
    exit 1
  fi
else
  echo "   ❌ Sign-in failed (HTTP $STATUS)"
  echo "   Response: $BODY"
  exit 1
fi

echo ""
echo "════════════════════════════════════════════════════════════"
echo "TEST 00 RESULT: ✅ PASS — Identity seeded and token saved"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Identity Summary:"
echo "  user_client_id       : usr_user_test"
echo "  email                : user_test@test.local"
echo "  workspace_client_id  : ws_workspace_test"
echo "  role                 : ADMIN"
echo "  workspace_role       : admin"
echo "  token saved to       : .test_token_bootstrap"
echo ""
