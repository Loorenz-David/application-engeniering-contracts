#!/usr/bin/env bash
# =============================================================================
# TEST 01 — Health Check + Auth
# Purpose : Validate that the server is up, DB + Redis are reachable,
#           sign-in works, and logout works.
# Run from: <project>/backend/app/
# Requires: 00_seed_identity.sh already run (.test_token_bootstrap present)
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"
BASE_URL="${APP_URL:-http://localhost:8000}"

echo "════════════════════════════════════════════════════════════"
echo "TEST 01: Health Check + Auth (sign-in / logout)"
echo "════════════════════════════════════════════════════════════"
echo ""

TOKEN=$(cat .test_token_bootstrap 2>/dev/null || echo "")
PASSED=0
FAILED=0

pass() { echo "   ✅ $1"; PASSED=$((PASSED + 1)); return 0; }
fail() { echo "   ❌ $1"; FAILED=$((FAILED + 1)); return 0; }

# ---------------------------------------------------------------------------
# A: Health Check
# ---------------------------------------------------------------------------
echo "A — GET /health"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" "$BASE_URL/health")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

DB_STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('services',{}).get('db',''))" 2>/dev/null)
REDIS_STATUS=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('services',{}).get('redis',''))" 2>/dev/null)

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS (expected 200)"
[ "$DB_STATUS" = "ok" ] && pass "services.db = ok" || fail "services.db = $DB_STATUS"
[ "$REDIS_STATUS" = "ok" ] && pass "services.redis = ok" || fail "services.redis = $REDIS_STATUS"
echo ""

# ---------------------------------------------------------------------------
# B: Sign-In
# ---------------------------------------------------------------------------
echo "B — POST /api/v1/auth/sign-in"
# Payload: email + password + app_scope
SIGNIN=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/auth/sign-in" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "user_test@test.local",
    "password": "Test1234!",
    "app_scope": "admin"
  }')
STATUS=$(echo "$SIGNIN" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$SIGNIN" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"

OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "ok=true" || fail "ok=$OK"

NEW_TOKEN=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('access_token',''))" 2>/dev/null)
[ -n "$NEW_TOKEN" ] && pass "access_token present" || fail "access_token missing"

# Save fresh token for subsequent checks
if [ -n "$NEW_TOKEN" ]; then
  echo "$NEW_TOKEN" > .test_token_bootstrap
  TOKEN="$NEW_TOKEN"
fi
echo ""

# ---------------------------------------------------------------------------
# C: Protected Endpoint (uses token)
# ---------------------------------------------------------------------------
echo "C — GET /api/v1/notifications (protected, confirms token is valid)"
NOTIF=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X GET "$BASE_URL/api/v1/notifications?unread_only=false&limit=1" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$NOTIF" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$NOTIF" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200 (token accepted)" || fail "HTTP $STATUS (token rejected or endpoint error)"
echo ""

# ---------------------------------------------------------------------------
# D: Logout
# ---------------------------------------------------------------------------
echo "D — POST /api/v1/auth/logout"
LOGOUT=$(curl -s -w "\n_STATUS_:%{http_code}" \
  -X POST "$BASE_URL/api/v1/auth/logout" \
  -H "Authorization: Bearer $TOKEN")
STATUS=$(echo "$LOGOUT" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$LOGOUT" | sed '/_STATUS_:/d')

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS"
LOGGED_OUT=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('logged_out',False))" 2>/dev/null)
[ "$LOGGED_OUT" = "True" ] && pass "logged_out=true" || fail "logged_out=$LOGGED_OUT"
echo ""

# ---------------------------------------------------------------------------
# Re-obtain token so downstream tests still work
# ---------------------------------------------------------------------------
FRESH=$(curl -s -X POST "$BASE_URL/api/v1/auth/sign-in" \
  -H "Content-Type: application/json" \
  -d '{"email":"user_test@test.local","password":"Test1234!","app_scope":"admin"}')
FRESH_TOKEN=$(echo "$FRESH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('data',{}).get('access_token',''))" 2>/dev/null)
[ -n "$FRESH_TOKEN" ] && echo "$FRESH_TOKEN" > .test_token_bootstrap

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 01 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_01_health_auth_<desc>.md"
  exit 1
fi
echo ""
exit 0
