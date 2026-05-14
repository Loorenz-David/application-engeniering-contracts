#!/usr/bin/env bash
# =============================================================================
# TEST 03 — VAPID Public Key Endpoint
# Purpose : Validate the public, unauthenticated push notification key endpoint.
# Run from: <project>/backend/app/
# Requires: App running on localhost:8000
# Notes   : This endpoint needs NO auth token. public_key=null is valid when
#           VAPID keys are not yet configured in .env.
# =============================================================================
set -uo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "$0")/../../app" && pwd)}"
cd "$APP_DIR"
BASE_URL="${APP_URL:-http://localhost:8000}"

echo "════════════════════════════════════════════════════════════"
echo "TEST 03: VAPID Public Key Endpoint"
echo "════════════════════════════════════════════════════════════"
echo ""
echo "Endpoint : GET /api/v1/notifications/vapid-public-key"
echo "Auth     : None (public)"
echo ""

PASSED=0
FAILED=0

pass() { echo "   ✅ $1"; PASSED=$((PASSED + 1)); return 0; }
fail() { echo "   ❌ $1"; FAILED=$((FAILED + 1)); return 0; }

# ---------------------------------------------------------------------------
# A: Request (no auth header)
# ---------------------------------------------------------------------------
echo "A — GET /api/v1/notifications/vapid-public-key (no auth)"
RESP=$(curl -s -w "\n_STATUS_:%{http_code}" \
  "$BASE_URL/api/v1/notifications/vapid-public-key")
STATUS=$(echo "$RESP" | grep "_STATUS_:" | cut -d':' -f2)
BODY=$(echo "$RESP" | sed '/_STATUS_:/d')

echo "   Response: $BODY"
echo ""

[ "$STATUS" = "200" ] && pass "HTTP 200" || fail "HTTP $STATUS (expected 200)"

OK=$(echo "$BODY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('ok',False))" 2>/dev/null)
[ "$OK" = "True" ] && pass "ok=true" || fail "ok=$OK"

HAS_DATA=$(echo "$BODY" | python3 -c "import sys,json; print('data' in json.load(sys.stdin))" 2>/dev/null)
[ "$HAS_DATA" = "True" ] && pass "data field present" || fail "data field missing"

HAS_KEY=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print('public_key' in d.get('data',{}))" 2>/dev/null)
[ "$HAS_KEY" = "True" ] && pass "data.public_key field present" || fail "data.public_key field missing"

PK=$(echo "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); pk=d.get('data',{}).get('public_key'); print('null' if pk is None else pk)" 2>/dev/null)
pass "data.public_key = $PK (null=not configured; key string=configured)"
echo ""

# ---------------------------------------------------------------------------
# B: Confirm endpoint is accessible without Authorization header (no 401/403)
# ---------------------------------------------------------------------------
echo "B — Confirm no 401/403 when called without auth"
NO_AUTH_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
  "$BASE_URL/api/v1/notifications/vapid-public-key")
[ "$NO_AUTH_STATUS" != "401" ] && [ "$NO_AUTH_STATUS" != "403" ] \
  && pass "No auth required (HTTP $NO_AUTH_STATUS)" \
  || fail "Endpoint rejected unauthenticated request ($NO_AUTH_STATUS) — should be public"
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "════════════════════════════════════════════════════════════"
echo "TEST 03 RESULT: $PASSED Passed, $FAILED Failed"
echo "════════════════════════════════════════════════════════════"
if [ "$FAILED" -gt "0" ]; then
  echo ""
  echo "⚠️  Record failures in:"
  echo "   tests/issues/YYYY-MM-DD_03_vapid_<desc>.md"
  exit 1
fi
echo ""
exit 0
